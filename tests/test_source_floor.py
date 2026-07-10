"""The research-source floor (v0.4.5): the research (full) run must return at least
``tiers.*.min_sources`` distinct actually-consulted sources, announced in its system
prompt and enforced in the validate/repair loop BEFORE any forecast is accepted.

Provenance: the first live tournament batch put its most crowd-divergent calls on its
thinnest research — the MC/numeric single-run paths had no dossier contract, and q44381
recorded a confident forecast with zero sources. Reasoning runs stay exempt: they work
from a shared dossier and ``[]`` is an honest answer there.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

import run_bot  # noqa: E402

from forecast_scaffold.core import DEFAULTS, Journal  # noqa: E402

POST = {"id": 1, "title": "Will X happen?"}
BINARY_Q = {
    "id": 1,
    "type": "binary",
    "title": "Will X happen?",
    "resolution_criteria": "Resolves YES per source S.",
    "scheduled_close_time": "2026-12-01T00:00:00Z",
    "scheduled_resolve_time": "2026-12-15T00:00:00Z",
}
MC_Q = {
    "id": 2,
    "type": "multiple_choice",
    "title": "Which of A/B?",
    "options": ["A", "B"],
    "resolution_criteria": "Resolves to the winner.",
    "scheduled_close_time": "2026-12-01T00:00:00Z",
    "scheduled_resolve_time": "2026-12-15T00:00:00Z",
}


def fenced(payload: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


class ScriptedAgent:
    """Replaces run_bot.run_agent; returns scripted outputs and records every call."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        self.calls.append({"cmd": cmd, "prompt": prompt, "system": system})
        if not self.outputs:
            raise RuntimeError("script exhausted")
        return self.outputs.pop(0), 0.05, "claude-sonnet-5"


class StubClient:
    def community_prediction(self, question: dict[str, Any]) -> float:
        return 0.5


def tiers(min_sources: int, runs: int = 1) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULTS))
    merged["tiers"] = {"medium": {
        "draws": 5, "searches": 5, "runs": runs, "run_models": [],
        "min_sources": min_sources,
    }}
    return merged


def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outputs: list[str],
        config: dict[str, Any], question: dict[str, Any],
        ) -> tuple[ScriptedAgent, dict[str, Any] | None, bool]:
    agent = ScriptedAgent(outputs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_bot, "verify_dossier", lambda *a, **k: ("", 0.0))
    args = argparse.Namespace(
        blind=False, effort="medium", provider="subscription", timeout=60,
        dry_run=True, comment=False, budget=0.0,
        agent_cmd=("claude -p --model claude-sonnet-5 --output-format json "
                   "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch"),
    )
    journal_path = tmp_path / "j.jsonl"
    ok = run_bot.forecast_question(
        StubClient(), POST, question, args, config, Journal(str(journal_path)),
        {"usd": 0.0}, None,
    )
    record = None
    if journal_path.exists() and journal_path.read_text(encoding="utf-8").strip():
        record = json.loads(journal_path.read_text(encoding="utf-8").splitlines()[-1])
    return agent, record, ok


THIN_MC = {"probabilities": {"A": 0.6, "B": 0.4}, "reasoning": "from memory", "sources": []}
# reference_class is now a research-run requirement for MC (v0.4.6), so the "researched"
# fixture that stands in for a valid repaired payload carries one.
RESEARCHED_MC = {"probabilities": {"A": 0.6, "B": 0.4}, "reasoning": "researched",
                 "reference_class": "past comparable cases", "base_rate": {"A": 0.55, "B": 0.45},
                 "sources": ["https://s/1", "https://s/2", "https://s/3"]}

NUMERIC_Q = {
    "id": 3,
    "type": "numeric",
    "title": "How many filings?",
    "scaling": {"range_min": 0, "range_max": 100},
    "resolution_criteria": "Resolves to the count.",
    "scheduled_close_time": "2026-12-01T00:00:00Z",
    "scheduled_resolve_time": "2026-12-15T00:00:00Z",
}
RESEARCHED_NUMERIC = {
    "percentiles": {"10": 10, "25": 20, "50": 30, "75": 40, "90": 50},
    "reasoning": "researched", "reference_class": "past quarters", "base_rate": 30,
    "sources": ["https://s/1", "https://s/2", "https://s/3"],
}


class TestDistinctSourceCount:
    def test_dedupes_after_trimming(self) -> None:
        assert run_bot.distinct_source_count(
            {"sources": ["https://a", "https://a ", "https://a"]}) == 1

    def test_ignores_blank_and_nonlist(self) -> None:
        assert run_bot.distinct_source_count({"sources": ["", "  ", "https://a"]}) == 1
        assert run_bot.distinct_source_count({"sources": "https://a"}) == 0
        assert run_bot.distinct_source_count({}) == 0


