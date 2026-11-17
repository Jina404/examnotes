"""
The one place that talks to an AI provider.

The rest of the app only ever calls:

    generate(prompt, system=None) -> str

Which provider answers is decided by environment variables:

    LLM_PROVIDER   "gemini", "claude_cli", "remote" or "fake"
                   (defaults to gemini when a key is set, otherwise fake)
    LLM_API_KEY    the provider's API key (not needed by claude_cli)
    LLM_MODEL      the model id - never hardcoded, see .env.example

The "claude_cli" provider shells out to the Claude Code CLI in print mode
("claude -p"), reusing whatever login that terminal already has. Handy for
local testing without any API key; it cannot be deployed to a server.

The "remote" provider is how a DEPLOYED copy can still use your Claude Code:
it forwards the prompt over HTTPS to the little agent in tools/llm_agent.py
running on your own machine, which runs "claude -p" and sends the text back.

Every call in this app asks for JSON; parsing and validation happen in
schemas.py, so this module just returns the raw reply text.
"""
from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time

from .errors import LLMError

log = logging.getLogger("examnotes.llm")

# Short backoff: enough to ride out a free-tier per-minute limit, not so long
# that an upload hangs for ever.
RATE_LIMIT_RETRIES = 3
BACKOFF_SECONDS = (2.0, 8.0, 20.0)

FRIENDLY_RATE_LIMIT = (
    "The free AI service is busy right now (rate limit reached). "
    "Please wait a minute and try again."
)


# --- Provider selection ----------------------------------------------------

def provider_name() -> str:
    """Which provider is active. Falls back to 'fake' when no key is set."""
    explicit = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    return "gemini" if os.environ.get("LLM_API_KEY") else "fake"


def model_name() -> str:
    """The model id from the environment. Never a hardcoded default."""
    model = (os.environ.get("LLM_MODEL") or "").strip()
    if not model:
        raise LLMError(
            "No model configured. Set LLM_MODEL in your .env file "
            "(see .env.example for a current free-tier model id)."
        )
    return model


def generate(prompt: str, system: str | None = None) -> str:
    """
    Send one prompt to the configured provider and return its reply text.

    Blocking on purpose: the pipeline runs it in a worker thread so several
    chunks can be in flight at once.
    """
    name = provider_name()
    if name == "fake":
        return _fake_generate(prompt, system)
    if name == "gemini":
        return _gemini_generate(prompt, system)
    if name in ("claude_cli", "claude-cli"):
        return _claude_cli_generate(prompt, system)
    if name == "remote":
        return _remote_generate(prompt, system)
    raise LLMError(
        f"Unknown LLM_PROVIDER '{name}'. "
        "Use 'gemini', 'claude_cli', 'remote' or 'fake'."
    )


# --- Gemini ----------------------------------------------------------------

_client = None


def _gemini_client():
    """Build (once) a google-genai client. Imported lazily so tests need no SDK."""
    global _client
    if _client is not None:
        return _client

    api_key = (os.environ.get("LLM_API_KEY") or "").strip()
    if not api_key:
        raise LLMError(
            "No API key configured. Put LLM_API_KEY in your .env file, or set "
            "LLM_PROVIDER=fake to run with sample output."
        )
    try:
        from google import genai  # noqa: PLC0415 - optional dependency
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise LLMError(
            "The Gemini SDK is not installed. Run: pip install -U google-genai"
        ) from exc

    _client = genai.Client(api_key=api_key)
    return _client


def _text_is_rate_limit(text: str) -> bool:
    """Does this error text look like a quota / rate-limit refusal?"""
    text = (text or "").lower()
    return any(
        marker in text
        for marker in ("429", "resource_exhausted", "rate limit", "rate_limit",
                       "usage limit", "quota", "too many requests", "overloaded")
    )


def _is_rate_limit(exc: Exception) -> bool:
    """Spot a 429 / RESOURCE_EXHAUSTED across SDK versions and error shapes."""
    for attr in ("status_code", "code"):
        if getattr(exc, attr, None) in (429, "429"):
            return True
    return _text_is_rate_limit(f"{exc.__class__.__name__}: {exc}")


