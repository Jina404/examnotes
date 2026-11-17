"""
Application-level exceptions.

Each one carries an HTTP status code and a message that is safe (and useful)
to show to a student in the browser.
"""
from __future__ import annotations


class ExamNotesError(Exception):
    """Base class: every error we raise on purpose has a status and a message."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UploadError(ExamNotesError):
    """The uploaded file was rejected (wrong type, too big, too many pages...)."""

    status_code = 400


class ScannedPdfError(ExamNotesError):
    """The PDF has no usable text layer - almost certainly a scan or photos."""

    status_code = 422

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "This PDF has almost no selectable text, so it looks like a scan or "
               "photos of pages. ExamNotes cannot read scanned PDFs (there is no OCR). "
               "Please upload a PDF exported from slides or a document instead."
        )


class RateLimitedError(ExamNotesError):
    """The caller's IP has used up its hourly upload allowance."""

    status_code = 429


class NotFoundError(ExamNotesError):
    status_code = 404


class ExpiredError(ExamNotesError):
    """The document existed but its text and result were purged after 24 hours."""

    status_code = 410

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "This document has expired. ExamNotes keeps results for 24 hours only; "
               "please upload the PDF again."
        )


class LLMError(ExamNotesError):
    """The AI service failed (rate limit, outage, or unparsable output)."""

    status_code = 503
