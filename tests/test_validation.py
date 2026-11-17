"""Upload validation: file type, size, page count and scanned PDFs."""
from __future__ import annotations

import pytest

from app import pdf
from app.errors import ScannedPdfError, UploadError
from tests.conftest import make_pdf


def test_accepts_a_real_pdf(sample_pdf):
    pdf.validate_upload(sample_pdf, "notes.pdf")       # must not raise


def test_rejects_non_pdf_even_with_pdf_extension():
    """The signature is checked, not the file name."""
    with pytest.raises(UploadError, match="not a PDF"):
        pdf.validate_upload(b"GIF89a this is really an image", "notes.pdf")


def test_rejects_empty_file():
    with pytest.raises(UploadError, match="empty"):
        pdf.validate_upload(b"", "notes.pdf")


def test_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_UPLOAD_BYTES", 1024)
    with pytest.raises(UploadError, match="limit is"):
        pdf.validate_upload(b"%PDF-1.7" + b"x" * 2048, "big.pdf")


def test_rejects_too_many_pages(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_PAGES", 2)
    data = make_pdf(["Some page text that is long enough to count as real text."] * 3)
    with pytest.raises(UploadError, match="limit is 2 pages"):
        pdf.extract_pages(data)


def test_rejects_scanned_pdf(scanned_pdf):
    with pytest.raises(ScannedPdfError, match="scan"):
        pdf.extract_pages(scanned_pdf)


def test_extracts_one_string_per_page(sample_pdf):
    pages = pdf.extract_pages(sample_pdf)
    assert len(pages) == 2
    assert "Photosynthesis" in pages[0]
    assert "thylakoid" in pages[1]


def test_sha256_is_stable_and_content_based(sample_pdf):
    assert pdf.sha256_hex(sample_pdf) == pdf.sha256_hex(sample_pdf)
    assert pdf.sha256_hex(sample_pdf) != pdf.sha256_hex(sample_pdf + b" ")