def _gemini_generate(prompt: str, system: str | None) -> str:
    """
    One call through the Gemini Interactions API, with retry on rate limits.

    Interactions is Google's current recommended interface (google-genai
    >= 2.3.0); `generateContent` still works but is now legacy.
    """
    client = _gemini_client()
    model = model_name()

    last_exc: Exception | None = None
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            kwargs: dict = {"model": model, "input": prompt}
            if system:
                kwargs["system_instruction"] = system
            interaction = client.interactions.create(**kwargs)
            text = getattr(interaction, "output_text", None)
            if not text:
                raise LLMError("The AI service returned an empty reply.")
            return text
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK raises provider-specific types
            last_exc = exc
            if _is_rate_limit(exc) and attempt < RATE_LIMIT_RETRIES - 1:
                delay = BACKOFF_SECONDS[attempt] + random.uniform(0, 1.0)
                log.warning("Gemini rate limited; retrying in %.1fs", delay)
                time.sleep(delay)
                continue
            break

    if last_exc is not None and _is_rate_limit(last_exc):
        raise LLMError(FRIENDLY_RATE_LIMIT) from last_exc
    raise LLMError(
        "The AI service could not be reached. Please try again in a moment."
    ) from last_exc


# --- Claude Code CLI (local testing, no API key) ---------------------------

# One model call can take a while through the CLI, so allow a generous wall
# clock before giving up.
CLI_TIMEOUT_SECONDS = float(os.environ.get("LLM_CLI_TIMEOUT", "300"))


def _claude_cli_command(system: str | None) -> list[str]:
    """Build the `claude -p` command line. Separate for easy testing."""
    exe = (os.environ.get("LLM_CLI_PATH") or "").strip() or shutil.which("claude")
    if not exe:
        raise LLMError(
            "The Claude Code CLI was not found on PATH. Install it, or set "
            "LLM_CLI_PATH to its full path, or use LLM_PROVIDER=fake."
        )

    cmd = [
        exe,
        "-p",                              # print mode: prompt in, answer out
        "--output-format", "text",
        "--restricted",                    # no shell/code tools for this call
        "--strict-mcp-config",             # ignore any MCP servers configured
        "--permission-prompts", "none",    # never block waiting for a human
    ]
    model = (os.environ.get("LLM_MODEL") or "").strip()
    if model:                              # empty = whatever the CLI defaults to
        cmd += ["--model", model]
    if system:
        cmd += ["--append-system-prompt", system]
    return cmd


def _claude_cli_generate(prompt: str, system: str | None) -> str:
    """
    Run one prompt through the Claude Code CLI already installed on this machine.

    Uses the terminal's own login, so no API key is involved. The prompt goes in
    on stdin (it is far too big for a command-line argument).
    """
    cmd = _claude_cli_command(system)

    last_error = ""
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT_SECONDS,
                cwd=tempfile.gettempdir(),   # run outside the project directory
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(
                f"The Claude CLI did not answer within {CLI_TIMEOUT_SECONDS:.0f}s. "
                "Try a shorter PDF, or raise LLM_CLI_TIMEOUT."
            ) from exc
        except OSError as exc:
            raise LLMError(f"Could not run the Claude CLI: {exc}") from exc

        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout

        last_error = (proc.stderr or proc.stdout or "").strip()[:500]
        if _text_is_rate_limit(last_error) and attempt < RATE_LIMIT_RETRIES - 1:
            delay = BACKOFF_SECONDS[attempt] + random.uniform(0, 1.0)
            log.warning("Claude CLI rate limited; retrying in %.1fs", delay)
            time.sleep(delay)
            continue
        break

    if _text_is_rate_limit(last_error):
        raise LLMError(
            "Your Claude Code usage limit has been reached. Please wait and try "
            "again, or switch LLM_PROVIDER."
        )
    raise LLMError(
        "The Claude CLI could not answer"
        + (f": {last_error}" if last_error else ".")
    )


# --- Remote agent (hosted app -> your own machine) -------------------------

REMOTE_TIMEOUT_SECONDS = float(os.environ.get("LLM_REMOTE_TIMEOUT", "300"))

OFFLINE_MESSAGE = (
    "The AI backend for this demo is offline. It runs on the author's computer, "
    "so it is only available while the demo is being shown. Please try again later."
)


