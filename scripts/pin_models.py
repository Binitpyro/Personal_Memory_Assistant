import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pin_models")

REPO_ROOT = Path(__file__).parent.parent
LOCK_FILE = REPO_ROOT / "models.lock.json"

MODEL_SPECS = [
    {
        "family": "embedding",
        "name": "BAAI/bge-small-en-v1.5",
        "repo_id": "Xenova/bge-small-en-v1.5",
        "revision": "ea104dacec62c0de699686887e3f920caeb4f3e3",
        "target_files": [
            "onnx/model_quantized.onnx",
            "tokenizer.json",
        ],
    },
    # NOTE: no OCR entry here on purpose. rapidocr-onnxruntime 1.4.x ships its
    # PP-OCRv4 mobile ONNX models inside the wheel (see its config.yaml, whose
    # model paths are package-relative), so they arrive already covered by the
    # pinned dependency rather than needing a separate download and digest.
]


def sha256_file(filepath: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def pin_all_models() -> dict[str, Any]:
    lock_data: dict[str, Any] = {
        "schema": 1,
        "models": {},
    }

    for spec in MODEL_SPECS:
        repo_id = spec["repo_id"]
        revision = spec["revision"]
        logger.info("Resolving snapshot for %s @ %s...", repo_id, revision)

        cached_dir = Path(
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                allow_patterns=["*.json", "*.onnx", "onnx/*"],
                local_files_only=False,
            )
        )

        files_map: dict[str, dict[str, Any]] = {}
        for rel_path in spec["target_files"]:
            file_path = cached_dir / rel_path
            if not file_path.exists():
                logger.warning("Target file %s does not exist in cached repo %s", rel_path, cached_dir)
                continue

            digest, size_bytes = sha256_file(file_path)
            files_map[rel_path] = {
                "sha256": digest,
                "size_bytes": size_bytes,
            }
            logger.info("Pinned %s -> SHA256: %s... (%d bytes)", rel_path, digest[:16], size_bytes)

        lock_data["models"][spec["name"]] = {
            "repo_id": repo_id,
            "revision": revision,
            "family": spec["family"],
            "files": files_map,
        }

    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        json.dump(lock_data, f, indent=2)
    logger.info("Successfully updated %s", LOCK_FILE)
    return lock_data


if __name__ == "__main__":
    pin_all_models()
