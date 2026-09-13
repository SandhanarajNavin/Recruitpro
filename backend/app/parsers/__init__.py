"""Document parsing: raw bytes to text, and text to structured records.

``extract_text`` and ``ExtractionError`` are re-exported because the ingestion
service treats extraction as one capability, not three modules.
"""

from app.parsers.extractor import ExtractionError, extract_text

__all__ = ["ExtractionError", "extract_text"]
