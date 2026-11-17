"""
SQLite storage: one table, four queries.

A row holds the extracted text and the finished result for one processed
upload. The uploaded PDF itself is never stored. Rows older than
RETENTION_HOURS have their text and result cleared (the id stays behind so the
app can say "expired" rather than "not found").
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

_db_path: Path | None = None


# --- Connection ------------------------------------------------------------

def set_db_path(path: Path | str) -> None:
    """Point the store at a different file (used by the tests)."""
    global _db_path
    _db_path = Path(path)


def db_path() -> Path:
    return _db_path if _db_path is not None else config.DB_PATH


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    sha256        TEXT NOT NULL,
    options_hash  TEXT NOT NULL,
    filename      TEXT,
    page_count    INTEGER,
    text_json     TEXT,            -- JSON list: one string per page
    result_json   TEXT,            -- JSON: {"notes": [...], "questions": [...]}
    created_at    TEXT NOT NULL    -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_documents_cache
    ON documents (sha256, options_hash);
"""


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# --- Helpers ---------------------------------------------------------------

def options_hash(options: dict) -> str:
    """Stable hash of the generation options, so the cache key is file + options."""
    blob = json.dumps(options, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff() -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=config.RETENTION_HOURS)
    ).isoformat()


# --- Queries ---------------------------------------------------------------

def save_document(
    *,
    sha256: str,
    options_hash_value: str,
    filename: str,
    page_count: int,
    pages: list[str],
    result: dict,
) -> str:
    """Insert a finished document and return its new id."""
    doc_id = uuid.uuid4().hex[:12]
    with connect() as conn:
        conn.execute(
            "INSERT INTO documents (id, sha256, options_hash, filename, page_count,"
            " text_json, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                sha256,
                options_hash_value,
                filename,
                page_count,
                json.dumps(pages),
                json.dumps(result),
                _now(),
            ),
        )
    return doc_id


def find_cached(sha256: str, options_hash_value: str) -> sqlite3.Row | None:
    """Step 2 of the pipeline: same file + same options = reuse the saved result."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE sha256 = ? AND options_hash = ?"
            " AND result_json IS NOT NULL AND created_at >= ?"
            " ORDER BY created_at DESC LIMIT 1",
            (sha256, options_hash_value, _cutoff()),
        ).fetchone()


def get_document(doc_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()


def purge_expired() -> int:
    """
    Clear text and results older than the retention window.

    Returns the number of rows cleared. Called on startup and on every upload,
    which is plenty for an app of this size - no scheduler needed.
    """
    with connect() as conn:
        cur = conn.execute(
            "UPDATE documents SET text_json = NULL, result_json = NULL"
            " WHERE created_at < ? AND (text_json IS NOT NULL OR result_json IS NOT NULL)",
            (_cutoff(),),
        )
        return cur.rowcount


def row_pages(row: sqlite3.Row) -> list[str]:
    return json.loads(row["text_json"]) if row["text_json"] else []


def row_result(row: sqlite3.Row) -> dict:
    return json.loads(row["result_json"]) if row["result_json"] else {}
