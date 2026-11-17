"""
Shared test fixtures.

Every test runs against the "fake" LLM provider, a throwaway SQLite file and
PDFs built in memory with PyMuPDF - no network, no key, no fixture files.
"""
from __future__ import annotations

import pytest

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from app import llm, ratelimit, store


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch, tmp_path):
    """Point the app at the fake provider and a temporary database."""
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm.reset_client_cache()

    store.set_db_path(tmp_path / "test.sqlite3")
    store.init_db()
    ratelimit.reset()
    yield
    store.set_db_path(store.config.DB_PATH)


def make_pdf(pages: list[str]) -> bytes:
    """Build a small text PDF, one page per string."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        y = 72
        for line in text.splitlines() or [""]:
            page.insert_text((72, y), line, fontsize=11)
            y += 16
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def sample_pdf() -> bytes:
    """A two-page set of lecture notes with headings."""
    return make_pdf(
        [
            "Lecture 1: Photosynthesis\n"
            "Photosynthesis converts light energy into chemical energy.\n"
            "It takes place in the chloroplast of plant cells.\n"
            "The overall equation is 6CO2 + 6H2O -> C6H12O6 + 6O2.",
            "2. The Light-Dependent Reactions\n"
            "These reactions occur in the thylakoid membrane.\n"
            "Water is split to release electrons, protons and oxygen.\n"
            "ATP and NADPH are produced for the Calvin cycle.",
        ]
    )


@pytest.fixture
def scanned_pdf() -> bytes:
    """A PDF with pages but effectively no text layer, like a scan."""
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def client():
    """FastAPI test client with the app's startup/shutdown hooks run."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
