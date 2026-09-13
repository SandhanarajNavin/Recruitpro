"""PDF text extraction."""

from __future__ import annotations

import io

from app.core.logging import get_logger

logger = get_logger(__name__)

PDF_TYPES = {"application/pdf"}


class PdfExtractionError(RuntimeError):
    pass


def extract(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        # An empty-password decrypt covers the common "protected but not secret" case.
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise PdfExtractionError("PDF is encrypted and could not be opened.") from exc

    pages = []
    for index, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - one bad page must not lose the rest
            logger.warning("Failed to extract PDF page %s: %s", index, exc)
    return "\n".join(pages)
