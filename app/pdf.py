"""
PDF validation and text extraction.

Kept deliberately small: validate the bytes, then hand back one string per page.
No OCR - scanned PDFs are rejected with a clear message.
"""
from __future__ import annotations

import hashlib

try:                      # PyMuPDF >= 1.24 ships the `pymupdf` name...
    import pymupdf
except ImportError:       # ...older releases only have `fitz`.
    import fitz as pymupdf

from .config import MAX_PAGES, MAX_UPLOAD_BYTES, MIN_CHARS_PER_PAGE, PDF_MAGIC
from .errors import ScannedPdfError, UploadError


def sha256_hex(data: bytes) -> str:
    """Content hash of the upload, used as the cache key."""
    return hashlib.sha256(data).hexdigest()


def validate_upload(data: bytes, filename: str = "") -> None:
    """
    Reject anything we cannot or should not process.

    Checks the real file signature rather than trusting the .pdf extension.
    Raises UploadError with a message meant for the student.
    """
    if not data:
        raise UploadError("The uploaded file is empty. Please choose a PDF file.")

    if len(data) > MAX_UPLOAD_BYTES:
        mb = len(data) / (1024 * 1024)
        raise UploadError(
            f"That file is {mb:.1f} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB - try splitting the notes up."
        )

    # File signature check. A real PDF starts with "%PDF-" (some files carry a
    # few junk bytes first, which readers tolerate, so look in the first 1 KB).
    head = data[:1024]
    if not head.startswith(PDF_MAGIC) and PDF_MAGIC not in head:
        shown = f" ({filename})" if filename else ""
        raise UploadError(
            f"That file{shown} is not a PDF. ExamNotes only accepts PDF files - "
            "renaming another file to .pdf will not work."
        )


def open_document(data: bytes):
    """Open the PDF, converting any parser failure into a friendly error."""
    try:
        return pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - any parse failure means a bad upload
        raise UploadError(
            "This PDF could not be opened - it may be corrupted or password "
            f"protected. ({exc.__class__.__name__})"
        ) from exc


def extract_pages(data: bytes) -> list[str]:
    """
    Return the text of each page, index 0 being page 1.

    Raises UploadError if the PDF has too many pages, and ScannedPdfError if
    there is barely any extractable text.
    """
    doc = open_document(data)
    try:
        if doc.needs_pass:
            raise UploadError(
                "This PDF is password protected. Please remove the password and "
                "upload it again."
            )
        if doc.page_count > MAX_PAGES:
            raise UploadError(
                f"This PDF has {doc.page_count} pages. The limit is {MAX_PAGES} "
                "pages - please upload one lecture or chapter at a time."
            )
        if doc.page_count == 0:
            raise UploadError("This PDF has no pages.")

        pages = [page.get_text("text") or "" for page in doc]
    finally:
        doc.close()

    check_has_text(pages)
    return pages


def check_has_text(pages: list[str]) -> None:
    """Treat a PDF with almost no selectable text as a scan and refuse it."""
    total_chars = sum(len(p.strip()) for p in pages)
    if total_chars < MIN_CHARS_PER_PAGE * max(len(pages), 1):
        raise ScannedPdfError()
