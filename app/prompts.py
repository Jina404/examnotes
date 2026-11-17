"""
All prompt text lives here so it can be read and tweaked in one place.

Every prompt demands a single JSON object and nothing else; schemas.py is
tolerant about fences and stray prose, but asking plainly keeps replies clean.
"""
from __future__ import annotations

import json

SYSTEM = (
    "You are a precise university teaching assistant. You read lecture notes and "
    "produce revision material for students. You never invent facts that are not "
    "in the source text. You always reply with a single valid JSON object and no "
    "other text, no explanation and no Markdown code fences."
)


def extract_chunk(chunk_text: str, page_range: str) -> str:
    """Step 5: key points + testable items for one chunk, with page numbers."""
    return f"""From the lecture-note extract below ({page_range}), pull out two things.

1. "key_points": the ideas a student must understand. Each has a short "topic"
   label, the "point" itself in one clear sentence, and the "page" number it
   came from.
2. "testable_items": things an exam could be built from - definitions,
   formulas, comparisons, processes, worked examples and stated learning
   objectives. Each has "kind" (one of: definition, formula, comparison,
   process, worked_example, objective), the "content" with enough detail to
   write a question and its answer from, and the "page" number.

Rules:
- Use only what the extract actually says.
- The page markers "[Page N]" tell you the page number for the text that
  follows them. Every item must carry the page it came from.
- Skip title slides, tables of contents, references and administrative text.
- If the extract contains nothing teachable, return empty lists.

Reply with exactly this JSON shape:
{{"key_points": [{{"topic": "...", "point": "...", "page": 1}}],
 "testable_items": [{{"kind": "definition", "content": "...", "page": 1}}]}}

--- EXTRACT ({page_range}) ---
{chunk_text}
--- END EXTRACT ---"""


def merge_notes(key_points_json: str) -> str:
    """Step 6 (merge A): group every key point into short notes by topic."""
    return f"""Below are key points extracted from one set of lecture notes, each with a
topic label and a page number.

Combine them into short revision notes grouped by topic:
- Merge duplicates and near-duplicates into one point.
- Keep each point to one sentence a student can revise from.
- Order topics the way they appear in the notes (lowest page first).
- Aim for 3 to 10 topics, each with 3 to 8 points. Do not invent new material.

Reply with exactly this JSON shape and nothing else:
{{"notes": [{{"topic": "...", "points": ["...", "..."]}}]}}

--- KEY POINTS ---
{key_points_json}
--- END KEY POINTS ---"""


def merge_questions(
    testable_items_json: str,
    question_count: int,
    question_types: list[str],
    difficulty: str,
) -> str:
    """Step 7 (merge B): questions built from the testable items only."""
    types_text = ", ".join(question_types)
    if difficulty == "mixed":
        difficulty_text = (
            "Mix easy, medium and hard questions, roughly evenly, and label each one."
        )
    else:
        difficulty_text = f'Make every question "{difficulty}" difficulty.'

    example = json.dumps(
        {
            "questions": [
                {
                    "type": "multiple_choice",
                    "question": "...",
                    "options": ["...", "...", "...", "..."],
                    "answer": "...",
                    "page": 12,
                    "difficulty": "medium",
                }
            ]
        }
    )

    return f"""Below is a list of testable items taken from one set of lecture notes.
Each has a kind, its content and the page it came from.

Write exactly {question_count} likely exam questions from these items, with a
model answer for each.

Rules:
- Build every question from the testable items, not from general knowledge.
- Allowed question types: {types_text}. Use only these, and when more than
  one type is allowed, spread the questions across them rather than using
  a single type for everything.
- {difficulty_text}
- "page" must be the page number of the item the question came from.
- multiple_choice questions need exactly 4 plausible "options", one of which is
  the correct answer, and "answer" must repeat that option's full text.
- short_answer model answers are 1-3 sentences. long_answer model answers are a
  short structured paragraph a student could write in an exam.
- Cover as many different items and pages as possible; do not ask the same
  thing twice.

Reply with exactly this JSON shape and nothing else:
{example}

--- TESTABLE ITEMS ---
{testable_items_json}
--- END TESTABLE ITEMS ---"""


def notes_from_text(source_text: str) -> str:
    """Fallback for merge A: build notes straight from the page text."""
    return f"""Below is the text of a set of lecture notes, with "[Page N]" markers
showing which page each part came from.

Write short revision notes grouped by topic:
- Use only what the text actually says; do not invent material.
- Each point is one sentence a student can revise from.
- Order topics as they appear (lowest page first).
- Aim for 3 to 8 topics, each with 3 to 8 points.
- Skip title slides, contents pages, references and administrative text.

Reply with exactly this JSON shape and nothing else:
{{"notes": [{{"topic": "...", "points": ["...", "..."]}}]}}

--- LECTURE TEXT ---
{source_text}
--- END LECTURE TEXT ---"""


def questions_from_text(
    source_text: str,
    question_count: int,
    question_types: list[str],
    difficulty: str,
) -> str:
    """Fallback for merge B: build questions straight from the page text."""
    types_text = ", ".join(question_types)
    if difficulty == "mixed":
        difficulty_text = (
            "Mix easy, medium and hard questions, roughly evenly, and label each one."
        )
    else:
        difficulty_text = f'Make every question "{difficulty}" difficulty.'

    example = json.dumps(
        {
            "questions": [
                {
                    "type": "multiple_choice",
                    "question": "...",
                    "options": ["...", "...", "...", "..."],
                    "answer": "...",
                    "page": 12,
                    "difficulty": "medium",
                }
            ]
        }
    )

    return f"""Below is the text of a set of lecture notes, with "[Page N]" markers
showing which page each part came from.

Write exactly {question_count} likely exam questions from this text, with a
model answer for each.

Rules:
- Base every question on what the text actually says, not general knowledge.
- Allowed question types: {types_text}. Use only these, and when more than one
  type is allowed, spread the questions across them.
- {difficulty_text}
- "page" must be the page number ("[Page N]") the question's content came from.
- multiple_choice questions need exactly 4 plausible "options", one correct,
  and "answer" must repeat that option's full text.
- short_answer answers are 1-3 sentences; long_answer answers are a short
  structured paragraph.
- Cover different pages and topics; do not repeat a question.

Reply with exactly this JSON shape and nothing else:
{example}

--- LECTURE TEXT ---
{source_text}
--- END LECTURE TEXT ---"""


def chat(message: str, notes_json: str, source_text: str) -> str:
    """Follow-up question about a document that was already processed."""
    return f"""A student has these revision notes and questions from their lecture notes,
and is asking a follow-up.

Answer using the source text below. If the answer is not in it, say so plainly.
If the student asks for more questions or different notes, write them out in
your reply as readable Markdown (headings, bullets, numbered questions with
answers), not as JSON.

Reply with exactly this JSON shape and nothing else:
{{"reply": "your answer as Markdown text"}}

--- STUDENT MESSAGE ---
{message}
--- CURRENT NOTES AND QUESTIONS ---
{notes_json}
--- SOURCE TEXT FROM THE PDF ---
{source_text}
--- END ---"""


RETRY_SUFFIX = (
    "\n\nIMPORTANT: your previous reply was not valid JSON of the requested "
    "shape. Reply again with ONLY the JSON object - no prose, no code fences."
)
