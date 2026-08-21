"""RapidOCR wrapper. Runs only inside `<ocr_env>`.

MUST NOT IMPORT `app.*`.
"""

import hashlib
import os

import postproc

#: Optional local overrides. Absent in a normal install: rapidocr-onnxruntime
#: 1.4.x carries its PP-OCRv4 mobile models inside the wheel and resolves them
#: from package-relative paths, so there is nothing to fetch and nothing to
#: point at. These names only exist so a user can drop in different weights.
DET_MODEL = "det.onnx"
REC_MODEL = "rec.onnx"
CLS_MODEL = "cls.onnx"
REC_KEYS = "rec_keys.txt"


class Engine:
    def __init__(self, models_dir, conf_floor):
        self.models_dir = models_dir
        self.conf_floor = float(conf_floor)
        # Must match app/ocr/settings.MODEL_VERSION: it is part of the
        # ocr_cache primary key, so a mismatch would serve text produced by a
        # different model.
        self.model_version = "ppocrv4-mobile"
        self.execution_provider = "CPUExecutionProvider"

        from rapidocr_onnxruntime import RapidOCR

        # Which venv this is decides the provider: the GPU tier installs
        # onnxruntime-directml, the CPU tier plain onnxruntime, and they cannot
        # coexist in one interpreter. Detecting it here rather than being told
        # over the wire means `ep` reports what is genuinely loaded - which is
        # what the manager compares against the install stamp.
        dml = self._directml_kwargs()
        if dml:
            self.execution_provider = "DmlExecutionProvider"

        base_kwargs = {**dml, "rec_batch_num": 8}
        overrides = self._custom_model_paths()
        if overrides:
            try:
                self._ocr = RapidOCR(**overrides, **base_kwargs)
                # Not the bare string "custom": that is one label for every
                # possible set of weights, so two different override sets would
                # share a cache key and serve each other's text. The digest is
                # taken from the files actually loaded, so the identity changes
                # whenever the weights do.
                self.model_version = f"custom-{self._override_digest(overrides)}"
                return
            except Exception:  # nosec B110
                # A bad override must not take the engine down - the bundled
                # models are always present and always work. The fallback is not
                # silent: model_version stays at the bundled value, which the
                # manager compares against the install stamp and reports as an
                # engine mismatch.
                pass

        self._ocr = RapidOCR(**base_kwargs)

    @staticmethod
    def _directml_kwargs():
        """`{det,cls,rec}_use_dml=True` when this venv can actually do DirectML.

        rapidocr's own `_check_dml()` additionally requires Windows 10+ and the
        provider to be registered, and falls back to CPU silently if not - so
        this is a request, not a guarantee. `execution_provider` is corrected
        from the live session afterwards rather than assumed from this.
        """
        try:
            import onnxruntime as ort

            if "DmlExecutionProvider" not in ort.get_available_providers():
                return {}
        except Exception:
            return {}
        return {"det_use_dml": True, "cls_use_dml": True, "rec_use_dml": True}

    @staticmethod
    def _override_digest(overrides):
        """Short content digest of the override weights, for the cache key.

        Hashes the model bytes rather than paths or mtimes: the same weights
        must produce the same identity across machines and reinstalls, and
        different weights must never collide.
        """
        digest = hashlib.sha256()
        for key in sorted(overrides):
            path = overrides[key]
            digest.update(key.encode("utf-8"))
            try:
                with open(path, "rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
            except OSError:
                # Unreadable here means unreadable at load time too, so this
                # branch is effectively unreachable - but a partial digest is
                # still better than crashing a worker that already loaded.
                digest.update(b"\0unreadable")
        return digest.hexdigest()[:12]

    def _custom_model_paths(self):
        """Override kwargs, or {} when no user-supplied models are present."""
        if not self.models_dir or not os.path.isdir(self.models_dir):
            return {}

        det = os.path.join(self.models_dir, DET_MODEL)
        rec = os.path.join(self.models_dir, REC_MODEL)
        if not (os.path.isfile(det) and os.path.isfile(rec)):
            return {}

        kwargs = {"det_model_path": det, "rec_model_path": rec}
        cls = os.path.join(self.models_dir, CLS_MODEL)
        if os.path.isfile(cls):
            kwargs["cls_model_path"] = cls
        keys = os.path.join(self.models_dir, REC_KEYS)
        if os.path.isfile(keys):
            kwargs["rec_keys_path"] = keys
        return kwargs

    def recognize(self, image):
        """Recognize one page image. Returns (lines, mean_conf)."""
        result, _elapsed = self._ocr(image)
        if not result:
            return [], 0.0
        page_width = image.shape[1] if hasattr(image, "shape") and len(image.shape) >= 2 else None
        return postproc.to_lines(result, self.conf_floor, page_width=page_width)
