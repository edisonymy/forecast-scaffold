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


def test_evaluate_alarms_on_open_questions_with_no_journal_row_ever() -> None:
    alarmed, reason = alarm.evaluate(None, 4, 0.5, NOW, run_gap_hours=2.0)

    assert alarmed
    assert "4 open question(s)" in reason
    assert "no journal row ever" in reason


def test_evaluate_alarms_on_journal_silence() -> None:
    stale = NOW - timedelta(hours=10)

    alarmed, reason = alarm.evaluate(stale, 2, 0.5, NOW, silence_hours=6.0, run_gap_hours=2.0)

    assert alarmed
    assert "2 open question(s)" in reason
    assert "10.0h" in reason


def test_evaluate_ok_when_journal_recent() -> None:
    recent = NOW - timedelta(hours=1)

    alarmed, reason = alarm.evaluate(recent, 2, 0.5, NOW, silence_hours=6.0, run_gap_hours=2.0)

    assert not alarmed
    assert reason.startswith("ok:")


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


def test_evaluate_ok_when_run_age_unknown_and_journal_recent() -> None:
    recent = NOW - timedelta(hours=1)

    alarmed, reason = alarm.evaluate(recent, 2, None, NOW, silence_hours=6.0)

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
