"""Tests for the optional AskNews research source (bot/asknews.py) and its wiring.

Everything here is offline: urllib is stubbed, so no test touches the AskNews API. Covered:
the env->keyfile->key.txt key precedence, search_news success / no-key / retry / failure,
news_section formatting (dated linked articles, no injected yes/no lean) + dedupe + cap +
disabled/no-key/failure -> "", the run_bot RESEARCH-run brief gaining the section only when
enabled+stubbed (and reasoning runs never getting it), and a COMPLIANCE guard that
bot/run_manifold.py neither imports asknews nor carries the section — the AskNews key is
licensed for the Metaculus competition only (operator, 2026-07-11), so Manifold use is out.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

import asknews  # noqa: E402
import run_bot  # noqa: E402
import run_manifold  # noqa: E402

from forecast_scaffold.core import DEFAULTS, Journal  # noqa: E402

# --------------------------------------------------------------------------- urllib stubs


class _Resp:
    """Minimal context-manager response with .read(), returning canned JSON bytes."""

    def __init__(self, payload: Any) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _by_strategy(mapping: dict[str, list[dict[str, Any]]], calls: list[str] | None = None):
    """A fake urlopen that routes on the ``strategy`` query param to a canned as_dicts list."""

    def fake(request: Any, timeout: Any = None) -> _Resp:
        if calls is not None:
            calls.append(request.full_url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        strat = (query.get("strategy") or [""])[0]
        return _Resp({"as_dicts": mapping.get(strat, [])})

    return fake


def _article(n: int, url: str, *, summary: str = "The central bank held its policy rate.",
             pub_date: str = "2026-07-10T14:09:55Z", source_id: str = "Reuters") -> dict[str, Any]:
    return {"title": f"Headline number {n}", "summary": summary, "pub_date": pub_date,
            "source_id": source_id, "article_url": url}


# --------------------------------------------------------------------------- key precedence


class TestKeyLookup:
    """asknews_key mirrors run_manifold.manifold_api_key: env -> keyfile -> key.txt -> ""."""

    def test_env_var_wins_over_keyfile(self, monkeypatch: pytest.MonkeyPatch,
                                       tmp_path: Path) -> None:
        keyfile = tmp_path / "key"
        keyfile.write_text("filekey\n", encoding="utf-8")
        monkeypatch.setattr(asknews, "KEYFILE", keyfile)
        monkeypatch.setenv("ASKNEWS_API_KEY", "  envkey  ")
        assert asknews.asknews_key() == "envkey"

    def test_plain_keyfile_when_env_empty(self, monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
        keyfile = tmp_path / "key"
        keyfile.write_text("filekey\n", encoding="utf-8")
        monkeypatch.setattr(asknews, "KEYFILE", keyfile)
        monkeypatch.delenv("ASKNEWS_API_KEY", raising=False)
        assert asknews.asknews_key() == "filekey"

    def test_key_txt_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Notepad appends .txt; asknews_key must accept ~/.asknews/key.txt too.
        keyfile = tmp_path / "key"  # no plain file written
        (tmp_path / "key.txt").write_text("txtkey\n", encoding="utf-8")
        monkeypatch.setattr(asknews, "KEYFILE", keyfile)
        monkeypatch.delenv("ASKNEWS_API_KEY", raising=False)
        assert asknews.asknews_key() == "txtkey"

    def test_plain_keyfile_precedes_key_txt(self, monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
        keyfile = tmp_path / "key"
        keyfile.write_text("filekey\n", encoding="utf-8")
        (tmp_path / "key.txt").write_text("txtkey\n", encoding="utf-8")
        monkeypatch.setattr(asknews, "KEYFILE", keyfile)
        monkeypatch.delenv("ASKNEWS_API_KEY", raising=False)
        assert asknews.asknews_key() == "filekey"

    def test_no_key_anywhere_returns_empty(self, monkeypatch: pytest.MonkeyPatch,
                                           tmp_path: Path) -> None:
        monkeypatch.setattr(asknews, "KEYFILE", tmp_path / "absent")
        monkeypatch.delenv("ASKNEWS_API_KEY", raising=False)
        assert asknews.asknews_key() == ""


# --------------------------------------------------------------------------- search_news


class TestSearchNews:
    def test_success_returns_as_dicts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ASKNEWS_API_KEY", "testkey")
        monkeypatch.setattr(asknews.urllib.request, "urlopen",
                            _by_strategy({"latest news": [_article(1, "https://ex.com/a")]}))
        got = asknews.search_news("q", strategy="latest news")
        assert [a["article_url"] for a in got] == ["https://ex.com/a"]

    def test_no_key_never_calls_network(self, monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
        monkeypatch.setattr(asknews, "KEYFILE", tmp_path / "absent")
        monkeypatch.delenv("ASKNEWS_API_KEY", raising=False)
        calls: list[str] = []
        monkeypatch.setattr(asknews.urllib.request, "urlopen", _by_strategy({}, calls))
        assert asknews.search_news("q") == []
        assert calls == []  # dark by default: no key -> no request

    def test_retries_once_on_5xx_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ASKNEWS_API_KEY", "testkey")
        slept: list[float] = []
        monkeypatch.setattr(asknews.time, "sleep", lambda s: slept.append(s))
        state = {"n": 0}

        def flaky(request: Any, timeout: Any = None) -> _Resp:
            if state["n"] == 0:
                state["n"] += 1
                raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)  # type: ignore[arg-type]
            return _Resp({"as_dicts": [_article(1, "https://ex.com/a")]})

        monkeypatch.setattr(asknews.urllib.request, "urlopen", flaky)
        got = asknews.search_news("q")
        assert [a["article_url"] for a in got] == ["https://ex.com/a"]
        assert slept == [asknews.RETRY_WAIT_S]  # exactly one retry wait

    def test_non_retryable_error_returns_empty_without_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASKNEWS_API_KEY", "testkey")
        slept: list[float] = []
        monkeypatch.setattr(asknews.time, "sleep", lambda s: slept.append(s))

        def boom(request: Any, timeout: Any = None) -> _Resp:
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(asknews.urllib.request, "urlopen", boom)
        assert asknews.search_news("q") == []
        assert slept == []  # a transport error is not retried, and never raises

    def test_4xx_client_error_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ASKNEWS_API_KEY", "testkey")
        slept: list[float] = []
        monkeypatch.setattr(asknews.time, "sleep", lambda s: slept.append(s))

        def unauthorized(request: Any, timeout: Any = None) -> _Resp:
            raise urllib.error.HTTPError(request.full_url, 401, "nope", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(asknews.urllib.request, "urlopen", unauthorized)
        assert asknews.search_news("q") == []
        assert slept == []

    def test_malformed_reply_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ASKNEWS_API_KEY", "testkey")
        monkeypatch.setattr(asknews.urllib.request, "urlopen",
                            lambda request, timeout=None: _Resp({"unexpected": "shape"}))
        assert asknews.search_news("q") == []


# --------------------------------------------------------------------------- news_section

# A set of markers that would betray an injected preliminary yes/no lean — the anti-pattern
# we deliberately avoid. None may appear in a section we build from neutral articles.
_LEAN_MARKERS = ("lean", "my estimate", "i estimate", "probability:", "forecast:",
                 "likely yes", "likely no", "recommend", "verdict")


def _enable(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[dict[str, Any]]],
            calls: list[str] | None = None) -> None:
    monkeypatch.setenv("ASKNEWS_DISABLE", "0")
    monkeypatch.setenv("ASKNEWS_API_KEY", "testkey")
    monkeypatch.setattr(asknews.urllib.request, "urlopen", _by_strategy(mapping, calls))


class TestNewsSection:
    def test_formatting_has_title_date_url_and_no_lean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, {
            "latest news": [_article(1, "https://ex.com/a", source_id="Reuters")],
            "news knowledge": [_article(2, "https://ex.com/c", source_id="WSJ",
                                        pub_date="2026-06-01T00:00:00Z")],
        })
        section = asknews.news_section("Will the Fed cut rates?")
        # header frames it as STARTING material to verify and search beyond
        assert asknews.NEWS_HEADER in section
        assert "starting material; verify key claims and search beyond it" in section
        # winning-template pieces: bold title, summary, dated, linked source
        assert "**Headline number 1**" in section
        assert "Publish date: 2026-07-10" in section  # ISO timestamp -> date only
        assert "Publish date: 2026-06-01" in section
        assert "Source: [Reuters](https://ex.com/a)" in section
        assert "Source: [WSJ](https://ex.com/c)" in section
        # NO injected yes/no lean anywhere
        low = section.lower()
        for marker in _LEAN_MARKERS:
            assert marker not in low, marker

    def test_dedupes_by_url_hot_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shared = "https://ex.com/dup"
        _enable(monkeypatch, {
            "latest news": [_article(1, shared), _article(2, "https://ex.com/b")],
            "news knowledge": [_article(3, shared), _article(4, "https://ex.com/c")],
        })
        section = asknews.news_section("q")
        assert section.count(shared) == 1  # the duplicate URL appears once
        # all three distinct URLs present
        for url in (shared, "https://ex.com/b", "https://ex.com/c"):
            assert url in section

    def test_section_capped_on_article_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        big = "word " * 200  # ~1000-char summary; a handful blows past the ~4000 cap
        many = [_article(i, f"https://ex.com/{i}", summary=big) for i in range(20)]
        _enable(monkeypatch, {"latest news": many})
        section = asknews.news_section("q")
        assert 0 < len(section) <= asknews.MAX_SECTION_CHARS
        assert section.rstrip().endswith(")")  # ends on a complete Source-link block

    def test_disabled_returns_empty_without_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASKNEWS_DISABLE", "1")  # the kill switch
        monkeypatch.setenv("ASKNEWS_API_KEY", "testkey")
        calls: list[str] = []
        monkeypatch.setattr(asknews.urllib.request, "urlopen",
                            _by_strategy({"latest news": [_article(1, "https://ex.com/a")]},
                                         calls))
        assert asknews.news_section("q") == ""
        assert calls == []  # disabled -> no request at all

    def test_no_key_returns_empty(self, monkeypatch: pytest.MonkeyPatch,
                                  tmp_path: Path) -> None:
        monkeypatch.setenv("ASKNEWS_DISABLE", "0")
        monkeypatch.setattr(asknews, "KEYFILE", tmp_path / "absent")
        monkeypatch.delenv("ASKNEWS_API_KEY", raising=False)
        assert asknews.news_section("q") == ""

    def test_api_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ASKNEWS_DISABLE", "0")
        monkeypatch.setenv("ASKNEWS_API_KEY", "testkey")

        def boom(request: Any, timeout: Any = None) -> _Resp:
            raise urllib.error.URLError("down")

        monkeypatch.setattr(asknews.urllib.request, "urlopen", boom)
        assert asknews.news_section("q") == ""  # a research run must never fail on this

    def test_all_articles_unlinkable_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable(monkeypatch, {"latest news": [
            {"title": "no url here", "summary": "x", "pub_date": "2026-07-01"},
        ]})
        assert asknews.news_section("q") == ""  # nothing citable -> no section


# --------------------------------------------------------------------------- run_bot wiring


def _config(runs: int) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULTS))
    merged["tiers"] = {"medium": {"draws": 5, "searches": 5, "runs": runs}}
    return merged


class _Agent:
    """Captures every (cmd, prompt, system) and returns scripted fenced-JSON outputs."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        self.calls.append({"cmd": cmd, "prompt": prompt, "system": system})
        return self.outputs.pop(0), 0.05, "claude-sonnet-5"


