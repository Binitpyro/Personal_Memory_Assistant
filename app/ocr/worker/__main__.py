"""OCR worker entry point. Runs inside `<ocr_env>`, never in the main process.

MUST NOT IMPORT `app.*`. This file is copied into `<ocr_env>/worker/` and run
by path, so `app` is not importable there anyway - but the rule is enforced by
`tests/test_ocr_protocol.py` rather than left to discipline.

Contract:
  * stdin  - one JSON protocol message per line.
  * stdout - protocol messages ONLY. Anything else corrupts the stream.
  * stderr - all logging and tracebacks. Drained by the manager.
  * page text goes to the NDJSON file named in the `doc` message, never stdout.

Exit codes are defined in protocol.py so the manager can tell a protocol
mismatch from an OOM without parsing anything.
"""

import contextlib
import json
import os
import queue
import sys
import threading
import time
import traceback

# Run-by-path puts this directory on sys.path[0], so protocol.py sits beside us.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import protocol


def _emit(msg):
    """Write one protocol message to stdout, flushed immediately."""
    sys.stdout.write(protocol.encode(msg))
    sys.stdout.flush()


def _log(message):
    sys.stderr.write(message.rstrip() + "\n")
    sys.stderr.flush()


class Worker:
    def __init__(self):
        self.engine = None
        self.dpi = 300
        self.conf_floor = 0.30
        self.page_timeout_s = 30
        self.models_dir = ""
        self.model_version = "unknown"
        self.ep = "CPUExecutionProvider"
        self._cancelled = set()

    # ── hello ────────────────────────────────────────────────────────────
    def handle_hello(self, msg):
        if msg.get("protocol") != protocol.PROTOCOL_VERSION:
            _emit(
                protocol.make_error(
                    code=protocol.E_PROTOCOL_MISMATCH,
                    detail=(
                        f"worker protocol {protocol.PROTOCOL_VERSION}, "
                        f"manager sent {msg.get('protocol')!r}"
                    ),
                )
            )
            sys.exit(protocol.EXIT_PROTOCOL_MISMATCH)

        self.models_dir = msg.get("models_dir") or ""
        self.dpi = int(msg.get("dpi") or 300)
        self.conf_floor = float(msg.get("conf_floor") or 0.30)
        self.page_timeout_s = int(msg.get("page_timeout_s") or 30)

        try:
            from engine import Engine

            self.engine = Engine(self.models_dir, self.conf_floor)
            self.model_version = self.engine.model_version
            self.ep = self.engine.execution_provider
        except Exception as exc:
            _log("Engine init failed:\n" + traceback.format_exc())
            _emit(protocol.make_error(code=protocol.E_MODEL_LOAD_FAILED, detail=str(exc)))
            sys.exit(protocol.EXIT_CRASHED)

        _emit(protocol.make_ready(model_version=self.model_version, ep=self.ep))

    # ── doc ──────────────────────────────────────────────────────────────
    def handle_doc(self, msg):
        import raster

        doc_id = msg.get("doc_id") or ""
        path = msg.get("path") or ""
        pages = [int(p) for p in (msg.get("pages") or [])]
        ndjson_path = msg.get("ndjson") or ""
        dpi = int(msg.get("dpi") or self.dpi)

        pages_ok = 0
        pages_failed = 0
        conf_total = 0.0

        # Bounded 2-slot prefetch queue: the background thread rasterizes page N+1
        # while the main thread recognizes page N. Bounded to 2 images so peak
        # memory stays strictly O(1) (~17 MB extra at 300 DPI).
        page_queue: queue.Queue = queue.Queue(maxsize=2)
        stop_event = threading.Event()

        def _raster_worker():
            doc = None
            try:
                try:
                    # Opened exclusively inside the raster thread; PDFium handles
                    # are never shared across threads.
                    doc = raster.open_document(path)
                except Exception as exc:
                    _log(f"Failed to open {path}: {exc}")
                    page_queue.put((-1, str(exc), "OPEN_FAILED"))
                    return

                for p_num in pages:
                    if stop_event.is_set() or doc_id in self._cancelled:
                        break
                    try:
                        img = raster.render_page(doc, p_num, dpi)
                        while not stop_event.is_set() and doc_id not in self._cancelled:
                            try:
                                page_queue.put((p_num, img, None), timeout=0.2)
                                break
                            except queue.Full:
                                continue
                    except MemoryError:
                        page_queue.put((p_num, "MemoryError", "OOM"))
                        break
                    except raster.RasterError as r_exc:
                        page_queue.put((p_num, str(r_exc), "RASTER_FAILED"))
                    except Exception as g_exc:
                        page_queue.put((p_num, str(g_exc), "RASTER_ERROR"))
            finally:
                with contextlib.suppress(Exception):
                    raster.close_document(doc)
                with contextlib.suppress(Exception):
                    page_queue.put(None)

        raster_thread = threading.Thread(
            target=_raster_worker, name=f"ocr-raster-{doc_id}", daemon=True
        )
        raster_thread.start()

        try:
            with open(ndjson_path, "a", encoding="utf-8") as sink:
                page_idx = 0
                while True:
                    if doc_id in self._cancelled:
                        stop_event.set()
                        break

                    try:
                        item = page_queue.get(timeout=0.5)
                    except queue.Empty:
                        if not raster_thread.is_alive() and page_queue.empty():
                            break
                        continue

                    if item is None:
                        break

                    page_num, payload, err_type = item

                    if err_type == "OPEN_FAILED":
                        _emit(
                            protocol.make_error(
                                code=protocol.E_RASTER_FAILED, detail=payload, doc_id=doc_id
                            )
                        )
                        _emit(
                            protocol.make_doc_done(
                                doc_id=doc_id, pages_ok=0, pages_failed=len(pages), mean_conf=0.0
                            )
                        )
                        return

                    if err_type == "OOM":
                        _log(f"Out of memory on {path}")
                        _emit(
                            protocol.make_error(code=protocol.E_OCR_OOM, detail=path, doc_id=doc_id)
                        )
                        sys.exit(protocol.EXIT_OOM)

                    started = time.time()
                    if err_type is not None:
                        # Page raster failed
                        record = {
                            "page": page_num,
                            "lines": [],
                            "mean_conf": 0.0,
                            "error": protocol.E_RASTER_FAILED,
                            "detail": str(payload)[:200],
                        }
                    else:
                        # Valid raster image, run recognition with watchdog
                        record = self._recognize_with_watchdog(page_num, payload)

                    elapsed_ms = int((time.time() - started) * 1000)
                    record["ms"] = elapsed_ms

                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    sink.flush()
                    if page_idx % 25 == 24:
                        os.fsync(sink.fileno())
                    page_idx += 1

                    if record.get("error"):
                        pages_failed += 1
                    else:
                        pages_ok += 1
                        conf_total += record.get("mean_conf") or 0.0

                    _emit(
                        protocol.make_page(
                            doc_id=doc_id,
                            page=page_num,
                            ok=not record.get("error"),
                            ms=elapsed_ms,
                        )
                    )
        except MemoryError:
            _log(f"Out of memory on {path}")
            _emit(protocol.make_error(code=protocol.E_OCR_OOM, detail=path, doc_id=doc_id))
            sys.exit(protocol.EXIT_OOM)
        finally:
            stop_event.set()
            while not page_queue.empty():
                with contextlib.suppress(Exception):
                    page_queue.get_nowait()
            raster_thread.join(timeout=3.0)
            if raster_thread.is_alive():
                _log(f"Warning: raster thread for {doc_id} did not terminate within timeout")
            self._cancelled.discard(doc_id)

        mean_conf = (conf_total / pages_ok) if pages_ok else 0.0
        _emit(
            protocol.make_doc_done(
                doc_id=doc_id,
                pages_ok=pages_ok,
                pages_failed=pages_failed,
                mean_conf=mean_conf,
            )
        )

    def _recognize_with_watchdog(self, page_num, image):
        """Recognize one page image with an interruptible watchdog timer. Never raises."""
        timer = None
        disarmed = False
        try:

            def _fire():
                nonlocal disarmed
                if disarmed:
                    return
                _log(f"Page {page_num} exceeded {self.page_timeout_s}s")
                if hasattr(_thread_interrupt, "interrupt_main"):
                    _thread_interrupt.interrupt_main()

            import _thread as _thread_interrupt

            timer = threading.Timer(self.page_timeout_s, _fire)
            timer.daemon = True
            timer.start()

            lines, mean_conf = self.engine.recognize(image)
            return {"page": page_num, "lines": lines, "mean_conf": mean_conf}

        except KeyboardInterrupt:
            return {
                "page": page_num,
                "lines": [],
                "mean_conf": 0.0,
                "error": protocol.E_OCR_PAGE_TIMEOUT,
            }
        except MemoryError:
            raise
        except Exception as exc:
            _log(f"Page {page_num} failed:\n{traceback.format_exc()}")
            return {
                "page": page_num,
                "lines": [],
                "mean_conf": 0.0,
                "error": protocol.E_RASTER_FAILED,
                "detail": str(exc)[:200],
            }
        finally:
            disarmed = True
            if timer is not None:
                timer.cancel()

    def _do_page(self, raster, doc, page_num, dpi):
        """Render and recognize one page synchronously. Preserved for direct invocation."""
        try:
            image = raster.render_page(doc, page_num, dpi)
            return self._recognize_with_watchdog(page_num, image)
        except MemoryError:
            raise
        except raster.RasterError as exc:
            _log(f"Raster failed on page {page_num}: {exc}")
            return {
                "page": page_num,
                "lines": [],
                "mean_conf": 0.0,
                "error": protocol.E_RASTER_FAILED,
            }
        except Exception as exc:
            _log(f"Page {page_num} failed:\n{traceback.format_exc()}")
            return {
                "page": page_num,
                "lines": [],
                "mean_conf": 0.0,
                "error": protocol.E_RASTER_FAILED,
                "detail": str(exc)[:200],
            }

    # ── loop ─────────────────────────────────────────────────────────────
    def run(self):
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
            except KeyboardInterrupt:
                # Stray interrupt after page watchdog timeout; continue message loop safely
                continue
            except Exception:
                break

            try:
                msg = protocol.decode(line)
            except protocol.ProtocolError:
                continue

            try:
                kind = msg.get("t")
                if kind == protocol.REQ_HELLO:
                    self.handle_hello(msg)
                elif kind == protocol.REQ_DOC:
                    self.handle_doc(msg)
                elif kind == protocol.REQ_CANCEL:
                    self._cancelled.add(msg.get("doc_id") or "")
                elif kind == protocol.REQ_SHUTDOWN:
                    return protocol.EXIT_OK
            except KeyboardInterrupt:
                continue
        return protocol.EXIT_OK


def main():
    try:
        return Worker().run()
    except SystemExit:
        raise
    except BaseException as exc:
        _log("Worker crashed:\n" + traceback.format_exc())
        with contextlib.suppress(Exception):
            _emit(protocol.make_error(code=protocol.E_WORKER_CRASHED, detail=str(exc)[:500]))
        return protocol.EXIT_CRASHED


if __name__ == "__main__":
    sys.exit(main())
