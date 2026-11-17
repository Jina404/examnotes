"""
Result shapes and tolerant JSON handling.

Models sometimes wrap JSON in ```json fences or add a sentence before it, so
`extract_json` is forgiving about the envelope but strict about the content:
anything that does not validate is rejected and the caller retries once.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .config import DIFFICULTIES, QUESTION_TYPES

log = logging.getLogger("examnotes.schemas")

QuestionType = Literal["multiple_choice", "short_answer", "long_answer"]
Difficulty = Literal["easy", "medium", "hard"]


# --- Final result ----------------------------------------------------------

def _coerce_page(v: Any) -> int:
    """Models occasionally return "p. 12", "12-13", null or 0 for a page."""
    if v is None or v == "":
        return 1
    if isinstance(v, str):
        digits = re.search(r"\d+", v)
        v = int(digits.group()) if digits else 1
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return 1


class NoteGroup(BaseModel):
    topic: str
    points: list[str] = Field(default_factory=list)


class Question(BaseModel):
    type: QuestionType
    question: str
    options: list[str] | None = None        # multiple_choice only
    answer: str
    page: int = 1
    difficulty: Difficulty = "medium"

    @field_validator("page", mode="before")
    @classmethod
    def _fix_page(cls, v: Any) -> int:
        return _coerce_page(v)

    @model_validator(mode="after")
    def _options_match_type(self) -> "Question":
        """Keep options and type consistent instead of failing the whole batch."""
        if self.type == "multiple_choice":
            opts = [o for o in (self.options or []) if str(o).strip()]
            if len(opts) < 2:
                # Not really a multiple-choice question - treat it as short answer.
                object.__setattr__(self, "type", "short_answer")
                object.__setattr__(self, "options", None)
            else:
                object.__setattr__(self, "options", opts)
        else:
            object.__setattr__(self, "options", None)
        return self


class Result(BaseModel):
    """Exactly the JSON the API returns under "result"."""

    notes: list[NoteGroup] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)


# --- Intermediate shapes (one per chunk) -----------------------------------

class KeyPoint(BaseModel):
    topic: str = "General"
    point: str
    page: int = 1

    @field_validator("page", mode="before")
    @classmethod
    def _fix_page(cls, v: Any) -> int:
        return _coerce_page(v)


class TestableItem(BaseModel):
    kind: str = "fact"            # definition | formula | comparison | process | worked_example | objective
    content: str
    page: int = 1

    @field_validator("page", mode="before")
    @classmethod
    def _fix_page(cls, v: Any) -> int:
        return _coerce_page(v)


class ChunkExtraction(BaseModel):
    key_points: list[KeyPoint] = Field(default_factory=list)
    testable_items: list[TestableItem] = Field(default_factory=list)


class ChatReply(BaseModel):
    """The follow-up chat endpoint's reply (Markdown text inside JSON)."""

    reply: str


def _keep_valid(raw: Any, model_cls: type[BaseModel], label: str) -> list:
    """
    Validate a list item by item, dropping the ones that do not fit.

    One malformed question should not throw away a whole batch of good ones.
    If every item fails we raise instead, so the caller retries the call.
    """
    if not isinstance(raw, list):
        return raw                      # let pydantic report the real problem
    kept, dropped = [], 0
    for item in raw:
        try:
            kept.append(model_cls.model_validate(item))
        except ValidationError:
            dropped += 1
    if dropped:
        log.warning("Dropped %d invalid %s from the model reply", dropped, label)
    if raw and not kept:
        raise ValueError(f"None of the {label} returned were usable.")
    return kept


class NotesOnly(BaseModel):
    notes: list[NoteGroup] = Field(default_factory=list)

    @field_validator("notes", mode="before")
    @classmethod
    def _drop_bad_groups(cls, v: Any) -> Any:
        return _keep_valid(v, NoteGroup, "note groups")


class QuestionsOnly(BaseModel):
    questions: list[Question] = Field(default_factory=list)

    @field_validator("questions", mode="before")
    @classmethod
    def _drop_bad_questions(cls, v: Any) -> Any:
        return _keep_valid(v, Question, "questions")


# --- JSON extraction -------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict:
    """
    Pull a JSON object out of a model reply.

    Handles bare JSON, ```json fences, and a JSON object with prose around it.
    Raises ValueError if nothing parses.
    """
    if not text or not text.strip():
        raise ValueError("The AI returned an empty reply.")

    candidates: list[str] = []
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)

    # Last resort: the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}

    raise ValueError("The AI reply was not valid JSON.")


def parse_model(text: str, model_cls: type[BaseModel]) -> BaseModel:
    """Extract JSON and validate it against `model_cls`."""
    return model_cls.model_validate(extract_json(text))


def clamp_pages(result: Result, page_count: int) -> Result:
    """Make sure no question points at a page that does not exist."""
    for q in result.questions:
        if q.page > page_count:
            q.page = page_count
    return result


__all__ = [
    "ChatReply", "ChunkExtraction", "Difficulty", "KeyPoint", "NoteGroup", "NotesOnly",
    "Question", "QuestionType", "QuestionsOnly", "Result", "TestableItem",
    "clamp_pages", "extract_json", "parse_model", "DIFFICULTIES", "QUESTION_TYPES",
]
