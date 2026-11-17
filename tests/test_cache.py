"""Caching: the same file with the same options is never processed twice."""
from __future__ import annotations

import asyncio

from app import pipeline, store


def run(coro):
    return asyncio.run(coro)


def options(count=10):
    return pipeline.normalise_options(count, ["short_answer"], "medium")


def test_second_upload_of_the_same_file_is_served_from_cache(sample_pdf):
    first = run(pipeline.process_upload(sample_pdf, "notes.pdf", options()))
    second = run(pipeline.process_upload(sample_pdf, "notes.pdf", options()))

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["document_id"] == first["document_id"]
    assert second["result"] == first["result"]


def test_different_options_are_a_different_cache_entry(sample_pdf):
    first = run(pipeline.process_upload(sample_pdf, "notes.pdf", options(10)))
    second = run(pipeline.process_upload(sample_pdf, "notes.pdf", options(5)))

    assert second["cached"] is False
    assert second["document_id"] != first["document_id"]


def test_options_hash_is_order_independent():
    a = store.options_hash({"question_count": 10, "difficulty": "easy"})
    b = store.options_hash({"difficulty": "easy", "question_count": 10})
    assert a == b
    assert a != store.options_hash({"question_count": 11, "difficulty": "easy"})


def test_expired_rows_lose_their_text_and_result(sample_pdf, monkeypatch):
    doc = run(pipeline.process_upload(sample_pdf, "notes.pdf", options()))
    monkeypatch.setattr(store.config, "RETENTION_HOURS", 0)

    assert store.purge_expired() == 1
    row = store.get_document(doc["document_id"])
    assert row is not None                      # the id survives...
    assert row["text_json"] is None             # ...but the content is gone
    assert row["result_json"] is None


def test_expired_rows_are_not_served_from_cache(sample_pdf, monkeypatch):
    run(pipeline.process_upload(sample_pdf, "notes.pdf", options()))
    monkeypatch.setattr(store.config, "RETENTION_HOURS", 0)
    store.purge_expired()

    again = run(pipeline.process_upload(sample_pdf, "notes.pdf", options()))
    assert again["cached"] is False


def test_the_uploaded_pdf_is_never_stored(sample_pdf):
    run(pipeline.process_upload(sample_pdf, "notes.pdf", options()))
    row = store.get_document(
        store.find_cached(
            __import__("app.pdf", fromlist=["sha256_hex"]).sha256_hex(sample_pdf),
            store.options_hash(options()),
        )["id"]
    )
    assert set(row.keys()) == {
        "id", "sha256", "options_hash", "filename", "page_count",
        "text_json", "result_json", "created_at",
    }
    assert sample_pdf[:8].decode("latin-1") not in (row["text_json"] or "")
