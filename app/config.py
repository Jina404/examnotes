"""
Central configuration.

Everything tunable lives here so the rest of the code never reads os.environ
directly (except llm.py, which owns the provider settings).
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    # Optional: load a local .env file during development.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
    pass


# --- Paths -----------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Writable directory for the SQLite file and the short-lived upload temp files.
DATA_DIR = Path(os.environ.get("EXAMNOTES_DATA_DIR", BASE_DIR.parent / "data"))
DB_PATH = DATA_DIR / "examnotes.sqlite3"


# --- Upload limits ---------------------------------------------------------

MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 20 MB
MAX_PAGES = 100
PDF_MAGIC = b"%PDF-"                  # file signature, checked instead of the extension

# A PDF with fewer than this many extractable characters per page on average is
# treated as a scan (we do not do OCR).
MIN_CHARS_PER_PAGE = 60


# --- Question options ------------------------------------------------------

DEFAULT_QUESTION_COUNT = 10
MAX_QUESTION_COUNT = 30
QUESTION_TYPES = ("multiple_choice", "short_answer", "long_answer")
DIFFICULTIES = ("easy", "medium", "hard")
DEFAULT_DIFFICULTY = "mixed"          # "mixed" lets the model vary difficulty


# --- Chunking --------------------------------------------------------------

PAGES_PER_CHUNK = 3
MAX_CHUNK_CHARS = 12_000              # hard ceiling so one dense page cannot blow up a prompt


# --- Pipeline --------------------------------------------------------------

CHUNK_CONCURRENCY = 3                 # small limit: free API tiers are rate limited
RETENTION_HOURS = 24                  # extracted text + results are purged after this


# --- Rate limiting ---------------------------------------------------------

UPLOADS_PER_HOUR_PER_IP = 5
