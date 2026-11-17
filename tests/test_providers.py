"""The llm.py adapter: provider selection and the Claude CLI provider.

No real CLI is launched - subprocess.run is replaced in every test.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app import llm
from app.errors import LLMError
from app.schemas import NotesOnly, QuestionsOnly


# --- Provider selection ----------------------------------------------------

def test_defaults_to_fake_without_a_key(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert llm.provider_name() == "fake"


def test_defaults_to_gemini_when_a_key_is_set(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "x")
    assert llm.provider_name() == "gemini"


# --- Claude CLI command line ----------------------------------------------

def test_cli_command_includes_model_and_system(monkeypatch):
    monkeypatch.setenv("LLM_CLI_PATH", "/usr/bin/claude")
    monkeypatch.setenv("LLM_MODEL", "haiku")
    cmd = llm._claude_cli_command("be terse")

    assert cmd[0] == "/usr/bin/claude"
    assert "-p" in cmd                                  # non-interactive print mode
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--append-system-prompt") + 1] == "be terse"
    assert "--restricted" in cmd                        # no shell tools for this call


def test_cli_command_omits_model_when_unset(monkeypatch):
    monkeypatch.setenv("LLM_CLI_PATH", "/usr/bin/claude")
    monkeypatch.setenv("LLM_MODEL", "")
    assert "--model" not in llm._claude_cli_command(None)


def test_missing_cli_is_reported_clearly(monkeypatch):
    monkeypatch.delenv("LLM_CLI_PATH", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    with pytest.raises(LLMError, match="not found on PATH"):
        llm._claude_cli_command(None)


# --- Claude CLI behaviour --------------------------------------------------

def _fake_run(stdout="", stderr="", returncode=0, record=None):
    def runner(cmd, **kwargs):
        if record is not None:
            record.append((cmd, kwargs))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    return runner


def test_cli_returns_stdout_and_sends_the_prompt_on_stdin(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
    monkeypatch.setenv("LLM_CLI_PATH", "/usr/bin/claude")
    calls: list = []
    monkeypatch.setattr(subprocess, "run", _fake_run('{"notes": []}', record=calls))

    assert llm.generate("summarise this", system="sys") == '{"notes": []}'
    assert calls[0][1]["input"] == "summarise this"     # prompt goes on stdin
    assert calls[0][1]["timeout"] == llm.CLI_TIMEOUT_SECONDS


def test_cli_usage_limit_is_retried_then_reported_kindly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
    monkeypatch.setenv("LLM_CLI_PATH", "/usr/bin/claude")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)      # no real backoff
    calls: list = []
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run(stderr="Claude usage limit reached", returncode=1, record=calls),
    )

    with pytest.raises(LLMError, match="usage limit"):
        llm.generate("hi")
    assert len(calls) == llm.RATE_LIMIT_RETRIES          # it retried before giving up


def test_cli_other_failures_are_not_retried(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
    monkeypatch.setenv("LLM_CLI_PATH", "/usr/bin/claude")
    calls: list = []
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run(stderr="unknown flag --nope", returncode=2, record=calls),
    )

    with pytest.raises(LLMError, match="could not answer"):
        llm.generate("hi")
    assert len(calls) == 1


def test_cli_timeout_is_friendly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
    monkeypatch.setenv("LLM_CLI_PATH", "/usr/bin/claude")

    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(LLMError, match="did not answer within"):
        llm.generate("hi")


# --- Tolerant merges -------------------------------------------------------

def test_one_malformed_question_does_not_lose_the_others():
    good = {"type": "short_answer", "question": "Q", "answer": "A", "page": 2}
    bad = {"type": "short_answer", "question": "Q"}        # no answer
    kept = QuestionsOnly.model_validate({"questions": [good, bad, good]}).questions
    assert len(kept) == 2


def test_all_malformed_questions_raise_so_the_call_is_retried():
    with pytest.raises(Exception):
        QuestionsOnly.model_validate({"questions": [{"type": "short_answer"}]})


def test_malformed_note_group_is_dropped():
    notes = NotesOnly.model_validate(
        {"notes": [{"topic": "T", "points": ["p"]}, {"oops": True}]}
    ).notes
    assert len(notes) == 1 and notes[0].topic == "T"
