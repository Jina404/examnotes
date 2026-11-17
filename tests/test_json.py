"""JSON handling: tolerant extraction, validation fixes and the single retry."""
from __future__ import annotations

import asyncio
import json

import pytest

from app import llm, pipeline
from app.errors import LLMError
from app.schemas import ChunkExtraction, Question, Result, clamp_pages, extract_json


def test_extracts_bare_json():
    assert extract_json('{"notes": []}') == {"notes": []}


def test_extracts_fenced_json():
    text = 'Sure!\n```json\n{"notes": [{"topic": "A", "points": ["x"]}]}\n```\n'
    assert extract_json(text)["notes"][0]["topic"] == "A"


def test_extracts_json_with_prose_around_it():
    text = 'Here you go:\n{"questions": []}\nHope that helps.'
    assert extract_json(text) == {"questions": []}


@pytest.mark.parametrize("bad", ["", "   ", "no json at all", "{broken: "])
def test_rejects_unparsable_text(bad):
    with pytest.raises(ValueError):
        extract_json(bad)


def test_page_numbers_are_coerced():
    assert Question(type="short_answer", question="q", answer="a", page="p. 12").page == 12
    assert Question(type="short_answer", question="q", answer="a", page=0).page == 1
    assert Question(type="short_answer", question="q", answer="a", page=None).page == 1


def test_multiple_choice_without_options_becomes_short_answer():
    q = Question(type="multiple_choice", question="q", answer="a", options=[])
    assert q.type == "short_answer" and q.options is None


def test_options_are_dropped_for_non_mcq():
    q = Question(type="short_answer", question="q", answer="a", options=["x", "y"])
    assert q.options is None


def test_clamp_pages_to_document_length():
    result = Result(questions=[Question(type="short_answer", question="q", answer="a", page=99)])
    assert clamp_pages(result, 10).questions[0].page == 10


def test_fake_provider_returns_valid_json():
    payload = json.loads(llm.generate("anything"))
    assert ChunkExtraction.model_validate(payload).key_points
    assert Result.model_validate(payload).questions


def test_ask_json_retries_once_then_succeeds(monkeypatch):
    """A first unparsable reply must trigger exactly one retry."""
    calls = []

    def flaky(prompt, system=None):
        calls.append(prompt)
        if len(calls) == 1:
            return "Sorry, I cannot do that."
        return json.dumps({"key_points": [{"topic": "T", "point": "P", "page": 2}]})

    monkeypatch.setattr(llm, "generate", flaky)
    parsed = asyncio.run(pipeline.ask_json("prompt", ChunkExtraction))

    assert len(calls) == 2
    assert "IMPORTANT" in calls[1]            # the retry carries the reminder
    assert parsed.key_points[0].page == 2


def test_ask_json_gives_up_after_the_retry(monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda prompt, system=None: "still not json")
    with pytest.raises(LLMError, match="could not read"):
        asyncio.run(pipeline.ask_json("prompt", ChunkExtraction))


def test_unknown_provider_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "banana")
    with pytest.raises(LLMError, match="Unknown LLM_PROVIDER"):
        llm.generate("hi")


def test_rate_limit_errors_are_recognised():
    assert llm._is_rate_limit(Exception("429 RESOURCE_EXHAUSTED"))
    assert not llm._is_rate_limit(Exception("500 internal error"))