class TestFloorOnResearchRun:
    def test_thin_mc_run_gets_repair_retry_then_records(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # The observed q44381 failure class: an MC forecast straight from memory.
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(THIN_MC), fenced(RESEARCHED_MC)],
                                tiers(min_sources=3), MC_Q)
        assert ok and record is not None
        assert record["research"]["n_searches"] == 3
        retry = agent.calls[1]["prompt"]
        assert "0 distinct source(s)" in retry and "at least 3" in retry

    def test_padding_with_duplicates_does_not_pass(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        padded = dict(THIN_MC, sources=["https://a", "https://a", "https://a"])
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(padded), fenced(padded)],
                                tiers(min_sources=3), MC_Q)
        assert not ok and record is None
        assert len(agent.calls) == 2  # floor tripped, retry tripped again -> question fails

    def test_two_thin_attempts_fail_and_ledger(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(THIN_MC), fenced(THIN_MC)],
                                tiers(min_sources=3), MC_Q)
        assert not ok and record is None
        ledger = tmp_path / "failures.jsonl"
        assert ledger.exists()
        assert "distinct source(s)" in ledger.read_text(encoding="utf-8")

    def test_floor_announced_in_research_system_prompt(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        agent, _, ok = run(monkeypatch, tmp_path, [fenced(RESEARCHED_MC)],
                           tiers(min_sources=3), MC_Q)
        assert ok
        assert "at least 3 DISTINCT" in agent.calls[0]["system"]

    def test_floor_zero_disables_announcement_and_check(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        agent, _, ok = run(monkeypatch, tmp_path, [fenced(THIN_MC)],
                           tiers(min_sources=0), MC_Q)
        assert ok and len(agent.calls) == 1
        assert "Research floor" not in agent.calls[0]["system"]


class TestReasoningRunsExempt:
    def test_multirun_reasoning_sources_may_be_empty(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        research = {"probability": 0.30, "dossier": "- fact A (src, 2026)",
                    "reasoning": "researched", "reference_class": "R", "base_rate": 0.2,
                    "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        reasoning = {"probability": 0.35, "reasoning": "x", "sources": [],
                     "named_scenarios": []}
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(research), fenced(reasoning), fenced(reasoning)],
                                tiers(min_sources=3, runs=3), BINARY_Q)
        assert ok and record is not None
        assert len(agent.calls) == 3  # no floor retries on the [] reasoning runs
        # the floor announcement is the research run's alone
        assert "Research floor" in agent.calls[0]["system"]
        assert "Research floor" not in agent.calls[1]["system"]


class TestReferenceClassFloor:
    """v0.4.6: research runs on MC/continuous must name a reference_class (the even-spread
    32/31/34 failure was an MC run that never derived a prior from one). Enforced only when
    min_sources>0 and the type is non-binary; binary keeps its own contract example."""

    def test_mc_missing_reference_class_repairs(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        no_ref = {"probabilities": {"A": 0.6, "B": 0.4}, "reasoning": "researched",
                  "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(no_ref), fenced(RESEARCHED_MC)],
                                tiers(min_sources=3), MC_Q)
        assert ok and record is not None
        assert "reference_class" in agent.calls[1]["prompt"]
        assert record["reference_class"] == "past comparable cases"

    def test_numeric_missing_reference_class_repairs(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        no_ref = {"percentiles": {"10": 10, "25": 20, "50": 30, "75": 40, "90": 50},
                  "reasoning": "researched",
                  "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(no_ref), fenced(RESEARCHED_NUMERIC)],
                                tiers(min_sources=3), NUMERIC_Q)
        assert ok and record is not None
        assert "reference_class" in agent.calls[1]["prompt"]
        assert record["reference_class"] == "past quarters"

    def test_min_sources_zero_does_not_require_reference_class(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # A reasoning-tier run (min_sources=0) never has to name a reference class.
        no_ref = {"probabilities": {"A": 0.6, "B": 0.4}, "reasoning": "from memory",
                  "sources": []}
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(no_ref)],
                                tiers(min_sources=0), MC_Q)
        assert ok and record is not None and len(agent.calls) == 1
        assert "Reference-class floor" not in agent.calls[0]["system"]

    def test_mc_base_rate_invented_label_rejected(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bad = {"probabilities": {"A": 0.6, "B": 0.4}, "reasoning": "researched",
               "reference_class": "past cases", "base_rate": {"A": 0.5, "C": 0.5},
               "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(bad), fenced(RESEARCHED_MC)],
                                tiers(min_sources=3), MC_Q)
        assert ok and record is not None  # repaired on the retry
        retry = agent.calls[1]["prompt"]
        assert "base_rate" in retry and "invent labels" in retry

    def test_mc_base_rate_not_summing_to_one_accepted(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # base_rate is an anchor, not the submission: valid labels but summing to 1.2 is fine.
        payload = {"probabilities": {"A": 0.6, "B": 0.4}, "reasoning": "researched",
                   "reference_class": "past cases", "base_rate": {"A": 0.6, "B": 0.6},
                   "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(payload)],
                                tiers(min_sources=3), MC_Q)
        assert ok and record is not None and len(agent.calls) == 1

    def test_binary_research_run_needs_no_reference_class(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        no_ref = {"probability": 0.6, "reasoning": "researched",
                  "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(no_ref)],
                                tiers(min_sources=3), BINARY_Q)
        assert ok and record is not None and len(agent.calls) == 1
        assert "Reference-class floor" not in agent.calls[0]["system"]

    def test_reference_class_announced_for_mc_research(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        agent, _, ok = run(monkeypatch, tmp_path, [fenced(RESEARCHED_MC)],
                           tiers(min_sources=3), MC_Q)
        assert ok
        assert "Reference-class floor" in agent.calls[0]["system"]
        assert "REQUIRED" in agent.calls[0]["system"]


class TestDefaults:
    def test_every_tier_ships_a_floor(self) -> None:
        assert DEFAULTS["tiers"]["low"]["min_sources"] == 1
        assert DEFAULTS["tiers"]["medium"]["min_sources"] == 3
        assert DEFAULTS["tiers"]["high"]["min_sources"] == 5

    def test_floor_never_exceeds_the_search_budget(self) -> None:
        for tier, params in DEFAULTS["tiers"].items():
            assert params["min_sources"] <= params["searches"], tier