def _remote_generate(prompt: str, system: str | None) -> str:
    """
    Forward one prompt to the agent running on the author's machine.

    The hosted app holds no API key: it only knows a URL and a shared token.
    """
    import httpx  # noqa: PLC0415 - already a dependency, imported lazily

    url = (os.environ.get("LLM_REMOTE_URL") or "").strip().rstrip("/")
    token = (os.environ.get("LLM_REMOTE_TOKEN") or "").strip()
    if not url:
        raise LLMError(
            "LLM_REMOTE_URL is not set. Point it at the tunnel URL of the agent "
            "in tools/llm_agent.py."
        )
    if not token:
        raise LLMError("LLM_REMOTE_TOKEN is not set (it must match the agent's).")

    payload = {"prompt": prompt, "system": system}
    headers = {"x-examnotes-token": token}

    last_detail = ""
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            response = httpx.post(
                f"{url}/generate",
                json=payload,
                headers=headers,
                timeout=REMOTE_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"The AI backend did not answer within "
                f"{REMOTE_TIMEOUT_SECONDS:.0f}s. Please try again."
            ) from exc
        except httpx.HTTPError as exc:          # DNS, refused, tunnel down...
            raise LLMError(OFFLINE_MESSAGE) from exc

        if response.status_code == 200:
            text = (response.json() or {}).get("text", "")
            if not text.strip():
                raise LLMError("The AI backend returned an empty reply.")
            return text

        if response.status_code in (401, 403):
            raise LLMError(
                "The AI backend rejected this server's token. Check that "
                "LLM_REMOTE_TOKEN matches the one the agent was started with."
            )

        last_detail = response.text[:300]
        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt < RATE_LIMIT_RETRIES - 1:
            delay = BACKOFF_SECONDS[attempt] + random.uniform(0, 1.0)
            log.warning("Remote agent busy (%s); retrying in %.1fs",
                        response.status_code, delay)
            time.sleep(delay)
            continue
        break

    if _text_is_rate_limit(last_detail):
        raise LLMError(
            "The demo's Claude usage limit has been reached. Please try again later."
        )
    raise LLMError(
        "The AI backend could not answer"
        + (f": {last_detail}" if last_detail else ".")
    )


# --- Fake (tests, and anyone running without a key) ------------------------

# One fixed, valid JSON document. Every prompt in this app asks for JSON, and
# each caller validates only the keys it needs, so this single reply satisfies
# chunk extraction, both merge steps and the chat endpoint.
_FAKE_REPLY = {
    "key_points": [
        {"topic": "Sample Topic", "point": "Photosynthesis converts light energy into chemical energy.", "page": 1},
        {"topic": "Sample Topic", "point": "The light-dependent reactions occur in the thylakoid membrane.", "page": 2},
    ],
    "testable_items": [
        {"kind": "definition", "content": "Photosynthesis: conversion of light energy into glucose.", "page": 1},
        {"kind": "process", "content": "The Calvin cycle fixes CO2 into sugar.", "page": 2},
    ],
    "notes": [
        {
            "topic": "Sample Topic",
            "points": [
                "Photosynthesis converts light energy into chemical energy.",
                "The light-dependent reactions occur in the thylakoid membrane.",
            ],
        }
    ],
    "questions": [
        {
            "type": "multiple_choice",
            "question": "Where do the light-dependent reactions take place?",
            "options": ["Thylakoid membrane", "Stroma", "Nucleus", "Ribosome"],
            "answer": "Thylakoid membrane",
            "page": 2,
            "difficulty": "easy",
        },
        {
            "type": "short_answer",
            "question": "Define photosynthesis.",
            "answer": "The conversion of light energy into chemical energy stored as glucose.",
            "page": 1,
            "difficulty": "medium",
        },
    ],
    "reply": "This is the offline sample provider. Set LLM_PROVIDER=gemini and an API key for real answers.",
}


def _fake_generate(prompt: str, system: str | None = None) -> str:
    """Return fixed, valid JSON - no network, no key, fully deterministic."""
    return json.dumps(_FAKE_REPLY)


def reset_client_cache() -> None:
    """Drop the cached SDK client (used by tests that change the environment)."""
    global _client
    _client = None
