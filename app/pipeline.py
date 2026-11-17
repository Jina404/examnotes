"""
The processing pipeline, start to finish.

Everything here runs inside the upload request - there is no job queue. The
only concurrency is a small pool of chunk extractions, because free AI tiers
do not like bursts.
"""
from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, ValidationError

from . import llm, prompts, store
from .chunking import chunk_pages
from .config import CHUNK_CONCURRENCY, MAX_QUESTION_COUNT
from .errors import LLMError
from .pdf import extract_pages, sha256_hex, validate_upload
from .schemas import (
    ChatReply,
    ChunkExtraction,
    NotesOnly,
    QuestionsOnly,
    Result,
    clamp_pages,
    parse_model,
)

log = logging.getLogger("examnotes.pipeline")

# Keep merge prompts to a sane size on a 100-page upload.
MAX_MERGE_CHARS = 40_000
MAX_CHAT_SOURCE_CHARS = 30_000


# --- One model call that must come back as JSON ----------------------------

async def ask_json(prompt: str, model_cls: type[BaseModel], system: str | None = None):
    """
    Ask the model, parse the JSON, validate it; on failure retry exactly once
    with a blunt reminder, then give up with a friendly error.
    """
    system = system or prompts.SYSTEM
    attempts = (prompt, prompt + prompts.RETRY_SUFFIX)
    last_error: Exception | None = None

    for i, attempt_prompt in enumerate(attempts):
        text = await asyncio.to_thread(llm.generate, attempt_prompt, system)
        try:
            return parse_model(text, model_cls)
        except (ValueError, ValidationError) as exc:
            last_error = exc
            log.warning("Invalid JSON from model (attempt %d): %s", i + 1, exc)

    raise LLMError(
        "The AI service returned an answer we could not read. Please try again."
    ) from last_error


# --- Steps -----------------------------------------------------------------

async def extract_items(chunks, semaphore: asyncio.Semaphore):
    """Step 5: run every chunk through the model, a few at a time."""
    failures: list[LLMError] = []

    async def one(chunk):
        async with semaphore:
            try:
                return await ask_json(
                    prompts.extract_chunk(chunk.text, chunk.page_range),
                    ChunkExtraction,
                )
            except LLMError as exc:
                # One bad chunk should not lose the whole document.
                log.warning("Chunk %s failed: %s", chunk.page_range, exc)
                failures.append(exc)
                return ChunkExtraction()

    results = await asyncio.gather(*(one(c) for c in chunks))

    # If *every* chunk failed, the AI service is the problem, not the PDF -
    # report that instead of telling the student their notes are empty.
    if chunks and len(failures) == len(chunks):
        raise failures[0]

    return results


def _trim(items: list[dict]) -> str:
    """Serialise merge input, dropping the tail if it would be enormous."""
    blob = json.dumps(items, ensure_ascii=False)
    if len(blob) <= MAX_MERGE_CHARS:
        return blob
    kept = items
    while kept and len(json.dumps(kept, ensure_ascii=False)) > MAX_MERGE_CHARS:
        kept = kept[: int(len(kept) * 0.9)]
    log.info("Trimmed merge input from %d to %d items", len(items), len(kept))
    return json.dumps(kept, ensure_ascii=False)


def _source_text(chunks) -> str:
    """Combined chunk text (with [Page N] markers), trimmed for a prompt."""
    joined = "\n\n".join(c.text for c in chunks)
    if len(joined) > MAX_MERGE_CHARS:
        joined = joined[:MAX_MERGE_CHARS] + "\n...[truncated]"
    return joined


async def merge_notes(extractions, chunks) -> NotesOnly:
    """Step 6 (merge A): all key points -> notes grouped by topic."""
    points = [kp.model_dump() for ex in extractions for kp in ex.key_points]
    if not points:
        # The model extracted no key points - derive notes from the text itself.
        log.info("No key points extracted; building notes from the page text")
        return await ask_json(prompts.notes_from_text(_source_text(chunks)), NotesOnly)
    return await ask_json(prompts.merge_notes(_trim(points)), NotesOnly)


