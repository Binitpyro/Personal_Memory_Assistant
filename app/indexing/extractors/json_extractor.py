from pathlib import Path
import logging
import json
import re

logger = logging.getLogger(__name__)

class JsonExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".json"

    def extract(self, path: Path, max_file_size: int) -> str:
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read(max_file_size)
            try: return json.dumps(json.loads(text), indent=2, ensure_ascii=False)[:200000]
            except Exception: pass
            
            try: return json.dumps(json.loads(re.sub(r',\s*([}\]])', r'\1', text)), indent=2, ensure_ascii=False)[:200000]
            except Exception: pass
            return text[:200000]
        except Exception as e:
            logger.warning("Failed to extract JSON %s: %s", path, e)
            return ""
