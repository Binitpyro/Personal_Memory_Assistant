import csv
import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

# Rows are bounded separately from bytes, and for a different reason. A CSV of
# empty or whitespace-only rows yields nothing, so `total` never grows and the
# byte budget alone would walk the entire file - the row cap is what bounds
# *iteration*. But a flat 5000 also truncated legitimate narrow-row files long
# before they reached the byte budget, which is what this scaling fixes: a floor
# preserves the old behaviour for small budgets, a ceiling bounds pathological
# input, and in between the row cap tracks the budget the caller actually set.
_MIN_CSV_ROWS = 5_000
_MAX_CSV_ROWS = 200_000
_ASSUMED_ROW_BYTES = 40


def _row_budget(max_file_size: int) -> int:
    scaled = max(1, max_file_size) // _ASSUMED_ROW_BYTES
    return max(_MIN_CSV_ROWS, min(scaled, _MAX_CSV_ROWS))


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
                row_cap = _row_budget(max_file_size)
                for i, row in enumerate(reader):
                    if i >= row_cap:
                        logger.warning(
                            "CSV %s exceeds %d rows; truncating remaining rows.",
                            path,
                            row_cap,
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
