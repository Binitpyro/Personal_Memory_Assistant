"""GPU embedding worker. Runs in its own venv (onnxruntime-directml + numpy).

Run by path with ``python -I -u worker.py``, so it imports nothing from ``app``
and nothing beside it: ``-I`` keeps the script's directory off ``sys.path``.
numpy and onnxruntime are imported inside ``main()`` so Core's tests can import
this module for its constants without either being the DirectML build.

JSON lines on stdin/stdout, one request answered before the next is read:
  hello{protocol, model_path}         -> ready{protocol, ep, dim}
  embed{id, rows, cols, ids, mask}    -> vecs{id, b64}
      ids/mask: base64 little-endian int32, row-major (rows x cols)
      b64: float32 rows x dim, mean-pooled and L2-normalised
  shutdown                            -> exit
Any failure answers error{code, detail}. A hello that does not land on
DmlExecutionProvider is an error, never a silent CPU run.
"""

import base64
import json
import sys

PROTOCOL = 1


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    import numpy as np
    import onnxruntime as ort

    sess = None
    names: set[str] = set()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            kind = msg.get("type")
            if kind == "hello":
                if msg.get("protocol") != PROTOCOL:
                    _send({"type": "error", "code": "PROTOCOL_MISMATCH", "detail": str(PROTOCOL)})
                    return
                opts = ort.SessionOptions()
                opts.enable_cpu_mem_arena = False
                sess = ort.InferenceSession(
                    msg["model_path"], opts, providers=["DmlExecutionProvider"]
                )
                ep = sess.get_providers()[0]
                if ep != "DmlExecutionProvider":
                    _send({"type": "error", "code": "NO_DML", "detail": ep})
                    return
                names = {i.name for i in sess.get_inputs()}
                dim = int(sess.get_outputs()[0].shape[-1])
                _send({"type": "ready", "protocol": PROTOCOL, "ep": ep, "dim": dim})
            elif kind == "embed":
                if sess is None:
                    _send({"type": "error", "code": "NOT_READY", "detail": "embed before hello"})
                    continue
                shape = (msg["rows"], msg["cols"])
                ids = np.frombuffer(base64.b64decode(msg["ids"]), dtype="<i4").reshape(shape)
                mask = np.frombuffer(base64.b64decode(msg["mask"]), dtype="<i4").reshape(shape)
                ids, mask = ids.astype(np.int64), mask.astype(np.int64)
                feed = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in names:
                    feed["token_type_ids"] = np.zeros_like(ids)
                hidden = sess.run(None, feed)[0].astype(np.float32)
                m = mask[..., None].astype(np.float32)
                v = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
                v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
                b64 = base64.b64encode(v.astype("<f4").tobytes()).decode("ascii")
                _send({"type": "vecs", "id": msg["id"], "b64": b64})
            elif kind == "shutdown":
                return
            else:
                _send({"type": "error", "code": "BAD_REQUEST", "detail": str(kind)[:80]})
        except Exception as exc:  # report, keep serving; the client decides
            detail = f"{type(exc).__name__}: {exc}"[:300]
            _send({"type": "error", "code": "EMBED_FAILED", "detail": detail})


if __name__ == "__main__":
    main()
