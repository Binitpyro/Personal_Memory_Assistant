import logging
from collections.abc import Iterator
from pathlib import Path

from . import ExtractMeta

logger = logging.getLogger(__name__)


class PdfExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str | ExtractMeta]:
        """Yield text page-by-page from the PDF.

        When OCR is enabled, also classifies every page and yields a single
        trailing :class:`ExtractMeta` naming the pages that need OCR. When it
        is disabled this behaves exactly as it did before - same yields, and
        `app.ocr` is never imported.
        """
        catch_types: tuple[type[BaseException], ...]
        try:
            from pypdf.errors import PyPdfError

            catch_types = (PyPdfError, OSError)
        except ImportError:
            # Only hit when pypdf itself is replaced wholesale (e.g. the test
            # suite's patch.dict("sys.modules", {"pypdf": Mock()}) convention,
            # which doesn't provide a real pypdf.errors submodule). Fall back
            # to the pre-narrowing behavior rather than let an unrelated
            # ImportError abort extraction.
            catch_types = (OSError,)

        # Resolved once: the per-page cost of the gate is only worth paying if
        # something downstream will act on the verdict.
        ocr_on = False
        classify = None
        gate_cfg = None
        try:
            from app.config import settings

            ocr_on = bool(settings.ocr_enabled) and settings.ocr_tier != "none"
        except Exception:
            ocr_on = False

        if ocr_on:
            try:
                # Function-local by design. A module-level import would create
                # extractors -> ocr -> indexing.service -> extractors.
                from app.ocr.gate import classify_page, default_gate_config

                classify = classify_page
                gate_cfg = default_gate_config()
            except Exception as exc:
                logger.warning("OCR gate unavailable, skipping detection: %s", exc)
                ocr_on = False

        ocr_pages: list[int] = []
        native_pages = 0
        blank_pages = 0
        page_count = 0
        truncated = False

        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path), strict=False)
            total = 0
            if reader.is_encrypted:
                yield (
                    f"[ENCRYPTED PDF: {path.name}] "
                    "Cannot extract text from password-protected file."
                )
                if ocr_on:
                    # No OCR path for encrypted files - we can't rasterize them
                    # either. Still emit meta so the footer handling is uniform.
                    yield ExtractMeta(page_count=0, reason="encrypted")
                return

            for idx, page in enumerate(reader.pages):
                page_count = idx + 1
                try:
                    txt = page.extract_text()
                except catch_types as page_err:
                    logger.debug("Page extraction failed for %s: %s", path, page_err)
                    txt = None

                if ocr_on and classify is not None:
                    try:
                        from app.ocr.types import PageVerdict

                        signal = classify(page, txt or "", gate_cfg)
                        if signal.verdict == PageVerdict.OCR:
                            ocr_pages.append(idx)
                        elif signal.verdict == PageVerdict.NATIVE:
                            native_pages += 1
                        else:
                            blank_pages += 1
                    except Exception as exc:
                        logger.debug("Gate failed on %s page %d: %s", path, idx, exc)

                if txt:
                    yield txt
                    total += len(txt)
                    if total > max_file_size:
                        truncated = True
                        break
        except catch_types as e:
            # Corrupt/unreadable PDF - expected, degrade gracefully.
            logger.warning("Failed to extract PDF %s: %s", path, e)
            if ocr_on:
                yield ExtractMeta(
                    page_count=page_count,
                    ocr_pages=tuple(ocr_pages),
                    native_pages=native_pages,
                    blank_pages=blank_pages,
                    truncated=truncated,
                    reason="corrupt",
                )
            return

        if ocr_on:
            if ocr_pages:
                logger.info(
                    "OCR gate: %s - %d page(s) need OCR (%d native, %d blank)",
                    path.name,
                    len(ocr_pages),
                    native_pages,
                    blank_pages,
                )
            yield ExtractMeta(
                page_count=page_count,
                ocr_pages=tuple(ocr_pages),
                native_pages=native_pages,
                blank_pages=blank_pages,
                truncated=truncated,
            )

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        # The stream can now carry a trailing ExtractMeta; callers of this
        # method want text only.
        return "\n".join(
            f for f in self.extract_stream(path, max_file_size) if isinstance(f, str)
        )[:max_file_size]
