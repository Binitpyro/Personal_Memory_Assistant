from pathlib import Path
import logging
import csv

logger = logging.getLogger(__name__)

class CsvExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".csv"

    def extract(self, path: Path, max_file_size: int) -> str:
        try:
            rows = []
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.reader(f)
                # Read headers
                try:
                    headers = next(reader)
                except StopIteration:
                    return ""
                
                for i, row in enumerate(reader):
                    if i > 5000: break
                    # Convert to key: value format
                    formatted_row = ", ".join(f"{h}: {v}" for h, v in zip(headers, row) if v.strip())
                    if formatted_row:
                        rows.append(formatted_row)
            return "\n".join(rows)[:max_file_size]
        except Exception as e:
            logger.warning("Failed to extract CSV %s: %s", path, e)
            return ""
