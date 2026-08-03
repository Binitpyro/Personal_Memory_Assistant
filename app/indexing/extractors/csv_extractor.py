import csv
import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_CSV_ROWS = 5000


class CsvExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".csv"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield formatted rows from the CSV."""
        try:
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.reader(f)
                # Read headers
                try:
                    headers = next(reader)
                except StopIteration:
                    return

                total = 0
                for i, row in enumerate(reader):
                    if i > _MAX_CSV_ROWS:
                        logger.warning(
                            "CSV %s exceeds %d rows; truncating remaining rows.",
                            path,
                            _MAX_CSV_ROWS,
                        )
                        break
                    # Convert to key: value format
                    formatted_row = ", ".join(
                        f"{h}: {v}" for h, v in zip(headers, row, strict=False) if v.strip()
                    )
                    if formatted_row:
                        yield formatted_row
                        total += len(formatted_row)
                        if total > max_file_size:
                            break
        except Exception as e:
            logger.warning("Failed to extract CSV %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "\n".join(self.extract_stream(path, max_file_size))[:max_file_size]
