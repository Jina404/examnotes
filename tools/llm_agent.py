"""
ExamNotes LLM agent - runs on YOUR machine, serves the HOSTED app.

The deployed copy of ExamNotes (on Render, say) has no API key. With
LLM_PROVIDER=remote it forwards every prompt to this little server, which runs
the Claude Code CLI you are already logged into and sends the text back.

    lecturer's browser -> Render -> HTTPS tunnel -> this agent -> claude -p

Run it from the project root, with a shared secret of your choosing:

    EXAMNOTES_AGENT_TOKEN=some-long-random-string \
    LLM_MODEL=haiku \
    .venv/bin/python -m tools.llm_agent

Then expose it (see the README) and give the hosted app the tunnel URL plus
the same token. The token is the only thing standing between the public
internet and your Claude Code quota, so make it long and keep it secret.
"""
from __future__ import annotations

import logging
import os
import secrets
import sys

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

# Reuse the project's own CLI plumbing: same command, same retry behaviour.
from app.errors import LLMError
from app.llm import _claude_cli_generate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("examnotes.agent")

TOKEN = (os.environ.get("EXAMNOTES_AGENT_TOKEN") or "").strip()
# Refuse to run wide open: without a token anyone who finds the URL could
# spend your Claude quota.
if not TOKEN:
    suggestion = secrets.token_urlsafe(32)
    sys.exit(
        "EXAMNOTES_AGENT_TOKEN is not set.\n"
        "Start the agent with a secret, for example:\n\n"
        f"    EXAMNOTES_AGENT_TOKEN={suggestion} .venv/bin/python -m tools.llm_agent\n\n"
        "Set the same value as LLM_REMOTE_TOKEN on the hosted app."
    )

app = FastAPI(title="ExamNotes LLM agent", version="1.0.0")


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=200_000)
    system: str | None = None


def _check(token: str | None) -> None:
    """Constant-time token check."""
    if not token or not secrets.compare_digest(token, TOKEN):
        log.warning("Rejected a request with a bad or missing token")
        raise HTTPException(status_code=401, detail="Bad token")


@app.get("/health")
async def health() -> dict:
    """Unauthenticated liveness check - says nothing useful to a stranger."""
    return {"status": "ok", "service": "examnotes-llm-agent"}


@app.post("/generate")
async def generate(
    body: GenerateRequest,
    x_examnotes_token: str | None = Header(default=None),
) -> dict:
    """Run one prompt through the local Claude Code CLI."""
    _check(x_examnotes_token)
    log.info("Prompt received (%d chars)", len(body.prompt))
    try:
        text = _claude_cli_generate(body.prompt, body.system)
    except LLMError as exc:
        # 503 so the hosted app retries rate limits and reports the rest.
        raise HTTPException(status_code=503, detail=exc.message) from exc
    log.info("Replied (%d chars)", len(text))
    return {"text": text}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("EXAMNOTES_AGENT_PORT", "8010"))
    log.info("Agent listening on 127.0.0.1:%d - expose it with a tunnel", port)
    uvicorn.run(app, host="127.0.0.1", port=port)
