"""Same-template prior facts reach the RESEARCH run's prompt only, and never block a run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_multirun import RESEARCH, fenced, reasoning_payload, run, run_bot

HEADER = "## Prior resolved questions of the same template"


def _seed(tmp_path: Path) -> None:
    (tmp_path / "j.jsonl").write_text(json.dumps({
        "id": "rec-9", "question": "Will X happen?", "question_type": "binary",
        "forecast_at": "2026-08-01T00:00:00+00:00", "probability": 0.4,
        "source": {"platform": "metaculus", "question_id": 9, "url": "https://m/q/9"},
    }) + "\n", encoding="utf-8")
    (tmp_path / "resolutions.jsonl").write_text(json.dumps({
        "question_id": 9, "record_id": "rec-9", "question_type": "binary",
        "title": "Will X happen?", "status": "resolved", "resolution_raw": "yes",
        "outcome": True, "annulled": False, "resolved_at": "2026-08-20T00:00:00Z",
    }) + "\n", encoding="utf-8")


class TestPriorFactsWiring:
    def test_off_by_default_even_with_a_matching_overlay(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        assert run_bot.PRIOR_FACTS_DEFAULT is False
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20, reasoning="lens1")),
            fenced(reasoning_payload(0.40, reasoning="lens2")),
        ])
        assert ok and record is not None
        assert all(HEADER not in call["prompt"] for call in agent.calls)

    def test_research_run_sees_prior_facts_reasoning_runs_do_not(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        monkeypatch.setattr(run_bot, "PRIOR_FACTS_DEFAULT", True)
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20, reasoning="lens1")),
            fenced(reasoning_payload(0.40, reasoning="lens2")),
        ])
        assert ok and record is not None
        assert HEADER in agent.calls[0]["prompt"]
        assert "-> resolved yes." in agent.calls[0]["prompt"]
        assert "submitted" not in agent.calls[0]["prompt"].split(HEADER)[1]
        assert HEADER not in agent.calls[1]["prompt"]
        assert HEADER not in agent.calls[2]["prompt"]

    def test_no_overlay_means_no_section_and_no_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(run_bot, "PRIOR_FACTS_DEFAULT", True)
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20, reasoning="lens1")),
            fenced(reasoning_payload(0.40, reasoning="lens2")),
        ])
        assert ok and record is not None
        assert HEADER not in agent.calls[0]["prompt"]

    def test_current_question_is_excluded_from_its_own_priors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        monkeypatch.setattr(run_bot, "PRIOR_FACTS_DEFAULT", True)
        # The seeded prior has question_id 9; the question under test has id 1 — give the
        # overlay a resolved row for id 1 too and check it is not fed back to itself.
        with (tmp_path / "resolutions.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "question_id": 1, "record_id": "rec-1", "question_type": "binary",
                "title": "Will X happen?", "status": "resolved", "resolution_raw": "no",
                "outcome": False, "annulled": False, "resolved_at": "2026-08-21T00:00:00Z",
            }) + "\n")
        with (tmp_path / "j.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": "rec-1", "question": "Will X happen?", "question_type": "binary",
                "forecast_at": "2026-08-02T00:00:00+00:00", "probability": 0.7,
                "source": {"platform": "metaculus", "question_id": 1, "url": "https://m/q/1"},
            }) + "\n")
        agent, _, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20, reasoning="lens1")),
            fenced(reasoning_payload(0.40, reasoning="lens2")),
        ])
        assert ok
        section = agent.calls[0]["prompt"].split(HEADER)[1]
        assert "-> resolved yes." in section       # the prior instance (qid 9)
        assert "-> resolved no." not in section    # the question's own row (qid 1)