class _Client:
    def community_prediction(self, question: dict[str, Any]) -> None:
        return None


def _fenced(payload: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


_POST = {"id": 1, "title": "Will X happen?"}
_QUESTION = {"id": 1, "type": "binary", "title": "Will X happen?",
             "resolution_criteria": "Resolves YES per source S.",
             "scheduled_resolve_time": "2026-12-15T00:00:00Z"}
_RESEARCH = {"probability": 0.30, "dossier": "compiled facts", "reasoning": "researched",
             "sources": []}
_REASONING = {"probability": 0.20, "reasoning": "x", "sources": [], "named_scenarios": []}


def _run_forecast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                  outputs: list[str]) -> _Agent:
    agent = _Agent(outputs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_bot, "verify_dossier", lambda *a, **k: ("", 0.0))
    args = argparse.Namespace(
        blind=False, effort="medium", provider="subscription", timeout=60,
        dry_run=True, comment=False, budget=0.0,
        agent_cmd=("claude -p --model claude-sonnet-5 --output-format json "
                   "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch"),
    )
    journal = Journal(str(tmp_path / "j.jsonl"))
    run_bot.forecast_question(_Client(), _POST, _QUESTION, args, _config(runs=2),
                              journal, {"usd": 0.0}, None)
    return agent


