"""
HTTP routes. Thin layer: parse the request, call the pipeline, return JSON.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, Field

from . import llm, pipeline, ratelimit, store
from .config import (
    DEFAULT_DIFFICULTY,
    DEFAULT_QUESTION_COUNT,
    MAX_PAGES,
    MAX_QUESTION_COUNT,
    MAX_UPLOAD_BYTES,
    RETENTION_HOURS,
)
from .errors import ExpiredError, NotFoundError, UploadError

log = logging.getLogger("examnotes.api")
router = APIRouter()


def client_ip(request: Request) -> str:
    """Real client IP, honouring the proxy header used by hosts like Render."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _split_types(raw: str | None) -> list[str]:
    """question_types arrives as "short_answer,multiple_choice"."""
    if not raw:
        return []
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


@router.get("/health")
async def health() -> dict:
    """Liveness check, plus which provider the server is using."""
    return {
        "status": "ok",
        "provider": llm.provider_name(),
        "limits": {
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "max_pages": MAX_PAGES,
            "max_questions": MAX_QUESTION_COUNT,
            "retention_hours": RETENTION_HOURS,
        },
    }


@router.post("/documents")
async def create_document(
    request: Request,
    file: UploadFile = File(...),
    question_count: int = Form(DEFAULT_QUESTION_COUNT),
    question_types: str = Form(""),
    difficulty: str = Form(DEFAULT_DIFFICULTY),
) -> dict:
    """
    Upload a PDF and get notes + exam questions back.

    Everything happens in this request: validate, cache lookup, extract, chunk,
    model calls, merge, save.
    """
    ratelimit.check_and_record(client_ip(request))
    store.purge_expired()          # cheap housekeeping on every upload

    # Read at most one byte over the limit, so an oversized file is rejected
    # without pulling all of it into memory.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()             # drops the temporary upload file
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
            "Please upload a smaller PDF."
        )

    options = pipeline.normalise_options(
        question_count, _split_types(question_types), difficulty
    )
    outcome = await pipeline.process_upload(data, file.filename or "upload.pdf", options)
    return {**outcome, "options": options}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict:
    """Fetch a previously generated result."""
    row = store.get_document(doc_id)
    if row is None:
        raise NotFoundError("No document with that id. It may have been deleted.")
    if not row["result_json"]:
        raise ExpiredError()
    return {
        "document_id": row["id"],
        "result": store.row_result(row),
        "pages": row["page_count"],
        "filename": row["filename"],
        "created_at": row["created_at"],
        "cached": True,
    }


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/documents/{doc_id}/chat")
async def chat(doc_id: str, body: ChatRequest) -> dict:
    """Follow-up question about one document, answered from its stored text."""
    row = store.get_document(doc_id)
    if row is None:
        raise NotFoundError("No document with that id. Please upload the PDF again.")
    if not row["text_json"]:
        raise ExpiredError()

    reply = await pipeline.chat_about(
        store.row_pages(row), store.row_result(row), body.message.strip()
    )
    return {"document_id": doc_id, "reply": reply}
