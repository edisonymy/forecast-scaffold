"""The direct OpenRouter chat transport (bench/direct_agent.py).

A tool-less single completion straight to OpenRouter's native chat API — the cheap,
model-agnostic replacement for shelling out to the `claude` CLI on the bench's one-shot
cells. The contract under test: text + cost are parsed from the native response, the
system message is included only when given, a missing key fails loudly, and the one-retry
policy fires on transient status (429/5xx) but never on other 4xx (the error body's head
is surfaced instead). No live HTTP: the _post seam is stubbed in every test.
"""

from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import direct_agent  # noqa: E402


def ok_response(text: str = "hello world", cost: float | None = 0.0012,
                model: str = "google/gemini-2.5-pro") -> dict[str, Any]:
    usage: dict[str, Any] = {"prompt_tokens": 10, "completion_tokens": 5}
    if cost is not None:
        usage["cost"] = cost
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": usage,
    }


def http_error(code: int, body: str) -> urllib.error.HTTPError:
    """An HTTPError whose .read() yields `body` — mirrors what urlopen raises so the
    transport's status branching and error-body surfacing can be exercised offline."""
    return urllib.error.HTTPError(
        direct_agent.OPENROUTER_CHAT_URL, code, "err", {},
        io.BytesIO(body.encode("utf-8")),
    )


class ScriptedPost:
    """Replaces direct_agent._post: pops one canned result per call (a dict is returned,
    an Exception is raised) and records every call so the sent payload can be inspected."""

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, payload: dict, headers: dict, timeout: int) -> dict:
        self.calls.append({"url": url, "payload": payload, "headers": headers,
                           "timeout": timeout})
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # The retry path sleeps 5s; never wait in tests.
    monkeypatch.setattr(direct_agent.time, "sleep", lambda *_: None)


@pytest.fixture
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")


class TestHappyPath:
    def test_parses_text_and_cost_and_model(
        self, monkeypatch: pytest.MonkeyPatch, _key: None
    ) -> None:
        post = ScriptedPost([ok_response()])
        monkeypatch.setattr(direct_agent, "_post", post)
        text, cost, model = direct_agent.run_direct(
            "PROMPT", None, "google/gemini-2.5-pro", 60)
        assert text == "hello world"
        assert cost == pytest.approx(0.0012)
        assert model == "google/gemini-2.5-pro"
        assert len(post.calls) == 1  # no retry on success
        # the key rides in the Authorization header, and usage accounting is requested
        assert post.calls[0]["headers"]["Authorization"] == "Bearer sk-or-test"
        assert post.calls[0]["payload"]["usage"] == {"include": True}

    def test_missing_cost_field_falls_back_to_zero(
        self, monkeypatch: pytest.MonkeyPatch, _key: None
    ) -> None:
        post = ScriptedPost([ok_response(cost=None)])
        monkeypatch.setattr(direct_agent, "_post", post)
        _text, cost, _model = direct_agent.run_direct("P", None, "some/model", 60)
        assert cost == 0.0  # stamped 0, never guessed


class TestSystemMessage:
    def test_system_included_when_given(
        self, monkeypatch: pytest.MonkeyPatch, _key: None
    ) -> None:
        post = ScriptedPost([ok_response()])
        monkeypatch.setattr(direct_agent, "_post", post)
        direct_agent.run_direct("PROMPT", "SYSTEM TEXT", "some/model", 60)
        messages = post.calls[0]["payload"]["messages"]
        assert messages == [
            {"role": "system", "content": "SYSTEM TEXT"},
            {"role": "user", "content": "PROMPT"},
        ]

    def test_system_absent_when_none(
        self, monkeypatch: pytest.MonkeyPatch, _key: None
    ) -> None:
        post = ScriptedPost([ok_response()])
        monkeypatch.setattr(direct_agent, "_post", post)
        direct_agent.run_direct("PROMPT", None, "some/model", 60)
        messages = post.calls[0]["payload"]["messages"]
        assert messages == [{"role": "user", "content": "PROMPT"}]
        assert all(m["role"] != "system" for m in messages)


class TestMissingKey:
    def test_missing_key_raises_clear_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        # never reaches the network: the guard fires before _post
        post = ScriptedPost([ok_response()])
        monkeypatch.setattr(direct_agent, "_post", post)
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            direct_agent.run_direct("P", None, "some/model", 60)
        assert post.calls == []


class TestRetryPolicy:
    def test_429_retries_once_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, _key: None
    ) -> None:
        post = ScriptedPost([http_error(429, "rate limited"), ok_response(text="second")])
        monkeypatch.setattr(direct_agent, "_post", post)
        text, _cost, _model = direct_agent.run_direct("P", None, "some/model", 60)
        assert text == "second"
        assert len(post.calls) == 2  # original + one retry

    def test_500_retries_once(
        self, monkeypatch: pytest.MonkeyPatch, _key: None
    ) -> None:
        post = ScriptedPost([http_error(503, "upstream"), ok_response(text="ok")])
        monkeypatch.setattr(direct_agent, "_post", post)
        text, _cost, _model = direct_agent.run_direct("P", None, "some/model", 60)
        assert text == "ok"
        assert len(post.calls) == 2

    def test_400_does_not_retry_and_surfaces_body_head(
        self, monkeypatch: pytest.MonkeyPatch, _key: None
    ) -> None:
        body = '{"error": {"message": "google/gemini-2.5-pro is not a valid model id"}}'
        post = ScriptedPost([http_error(400, body)])
        monkeypatch.setattr(direct_agent, "_post", post)
        with pytest.raises(RuntimeError) as exc:
            direct_agent.run_direct("P", None, "google/gemini-2.5-pro", 60)
        assert len(post.calls) == 1  # no retry on a plain 4xx
        assert "400" in str(exc.value)
        assert "not a valid model id" in str(exc.value)  # the error body's head

    def test_repeated_transient_failure_finally_raises(
        self, monkeypatch: pytest.MonkeyPatch, _key: None
    ) -> None:
        post = ScriptedPost([http_error(429, "rl"), http_error(429, "still rl")])
        monkeypatch.setattr(direct_agent, "_post", post)
        with pytest.raises(RuntimeError, match="429"):
            direct_agent.run_direct("P", None, "some/model", 60)
        assert len(post.calls) == 2  # exactly one retry, no more