class TestRunBotWiring:
    def test_research_brief_gains_section_reasoning_runs_do_not(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _enable(monkeypatch, {
            "latest news": [_article(1, "https://ex.com/a")],
            "news knowledge": [_article(2, "https://ex.com/c")],
        })
        agent = _run_forecast(monkeypatch, tmp_path,
                              [_fenced(_RESEARCH), _fenced(_REASONING)])
        research_prompt = agent.calls[0]["prompt"]
        assert asknews.NEWS_HEADER in research_prompt
        assert "**Headline number 1**" in research_prompt
        # reasoning runs work from the dossier — they must NOT carry the news section
        for call in agent.calls[1:]:
            assert asknews.NEWS_HEADER not in call["prompt"]

    def test_research_brief_unchanged_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # autouse conftest already set ASKNEWS_DISABLE=1; confirm the brief is untouched.
        agent = _run_forecast(monkeypatch, tmp_path,
                              [_fenced(_RESEARCH), _fenced(_REASONING)])
        assert asknews.NEWS_HEADER not in agent.calls[0]["prompt"]
        assert "AskNews" not in agent.calls[0]["prompt"]


# --------------------------------------------------------------- run_manifold COMPLIANCE

class TestManifoldCompliance:
    """The AskNews key is licensed for the Metaculus competition only (operator, 2026-07-11);
    Manifold use is not permitted. Guard that run_manifold never imports asknews and no
    Manifold brief carries the section — a regression here would be a licensing breach."""

    def test_run_manifold_does_not_import_asknews(self) -> None:
        assert not hasattr(run_manifold, "asknews")
        source = Path(run_manifold.__file__).read_text(encoding="utf-8")
        assert "asknews" not in source.lower()

    def test_manifold_briefs_never_carry_the_section(self) -> None:
        market = {
            "id": "m1", "question": "Will the Fed cut rates at the next meeting?",
            "textDescription": "Resolves YES if the target range is lowered.",
            "probability": 0.42, "volume24Hours": 1000.0, "uniqueBettorCount": 40,
            "closeTime": 1_780_000_000_000, "url": "https://manifold.markets/x",
        }
        for sighted in (False, True):
            brief = run_manifold.build_manifold_brief(market, sighted=sighted)
            assert asknews.NEWS_HEADER not in brief
            assert "AskNews" not in brief