async def merge_questions(extractions, options: dict, chunks) -> QuestionsOnly:
    """Step 7 (merge B): testable items -> exam questions. Never from the notes."""
    items = [ti.model_dump() for ex in extractions for ti in ex.testable_items]
    if not items:
        # No testable items - fall back to the page text so a sparse or
        # awkwardly-extracted PDF still yields questions.
        log.info("No testable items extracted; building questions from the page text")
        return await ask_json(
            prompts.questions_from_text(
                _source_text(chunks),
                options["question_count"],
                options["question_types"],
                options["difficulty"],
            ),
            QuestionsOnly,
        )
    return await ask_json(
        prompts.merge_questions(
            _trim(items),
            options["question_count"],
            options["question_types"],
            options["difficulty"],
        ),
        QuestionsOnly,
    )


# --- The whole thing -------------------------------------------------------

async def process_upload(data: bytes, filename: str, options: dict) -> dict:
    """
    Validate -> cache lookup -> extract -> chunk -> model -> merge -> save.

    Returns {"document_id": ..., "result": {...}, "cached": bool, "pages": int}.
    Raises UploadError / ScannedPdfError / LLMError with student-facing messages.
    """
    validate_upload(data, filename)

    digest = sha256_hex(data)
    opts_hash = store.options_hash(options)

    cached = store.find_cached(digest, opts_hash)
    if cached is not None:
        log.info("Cache hit for %s", digest[:12])
        return {
            "document_id": cached["id"],
            "result": store.row_result(cached),
            "cached": True,
            "pages": cached["page_count"],
            "filename": cached["filename"],
        }

    pages = extract_pages(data)                       # steps 3 (+ scanned check)
    chunks = chunk_pages(pages)                       # step 4
    log.info("Processing %s: %d pages, %d chunks", filename, len(pages), len(chunks))

    semaphore = asyncio.Semaphore(CHUNK_CONCURRENCY)
    extractions = await extract_items(chunks, semaphore)      # step 5

    notes, questions = await asyncio.gather(                  # steps 6 and 7
        merge_notes(extractions, chunks),
        merge_questions(extractions, options, chunks),
    )

    result = Result(notes=notes.notes, questions=questions.questions)
    result.questions = result.questions[: options["question_count"]]
    result = clamp_pages(result, len(pages))

    if not result.notes and not result.questions:
        raise LLMError(
            "The AI could not find anything teachable in this PDF. If it is mostly "
            "a title page, contents or admin text, try a PDF with more lecture "
            "content; otherwise please try again."
        )

    doc_id = store.save_document(                             # step 8
        sha256=digest,
        options_hash_value=opts_hash,
        filename=filename,
        page_count=len(pages),
        pages=pages,
        result=result.model_dump(),
    )
    # The uploaded bytes are never written to disk; `data` goes out of scope here.
    return {
        "document_id": doc_id,
        "result": result.model_dump(),
        "cached": False,
        "pages": len(pages),
        "filename": filename,
    }


async def chat_about(pages: list[str], result: dict, message: str) -> str:
    """Follow-up question, answered from the stored extracted text."""
    source = "\n\n".join(f"[Page {i + 1}]\n{t}" for i, t in enumerate(pages))
    if len(source) > MAX_CHAT_SOURCE_CHARS:
        source = source[:MAX_CHAT_SOURCE_CHARS] + "\n...[truncated]"
    reply = await ask_json(
        prompts.chat(message, json.dumps(result, ensure_ascii=False)[:8000], source),
        ChatReply,
    )
    return reply.reply


def normalise_options(
    question_count: int | None,
    question_types: list[str] | None,
    difficulty: str | None,
) -> dict:
    """Clean up the form fields so the cache key is stable and the prompt is sane."""
    from .config import (
        DEFAULT_DIFFICULTY,
        DEFAULT_QUESTION_COUNT,
        DIFFICULTIES,
        QUESTION_TYPES,
    )

    count = question_count or DEFAULT_QUESTION_COUNT
    count = max(1, min(int(count), MAX_QUESTION_COUNT))

    types = [t for t in (question_types or []) if t in QUESTION_TYPES]
    types = sorted(set(types)) or list(QUESTION_TYPES)

    diff = (difficulty or DEFAULT_DIFFICULTY).lower()
    if diff not in DIFFICULTIES and diff != "mixed":
        diff = DEFAULT_DIFFICULTY

    return {"question_count": count, "question_types": types, "difficulty": diff}
