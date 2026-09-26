"""Tests for the tournament-bot outage alarm (scripts/journal_alarm.py).

Covers the three pure/network-adjacent pieces directly: ``newest_forecast_at`` against a
temp journal with a malformed line, ``evaluate``'s full decision matrix, and
``open_question_count`` with ``urllib.request.urlopen`` monkeypatched so no test touches
the network. ``last_successful_run_age_hours`` shells out to the ``gh`` CLI and the CLI
wiring in ``main`` are exercised only indirectly through those three, matching what the
task asked to unit-test.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import journal_alarm as alarm  # noqa: E402

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _row(**over: object) -> dict[str, object]:
    value: dict[str, object] = {
        "forecast_at": "2026-08-01T00:00:00+00:00",
        "source": {"platform": "metaculus", "question_id": 1, "url": "https://x"},
    }
    value.update(over)
    return value


# -- newest_forecast_at -------------------------------------------------------


def test_newest_forecast_at_ignores_malformed_and_non_metaculus_rows(tmp_path: Path) -> None:
    journal = tmp_path / "forecasts.jsonl"
    journal.write_text(
        "\n".join(
            [
                json.dumps(_row(forecast_at="2026-08-01T00:00:00+00:00")),
                "{not valid json at all",
                json.dumps(
                    _row(
                        forecast_at="2026-08-03T00:00:00+00:00",
                        source={"platform": "manifold"},
                    )
                ),
                json.dumps(_row(forecast_at="2026-08-02T12:00:00+00:00")),
                "",
            ]
        ),
        encoding="utf-8",
    )

    newest = alarm.newest_forecast_at(journal)

    # The manifold row is later but must not count; the malformed line must not raise.
    assert newest == datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def test_newest_forecast_at_missing_file_returns_none(tmp_path: Path) -> None:
    assert alarm.newest_forecast_at(tmp_path / "nope.jsonl") is None


def test_newest_forecast_at_empty_file_returns_none(tmp_path: Path) -> None:
    journal = tmp_path / "forecasts.jsonl"
    journal.write_text("", encoding="utf-8")

    assert alarm.newest_forecast_at(journal) is None


# -- evaluate ------------------------------------------------------------------


def test_evaluate_alarms_on_stale_run() -> None:
    alarmed, reason = alarm.evaluate(NOW, 3, 5.0, NOW, run_gap_hours=2.0)

    assert alarmed
    assert "no successful bot run for 5.0h" in reason


def test_evaluate_alarms_on_unforecast_open_questions_even_with_a_recent_journal() -> None:
    # open_count means "open, never forecast by the bot, past the grace window": a
    # coverage failure regardless of how recently some OTHER question was journaled.
    recent = NOW - timedelta(minutes=5)

    alarmed, reason = alarm.evaluate(recent, 2, 0.5, NOW, run_gap_hours=2.0)

    assert alarmed
    assert "2 open question(s) still have no forecast" in reason


def test_evaluate_ok_when_no_open_questions_even_if_journal_silent() -> None:
    stale = NOW - timedelta(days=3)

    alarmed, reason = alarm.evaluate(stale, 0, 0.5, NOW, silence_hours=6.0, run_gap_hours=2.0)

    assert not alarmed
    assert reason.startswith("ok:")


def test_evaluate_skips_silence_check_when_open_count_unknown() -> None:
    # open_count == -1 ("couldn't reach Metaculus") must never be treated as "0 open".
    alarmed, reason = alarm.evaluate(None, -1, 0.5, NOW, run_gap_hours=2.0)

    assert not alarmed
    assert "unknown" in reason


def test_evaluate_ok_when_run_age_unknown_and_everything_forecast() -> None:
    alarmed, reason = alarm.evaluate(NOW, 0, None, NOW)

    assert not alarmed
    assert "unknown" in reason


# -- open_question_count -------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_open_question_count_sums_direct_and_group_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "results": [
            {"question": {"status": "open"}},
            {"question": {"status": "closed"}},
            {
                "group_of_questions": {
                    "questions": [
                        {"status": "open"},
                        {"status": "open"},
                        {"status": "resolved"},
                    ]
                }
            },
        ]
    }

    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    assert alarm.open_question_count(["minibench"]) == 3


def test_open_question_count_sums_across_multiple_slugs(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"results": [{"question": {"status": "open"}}]}

    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    assert alarm.open_question_count(["minibench", "other-slug"]) == 2


def test_open_question_count_ignores_blank_slugs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        calls.append(request.full_url)  # type: ignore[attr-defined]
        return _FakeResponse({"results": []})

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    assert alarm.open_question_count(["", "  ", "minibench"]) == 0
    assert len(calls) == 1


def test_open_question_count_returns_unknown_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    assert alarm.open_question_count(["minibench"]) == -1


def test_open_question_count_returns_unknown_on_malformed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BadResponse(_FakeResponse):
        def read(self) -> bytes:
            return b"not json"

    def fake_urlopen(request: object, timeout: float = 30) -> _BadResponse:
        return _BadResponse({})

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    assert alarm.open_question_count(["minibench"]) == -1


def test_open_question_count_skips_forecast_and_recently_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 21, 22, 0, tzinfo=UTC)
    payload = {"results": [
        # already forecast by the bot: fine
        {"question": {"status": "open", "open_time": "2026-09-21T10:00:00Z",
                      "my_forecasts": {"latest": {"start_time": 1}}}},
        # opened 30 min ago: inside the grace window, the bot is presumably on it
        {"question": {"status": "open", "open_time": "2026-09-21T21:30:00Z"}},
        # opened 3h ago and never forecast: THIS is the Sep 21 failure
        {"question": {"status": "open", "open_time": "2026-09-21T19:00:00Z"}},
        # no open_time at all: counts (fail loud)
        {"question": {"status": "open"}},
    ]}

    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    assert alarm.open_question_count(["minibench"], grace_minutes=120, now=now) == 2


def test_open_question_count_flags_imminent_unforecast_and_sends_with_cp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 21, 22, 0, tzinfo=UTC)
    urls: list[str] = []
    payload = {"results": [
        # opened 20 min ago but closes in 30: about to be missed
        {"question": {"status": "open", "open_time": "2026-09-21T21:40:00Z",
                      "scheduled_close_time": "2026-09-21T22:30:00Z"}},
        # opened 20 min ago, closes tomorrow: a healthy bot is still on it
        {"question": {"status": "open", "open_time": "2026-09-21T21:40:00Z",
                      "scheduled_close_time": "2026-09-22T22:00:00Z"}},
    ]}

    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        urls.append(request.full_url)  # type: ignore[attr-defined]
        return _FakeResponse(payload)

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    assert alarm.open_question_count(["minibench"], now=now) == 1
    # without with_cp the API omits my_forecasts and everything looks unforecast
    assert "with_cp=true" in urls[0]


def test_open_question_count_skips_a_slug_that_does_not_exist_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        if "market-pulse-26q4" in request.full_url:  # type: ignore[attr-defined]
            raise urllib.error.HTTPError(request.full_url, 400, "no such tournament",  # type: ignore[attr-defined]
                                         {}, None)  # type: ignore[arg-type]
        return _FakeResponse({"results": [{"question": {"status": "open"}}]})

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    assert alarm.open_question_count(["minibench", "market-pulse-26q4"]) == 1


def test_rejected_token_is_an_alarm_not_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden",  # type: ignore[attr-defined]
                                     {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)

    count = alarm.open_question_count(["minibench"])
    assert count == alarm.AUTH_REJECTED
    alarmed, reason = alarm.evaluate(NOW, count, 0.5, NOW)
    assert alarmed and "401/403" in reason


def test_open_question_count_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2026-09-26: only the first 100 open posts were ever read.
    offsets: list[str] = []

    def fake_urlopen(request: object, timeout: float = 30) -> _FakeResponse:
        url = request.full_url  # type: ignore[attr-defined]
        offset = url.split("offset=")[1].split("&")[0]
        offsets.append(offset)
        page = {"results": [{"question": {"status": "open"}}]}
        if offset == "0":
            page["next"] = "page-2"
        return _FakeResponse(page)

    monkeypatch.setattr(alarm.urllib.request, "urlopen", fake_urlopen)
    assert alarm.open_question_count(["season"], skip=set()) == 2
    assert offsets == ["0", "100"]


def test_open_question_count_ignores_banned_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"results": [
        {"id": 45516, "question": {"id": 1, "status": "open"}},  # banned post
        {"id": 7, "question": {"id": 99, "status": "open"}},  # banned question id
        {"id": 8, "question": {"id": 2, "status": "open"}},
    ]}
    monkeypatch.setattr(alarm.urllib.request, "urlopen",
                        lambda request, timeout=30: _FakeResponse(payload))
    monkeypatch.setenv("SKIP_POSTS", "45516, 99")
    assert alarm.open_question_count(["season"]) == 1


# -- last_successful_run_age_hours ---------------------------------------------


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.returncode = 0
        self.stdout = stdout


def test_run_age_filters_success_client_side(monkeypatch: pytest.MonkeyPatch) -> None:
    # The server-side --status filter lagged by a day on 2026-09-25 (false alarms).
    now = datetime.now(UTC)
    rows = [
        {"conclusion": "", "updatedAt": now.isoformat()},  # in progress
        {"conclusion": "failure", "updatedAt": (now - timedelta(minutes=10)).isoformat()},
        {"conclusion": "success", "updatedAt": (now - timedelta(minutes=30)).isoformat()},
    ]
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> _Completed:
        seen.append(cmd)
        return _Completed(json.dumps(rows))

    monkeypatch.setattr(alarm.subprocess, "run", fake_run)
    age = alarm.last_successful_run_age_hours()
    assert age is not None and 0.45 < age < 0.55
    assert "--status" not in seen[0]


def test_run_age_with_no_success_listed_is_a_lower_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    rows = [{"conclusion": "failure", "updatedAt": (now - timedelta(hours=h)).isoformat()}
            for h in (1, 5, 17)]
    monkeypatch.setattr(alarm.subprocess, "run",
                        lambda cmd, **kw: _Completed(json.dumps(rows)))
    age = alarm.last_successful_run_age_hours()
    assert age is not None and age > 16.9
