"""The remote provider (hosted app) and the local agent that answers it.

Nothing here touches the network or launches a real CLI.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.errors import LLMError

TOKEN = "test-token"


@pytest.fixture
def remote_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "remote")
    monkeypatch.setenv("LLM_REMOTE_URL", "https://agent.example.com/")
    monkeypatch.setenv("LLM_REMOTE_TOKEN", TOKEN)


def fake_post(status_code=200, json_body=None, record=None, error=None):
    def poster(url, **kwargs):
        if record is not None:
            record.append((url, kwargs))
        if error is not None:
            raise error
        return SimpleNamespace(
            status_code=status_code,
            json=lambda: json_body or {},
            text=str(json_body or ""),
        )
    return poster


# --- Configuration ---------------------------------------------------------

def test_missing_url_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "remote")
    monkeypatch.delenv("LLM_REMOTE_URL", raising=False)
    with pytest.raises(LLMError, match="LLM_REMOTE_URL"):
        llm.generate("hi")


def test_missing_token_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "remote")
    monkeypatch.setenv("LLM_REMOTE_URL", "https://agent.example.com")
    monkeypatch.delenv("LLM_REMOTE_TOKEN", raising=False)
    with pytest.raises(LLMError, match="LLM_REMOTE_TOKEN"):
        llm.generate("hi")


# --- Happy path ------------------------------------------------------------

def test_prompt_is_forwarded_with_the_token(monkeypatch, remote_env):
    calls: list = []
    monkeypatch.setattr(httpx, "post", fake_post(200, {"text": '{"notes": []}'}, calls))

    assert llm.generate("summarise", system="sys") == '{"notes": []}'
    url, kwargs = calls[0]
    assert url == "https://agent.example.com/generate"      # trailing slash trimmed
    assert kwargs["json"] == {"prompt": "summarise", "system": "sys"}
    assert kwargs["headers"]["x-examnotes-token"] == TOKEN


def test_empty_reply_is_an_error(monkeypatch, remote_env):
    monkeypatch.setattr(httpx, "post", fake_post(200, {"text": "   "}))
    with pytest.raises(LLMError, match="empty reply"):
        llm.generate("hi")


# --- Failures --------------------------------------------------------------

def test_bad_token_is_explained(monkeypatch, remote_env):
    monkeypatch.setattr(httpx, "post", fake_post(401, {"detail": "Bad token"}))
    with pytest.raises(LLMError, match="rejected this server's token"):
        llm.generate("hi")


def test_agent_offline_gives_a_demo_friendly_message(monkeypatch, remote_env):
    monkeypatch.setattr(
        httpx, "post", fake_post(error=httpx.ConnectError("connection refused"))
    )
    with pytest.raises(LLMError, match="offline"):
        llm.generate("hi")


def test_timeout_is_friendly(monkeypatch, remote_env):
    monkeypatch.setattr(httpx, "post", fake_post(error=httpx.ReadTimeout("slow")))
    with pytest.raises(LLMError, match="did not answer within"):
        llm.generate("hi")


def test_server_errors_are_retried(monkeypatch, remote_env):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls: list = []
    monkeypatch.setattr(httpx, "post", fake_post(503, {"detail": "busy"}, calls))

    with pytest.raises(LLMError):
        llm.generate("hi")
    assert len(calls) == llm.RATE_LIMIT_RETRIES


def test_usage_limit_from_the_agent_is_explained(monkeypatch, remote_env):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        httpx, "post", fake_post(503, {"detail": "Claude usage limit reached"})
    )
    with pytest.raises(LLMError, match="usage limit"):
        llm.generate("hi")


# --- The agent itself ------------------------------------------------------

@pytest.fixture
def agent(monkeypatch):
    """Import the agent with a known token, with the CLI call stubbed out."""
    monkeypatch.setenv("EXAMNOTES_AGENT_TOKEN", TOKEN)
    import tools.llm_agent as module

    module = importlib.reload(module)
    monkeypatch.setattr(module, "_claude_cli_generate", lambda p, s: f"echo:{p}")
    return module


def test_agent_health_needs_no_token(agent):
    res = TestClient(agent.app).get("/health")
    assert res.status_code == 200 and res.json()["status"] == "ok"


def test_agent_runs_the_prompt_with_a_good_token(agent):
    res = TestClient(agent.app).post(
        "/generate",
        json={"prompt": "hello", "system": None},
        headers={"x-examnotes-token": TOKEN},
    )
    assert res.status_code == 200
    assert res.json()["text"] == "echo:hello"


@pytest.mark.parametrize("headers", [{}, {"x-examnotes-token": "wrong"}])
def test_agent_rejects_a_missing_or_wrong_token(agent, headers):
    res = TestClient(agent.app).post(
        "/generate", json={"prompt": "hello"}, headers=headers
    )
    assert res.status_code == 401


def test_agent_reports_cli_failure_as_503(agent, monkeypatch):
    def boom(prompt, system):
        raise LLMError("Claude usage limit reached")

    monkeypatch.setattr(agent, "_claude_cli_generate", boom)
    res = TestClient(agent.app).post(
        "/generate", json={"prompt": "hi"}, headers={"x-examnotes-token": TOKEN}
    )
    assert res.status_code == 503 and "usage limit" in res.json()["detail"]


# --- The whole pipeline when the backend is unreachable --------------------

def test_backend_failure_is_reported_as_such_not_as_an_empty_pdf(
    monkeypatch, remote_env, sample_pdf
):
    """
    If every chunk call fails, the student must be told the service is down -
    not that their PDF has no teachable text.
    """
    import asyncio

    from app import pipeline

    monkeypatch.setattr(
        httpx, "post", fake_post(error=httpx.ConnectError("connection refused"))
    )
    options = pipeline.normalise_options(5, ["short_answer"], "medium")

    with pytest.raises(LLMError, match="offline"):
        asyncio.run(pipeline.process_upload(sample_pdf, "notes.pdf", options))
