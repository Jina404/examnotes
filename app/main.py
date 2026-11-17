"""
FastAPI application: wiring only.

Serves the single-page frontend at "/" and the JSON API under "/api".
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import store
from .api import router
from .config import STATIC_DIR
from .errors import ExamNotesError

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("examnotes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the database and clear anything older than the retention window."""
    store.init_db()
    cleared = store.purge_expired()
    if cleared:
        log.info("Purged %d expired document(s) on startup", cleared)
    yield


app = FastAPI(
    title="ExamNotes",
    description="Turn lecture-note PDFs into revision notes and exam questions.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(ExamNotesError)
async def handle_app_error(request: Request, exc: ExamNotesError) -> JSONResponse:
    """Every deliberate error becomes {"error": "<message a student can read>"}."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Turn FastAPI's field errors into one readable sentence."""
    missing = [
        str(err["loc"][-1]) for err in exc.errors() if err["type"] == "missing"
    ]
    if missing:
        message = f"Missing required field(s): {', '.join(missing)}."
    else:
        message = "That request was not valid. Please check the form and try again."
    return JSONResponse(status_code=400, content={"error": message})


app.include_router(router, prefix="/api")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
