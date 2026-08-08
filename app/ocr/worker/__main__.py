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
        doc = None

        try:
            # Opened once for the whole document: PDFium is not thread-safe,
            # and reopening per page dominates the cost on large scans.
            doc = raster.open_document(path)
        except Exception as exc:
            _log(f"Failed to open {path}: {exc}")
            _emit(
                protocol.make_error(
                    code=protocol.E_RASTER_FAILED, detail=str(exc), doc_id=doc_id
                )
            )
            _emit(
                protocol.make_doc_done(
                    doc_id=doc_id, pages_ok=0, pages_failed=len(pages), mean_conf=0.0
                )
            )
            return

        try:
            with open(ndjson_path, "a", encoding="utf-8") as sink:
                for i, page_num in enumerate(pages):
                    if doc_id in self._cancelled:
                        break

                    started = time.time()
                    record = self._do_page(raster, doc, page_num, dpi)
                    elapsed_ms = int((time.time() - started) * 1000)
                    record["ms"] = elapsed_ms

                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    # Flush every page so a kill loses at most the current one.
                    # fsync is far more expensive and only guards power loss,
                    # which costs us a re-OCR rather than corruption.
                    sink.flush()
                    if i % 25 == 24:
                        os.fsync(sink.fileno())

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
            self._cancelled.discard(doc_id)
            with contextlib.suppress(Exception):
                raster.close_document(doc)

        mean_conf = (conf_total / pages_ok) if pages_ok else 0.0
        _emit(
            protocol.make_doc_done(
                doc_id=doc_id,
                pages_ok=pages_ok,
                pages_failed=pages_failed,
                mean_conf=mean_conf,
            )
        )

    def _do_page(self, raster, doc, page_num, dpi):
        """Render and recognize one page. Never raises - returns an error record.

        A single bad page must not fail the document; that guarantee is what
        makes partial results worth indexing.
        """
        timer = None
        try:
            # Watchdog: a pathological page can wedge the decoder indefinitely.
            # Interrupting the main thread is crude but it is the only way to
            # break out of a C extension call without killing the process.
            def _fire():
                _log(f"Page {page_num} exceeded {self.page_timeout_s}s")
                if hasattr(_thread_interrupt, "interrupt_main"):
                    _thread_interrupt.interrupt_main()

            import _thread as _thread_interrupt

            timer = threading.Timer(self.page_timeout_s, _fire)
            timer.daemon = True
            timer.start()

            image = raster.render_page(doc, page_num, dpi)
            lines, mean_conf = self.engine.recognize(image)
            return {"page": page_num, "lines": lines, "mean_conf": mean_conf}

        except KeyboardInterrupt:
            # Raised in this thread by the watchdog above.
            return {"page": page_num, "lines": [], "mean_conf": 0.0,
                    "error": protocol.E_OCR_PAGE_TIMEOUT}
        except MemoryError:
            raise
        except raster.RasterError as exc:
            _log(f"Raster failed on page {page_num}: {exc}")
            return {"page": page_num, "lines": [], "mean_conf": 0.0,
                    "error": protocol.E_RASTER_FAILED}
        except Exception as exc:
            _log(f"Page {page_num} failed:\n{traceback.format_exc()}")
            return {"page": page_num, "lines": [], "mean_conf": 0.0,
                    "error": protocol.E_RASTER_FAILED, "detail": str(exc)[:200]}
        finally:
            if timer is not None:
                timer.cancel()

    # ── loop ─────────────────────────────────────────────────────────────
    def run(self):
        for line in sys.stdin:
            try:
                msg = protocol.decode(line)
            except protocol.ProtocolError:
                continue

            kind = msg.get("t")
            if kind == protocol.REQ_HELLO:
                self.handle_hello(msg)
            elif kind == protocol.REQ_DOC:
                self.handle_doc(msg)
            elif kind == protocol.REQ_CANCEL:
                self._cancelled.add(msg.get("doc_id") or "")
            elif kind == protocol.REQ_SHUTDOWN:
                return protocol.EXIT_OK
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
