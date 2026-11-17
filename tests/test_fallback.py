"""
Empty-extraction fallback.

When the model returns no key points / testable items for any chunk (some
models do this unpredictably), the pipeline must still produce notes and
questions from the page text rather than failing with "nothing teachable".
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import llm, pipeline
from app.chunking import chunk_pages
from app.errors import LLMError

PAGE = "Photosynthesis converts light energy into chemical energy in the chloroplast. "


def _router(monkeypatch, *, extract, notes, questions):
    """Point llm.generate at the right canned reply for each prompt kind."""
    def gen(prompt, system=None):
        if "EXTRACT" in prompt:
            return extract
        if "LECTURE TEXT" in prompt and '"notes"' in prompt:
            return notes
        if "LECTURE TEXT" in prompt and '"questions"' in prompt:
            return questions
        return json.dumps({})
    monkeypatch.setattr(llm, "generate", gen)


def _run_merges(chunks, options):
    extractions = asyncio.run(
        pipeline.extract_items(chunks, asyncio.Semaphore(3))
    )
    notes = asyncio.run(pipeline.merge_notes(extractions, chunks))
    questions = asyncio.run(pipeline.merge_questions(extractions, options, chunks))
    return notes, questions


def test_empty_extraction_falls_back_to_text(monkeypatch):
    _router(
        monkeypatch,
        extract=json.dumps({"key_points": [], "testable_items": []}),
        notes=json.dumps({"notes": [{"topic": "T", "points": ["p1", "p2"]}]}),
        questions=json.dumps(
            {"questions": [{"type": "short_answer", "question": "Q?",
                            "answer": "A", "page": 1, "difficulty": "easy"}]}
        ),
    )
    options = pipeline.normalise_options(3, ["short_answer"], "easy")
    notes, questions = _run_merges(chunk_pages([PAGE, PAGE]), options)

    assert notes.notes and notes.notes[0].topic == "T"
    assert questions.questions and questions.questions[0].question == "Q?"


def test_normal_extraction_does_not_use_the_fallback(monkeypatch):
    """When items exist, the item-based merge is used, not the text prompt."""
    seen: list[str] = []

    def gen(prompt, system=None):
        seen.append(prompt)
        if "EXTRACT" in prompt:
            return json.dumps({
                "key_points": [{"topic": "Bio", "point": "x", "page": 1}],
                "testable_items": [{"kind": "definition", "content": "y", "page": 1}],
            })
        return json.dumps({"notes": [{"topic": "Bio", "points": ["x"]}],
                           "questions": [{"type": "short_answer", "question": "q",
                                          "answer": "a", "page": 1}]})
    monkeypatch.setattr(llm, "generate", gen)

    options = pipeline.normalise_options(3, ["short_answer"], "easy")
    _run_merges(chunk_pages([PAGE, PAGE]), options)
    assert not any("LECTURE TEXT" in p for p in seen)   # fallback never fired


def test_hard_error_only_when_even_the_text_yields_nothing(monkeypatch, sample_pdf):
    """The genuine 'nothing teachable' error survives, with clearer wording."""
    _router(
        monkeypatch,
        extract=json.dumps({"key_points": [], "testable_items": []}),
        notes=json.dumps({"notes": []}),
        questions=json.dumps({"questions": []}),
    )
    options = pipeline.normalise_options(3, ["short_answer"], "easy")
    with pytest.raises(LLMError, match="could not find anything teachable"):
        asyncio.run(pipeline.process_upload(sample_pdf, "admin.pdf", options))
