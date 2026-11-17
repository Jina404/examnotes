"""
Split extracted page text into model-sized chunks.

Two rules:
  * a chunk is a few consecutive pages (config.PAGES_PER_CHUNK) and never
    longer than config.MAX_CHUNK_CHARS characters;
  * where possible the break lands on a page that starts a new heading, so a
    topic is not cut in half.

Every chunk keeps its page range, which is what lets each question carry a
page number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import MAX_CHUNK_CHARS, PAGES_PER_CHUNK

# "1.2", "1.2.3", "Chapter 4", "Lecture 2", "Section 3", "Week 5", "Topic 2"
_NUMBERED = re.compile(r"^\s*(\d+(\.\d+)*)[.)]?\s+\S")
_KEYWORD = re.compile(
    r"^\s*(chapter|lecture|section|unit|topic|week|part|module|appendix)\b",
    re.IGNORECASE,
)


@dataclass
class Chunk:
    """A few pages of text, with the page numbers they came from."""

    index: int
    start_page: int          # 1-based, inclusive
    end_page: int            # 1-based, inclusive
    text: str                # page text with "[Page N]" markers

    @property
    def page_range(self) -> str:
        if self.start_page == self.end_page:
            return f"page {self.start_page}"
        return f"pages {self.start_page}-{self.end_page}"


def looks_like_heading(line: str) -> bool:
    """Heuristic: is this line a heading rather than body text?"""
    s = line.strip()
    if not (3 <= len(s) <= 90):
        return False
    if s.endswith((".", ",", ";", ":")) and not _NUMBERED.match(s):
        return False
    if _NUMBERED.match(s) or _KEYWORD.match(s):
        return True
    words = s.split()
    if len(words) > 12:
        return False
    letters = [c for c in s if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True                                  # ALL CAPS TITLE
    # Title Case: most words capitalised and the line has no sentence punctuation.
    capped = sum(1 for w in words if w[:1].isupper())
    return len(words) >= 2 and capped >= max(2, len(words) - 1)


def starts_with_heading(page_text: str) -> bool:
    """True if one of the first few non-empty lines of a page is a heading."""
    lines = [ln for ln in page_text.splitlines() if ln.strip()][:3]
    return any(looks_like_heading(ln) for ln in lines)


def chunk_pages(
    pages: list[str],
    pages_per_chunk: int = PAGES_PER_CHUNK,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[Chunk]:
    """
    Group page texts into Chunk objects.

    `pages[0]` is page 1. Pages with no text still count towards the page
    numbering, so page references stay correct.
    """
    chunks: list[Chunk] = []
    current: list[tuple[int, str]] = []       # (page_number, text)
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        body = "\n\n".join(f"[Page {n}]\n{t.strip()}" for n, t in current)
        chunks.append(
            Chunk(
                index=len(chunks),
                start_page=current[0][0],
                end_page=current[-1][0],
                text=body,
            )
        )
        current = []
        current_chars = 0

    for i, text in enumerate(pages):
        page_no = i + 1
        size = len(text)

        # Break before this page if the chunk is already full, or if it is
        # nearly full and this page opens a new heading.
        if current:
            full = len(current) >= pages_per_chunk or current_chars + size > max_chars
            nearly_full = len(current) >= max(1, pages_per_chunk - 1)
            if full or (nearly_full and starts_with_heading(text)):
                flush()

        current.append((page_no, text))
        current_chars += size

        # A single enormous page: emit it on its own rather than overflow.
        if current_chars > max_chars:
            flush()

    flush()
    return chunks
