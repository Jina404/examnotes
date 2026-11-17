"""Chunking: page ranges, heading-aware breaks and the size ceiling."""
from __future__ import annotations

from app.chunking import Chunk, chunk_pages, looks_like_heading

BODY = "This is ordinary body text on the page, long enough to be realistic. "


def test_groups_pages_and_keeps_page_numbers():
    pages = [BODY] * 7
    chunks = chunk_pages(pages, pages_per_chunk=3)

    assert [(c.start_page, c.end_page) for c in chunks] == [(1, 3), (4, 6), (7, 7)]
    assert all(isinstance(c, Chunk) for c in chunks)
    assert [c.index for c in chunks] == [0, 1, 2]


def test_every_page_appears_exactly_once():
    pages = [f"{BODY} page {i}" for i in range(1, 11)]
    chunks = chunk_pages(pages, pages_per_chunk=3)
    covered = [p for c in chunks for p in range(c.start_page, c.end_page + 1)]
    assert covered == list(range(1, 11))


def test_text_carries_page_markers():
    chunks = chunk_pages([BODY, BODY], pages_per_chunk=3)
    assert "[Page 1]" in chunks[0].text and "[Page 2]" in chunks[0].text


def test_breaks_early_at_a_heading():
    """A heading on page 3 should start a new chunk instead of being swallowed."""
    pages = [BODY, BODY, "2. The Light-Dependent Reactions\n" + BODY, BODY]
    chunks = chunk_pages(pages, pages_per_chunk=3)
    assert (chunks[0].start_page, chunks[0].end_page) == (1, 2)
    assert chunks[1].start_page == 3


def test_huge_page_gets_its_own_chunk():
    pages = [BODY, "x" * 20_000, BODY]
    chunks = chunk_pages(pages, pages_per_chunk=3, max_chars=5_000)
    sizes = {(c.start_page, c.end_page) for c in chunks}
    assert (2, 2) in sizes


def test_page_range_label():
    chunks = chunk_pages([BODY, BODY], pages_per_chunk=3)
    assert chunks[0].page_range == "pages 1-2"
    assert chunk_pages([BODY], pages_per_chunk=3)[0].page_range == "page 1"


def test_heading_detection():
    assert looks_like_heading("3.2 Enzyme Kinetics")
    assert looks_like_heading("Chapter 4: Thermodynamics")
    assert looks_like_heading("THE CALVIN CYCLE")
    assert not looks_like_heading(
        "The reaction proceeds slowly at low temperatures because of this."
    )
    assert not looks_like_heading("")
