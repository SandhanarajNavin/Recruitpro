"""Text extraction dispatch (architecture doc §7 step 4).

Deterministic, no model involved. Failure here is reported as a resume-level error
rather than an exception that loses the upload: a resume whose text cannot be read
is a visible, actionable state for the recruiter.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.parsers import docx_parser, pdf_parser
from app.utils.file_utils import suffix_of
from app.utils.text_utils import normalise_lines, too_little_text

logger = get_logger(__name__)

PDF_TYPES = pdf_parser.PDF_TYPES
DOCX_TYPES = docx_parser.DOCX_TYPES
TEXT_TYPES = {"text/plain", "text/markdown"}


class ExtractionError(RuntimeError):
    pass


def extract_text(data: bytes, content_type: str, filename: str = "") -> str:
    lowered = (content_type or "").lower()
    suffix = suffix_of(filename)

    try:
        if lowered in PDF_TYPES or suffix == "pdf":
            text = pdf_parser.extract(data)
        elif lowered in DOCX_TYPES or suffix in ("docx", "doc"):
            text = docx_parser.extract(data)
        elif lowered in TEXT_TYPES or suffix in ("txt", "md"):
            text = data.decode("utf-8", errors="replace")
        else:
            raise ExtractionError(f"Unsupported resume type: {content_type or suffix or 'unknown'}")
    except ExtractionError:
        raise
    except pdf_parser.PdfExtractionError as exc:
        raise ExtractionError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"Could not read the document: {exc}") from exc

    cleaned = normalise_lines(text)
    if too_little_text(cleaned):
        # A scanned image PDF extracts to almost nothing. Say so plainly instead of
        # letting a blank profile through the pipeline.
        raise ExtractionError(
            "Extracted almost no text — the file may be a scanned image and need OCR."
        )
    return cleaned
