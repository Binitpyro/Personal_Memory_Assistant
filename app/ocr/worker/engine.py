"""RapidOCR wrapper. Runs only inside `<ocr_env>`.

MUST NOT IMPORT `app.*`.
"""

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

        overrides = self._custom_model_paths()
        if overrides:
            try:
                self._ocr = RapidOCR(**overrides)
                self.model_version = "custom"
                return
            except Exception:
                # Never let a bad override take the engine down - the bundled
                # models are always present and always work.
                pass

        self._ocr = RapidOCR()

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
