"""State-machine tests for forecast_question's multi-run dossier path (v0.2.1+).

run_agent is mocked with scripted responses, so these cover the loop logic the helper
unit tests can't: dossier retry, research-run failure recovery, reasoning-run failure,
lens assignment order, cross-model cycling, and which run's payload feeds the record.
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

from forecast_scaffold.core import DEFAULTS, Journal, geo_mean_odds  # noqa: E402


def config_with_tiers(tiers: dict[str, Any]) -> dict[str, Any]:
    """A full config (production always merges DEFAULTS) with the tiers under test."""
    merged = json.loads(json.dumps(DEFAULTS))
    merged["tiers"] = tiers
    return merged


POST = {"id": 1, "title": "Will X happen?"}
QUESTION = {
    "id": 1,
    "type": "binary",
    "title": "Will X happen?",
    "resolution_criteria": "Resolves YES per source S.",
    "scheduled_close_time": "2026-12-01T00:00:00Z",
    "scheduled_resolve_time": "2026-12-15T00:00:00Z",
}


def fenced(payload: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


RESEARCH = {"probability": 0.30, "dossier": "- fact A (src, 2026)\n- fact B (src, 2026)",
            "reasoning": "researched", "sources": ["https://example.com/a"],
            "reference_class": "class R", "base_rate": 0.2}


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
        out = self.outputs.pop(0)
        if out == "AGENT_FAILURE":
            raise RuntimeError("agent failed (1): boom")
        return out, 0.05, "claude-sonnet-5"


class StubClient:
    def community_prediction(self, question: dict[str, Any]) -> float:
        return 0.5


def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outputs: list[str],
        config: dict[str, Any] | None = None, effort: str = "medium",
        question: dict[str, Any] | None = None,
        budget: float = 0.0) -> tuple[ScriptedAgent, dict[str, Any] | None, bool]:
    agent = ScriptedAgent(outputs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    args = argparse.Namespace(
        blind=False, effort=effort, provider="subscription", timeout=60,
        dry_run=True, comment=False, budget=budget,
        agent_cmd=("claude -p --model claude-sonnet-5 --output-format json "
                   "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch"),
    )
    config = config or config_with_tiers(
        {"medium": {"draws": 5, "searches": 5, "runs": 3}}
    )
    journal_path = tmp_path / "j.jsonl"
    journal = Journal(str(journal_path))
    spent = {"usd": 0.0}
    ok = run_bot.forecast_question(
        StubClient(), POST, question or QUESTION, args, config, journal, spent
    )
    record = None
    if journal_path.exists() and journal_path.read_text(encoding="utf-8").strip():
        record = json.loads(journal_path.read_text(encoding="utf-8").splitlines()[-1])
    return agent, record, ok


class TestHappyPath:
    def test_three_runs_pool_and_record(self, monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced({"probability": 0.20, "reasoning": "lens1", "sources": []}),
            fenced({"probability": 0.40, "reasoning": "lens2", "sources": []}),
        ])
        assert ok and record is not None
        assert record["raw_draws"] == [0.30, 0.20, 0.40]
        assert record["probability"] == pytest.approx(geo_mean_odds([0.3, 0.2, 0.4]))
        assert record["aggregation"] == "geo_mean_odds(runs=3)"
        # the RECORD payload is the researcher's: its sources/reasoning/base_rate survive
        assert record["research"]["sources"] == ["https://example.com/a"]
        assert record["reasoning"].startswith("researched")  # + the appended pooling note
        assert record["base_rate"] == 0.2

    def test_research_run_asks_for_dossier_reasoning_runs_do_not(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, _, _ = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced({"probability": 0.20, "reasoning": "x", "sources": []}),
            fenced({"probability": 0.40, "reasoning": "x", "sources": []}),
        ])
        assert "Dossier (multi-run mode" in (agent.calls[0]["system"] or "")
        for call in agent.calls[1:]:
            assert "Reasoning-only run" in (call["system"] or "")
            assert "Dossier (multi-run mode" not in (call["system"] or "")

    def test_reasoning_runs_are_webless_and_lens_ordered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, _, _ = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced({"probability": 0.20, "reasoning": "x", "sources": []}),
            fenced({"probability": 0.40, "reasoning": "x", "sources": []}),
        ])
        for call in agent.calls[1:]:
            assert "--disallowed-tools WebSearch,WebFetch" in call["cmd"]
            assert "WebSearch" not in call["cmd"].split("--disallowed-tools")[0].split(
                "--allowed-tools"
            )[1].split()[0]
            assert RESEARCH["dossier"] in call["prompt"]
        assert run_bot.LENSES[0].split(":")[0] in agent.calls[1]["prompt"]
        assert run_bot.LENSES[1].split(":")[0] in agent.calls[2]["prompt"]


class TestFailureRecovery:
    def test_missing_dossier_triggers_repair_retry(self, monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
        no_dossier = {k: v for k, v in RESEARCH.items() if k != "dossier"}
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(no_dossier),          # attempt 1: valid payload but no dossier
            fenced(RESEARCH),            # attempt 2 (repair): includes dossier
            fenced({"probability": 0.20, "reasoning": "x", "sources": []}),
            fenced({"probability": 0.40, "reasoning": "x", "sources": []}),
        ])
        assert ok and record is not None
        assert 'requires a non-empty "dossier"' in agent.calls[1]["prompt"]
        assert record["raw_draws"] == [0.30, 0.20, 0.40]

    def test_research_failure_consumes_a_run_then_recovers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # run 1 fails both attempts -> iteration 2 retries the FULL run -> 1 reasoning run left
        agent, record, ok = run(monkeypatch, tmp_path, [
            "AGENT_FAILURE", "AGENT_FAILURE",   # research run, both attempts
            fenced(RESEARCH),                    # second iteration: research succeeds
            fenced({"probability": 0.20, "reasoning": "x", "sources": []}),
        ])
        assert ok and record is not None
        assert record["raw_draws"] == [0.30, 0.20]
        assert record["aggregation"] == "geo_mean_odds(runs=2)"

    def test_all_research_attempts_fail_skips_question(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, ["AGENT_FAILURE"] * 6)
        assert not ok and record is None

    def test_reasoning_failure_shrinks_pool(self, monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            "AGENT_FAILURE", "AGENT_FAILURE",   # reasoning run 1 dies
            fenced({"probability": 0.40, "reasoning": "x", "sources": []}),
        ])
        assert ok and record is not None
        assert record["raw_draws"] == [0.30, 0.40]

    def test_lens_advances_past_failed_slot(self, monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
        # The lens index must come from an attempt counter, not the success count —
        # otherwise one transient failure silently hands the SAME lens (and model) to
        # the next slot and collapses the ensemble's diversity (red-team finding #1).
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            "AGENT_FAILURE", "AGENT_FAILURE",   # slot for LENSES[0] dies
            fenced({"probability": 0.40, "reasoning": "x", "sources": []}),
        ])
        assert ok
        lens0, lens1 = (lens.split(":")[0] for lens in run_bot.LENSES[:2])
        assert lens0 in agent.calls[1]["prompt"]          # failed slot used lens 0
        assert lens1 in agent.calls[3]["prompt"]          # next slot ADVANCED to lens 1
        assert lens0 not in agent.calls[3]["prompt"]

    def test_budget_stops_new_run_slots(self, monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
        # Each scripted call costs $0.05; a $0.04 budget is exhausted by the research
        # run alone, so no reasoning slots may start — but the question still records.
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(RESEARCH)], budget=0.04)
        assert ok and record is not None
        assert len(agent.calls) == 1
        assert record.get("raw_draws") is None

    def test_missing_evidence_reaches_the_record(self, monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced({"probability": 0.20, "reasoning": "x", "sources": [],
                    "missing_evidence": "no polling later than March"}),
            fenced({"probability": 0.40, "reasoning": "x", "sources": []}),
        ])
        assert ok and record is not None
        assert record["research"]["missing_evidence"] == ["no polling later than March"]

    def test_pooled_reasoning_carries_the_pooling_note(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Without the note the journal (and any posted comment) narrates the research
        # run's own number, not the pooled one that was actually submitted.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced({"probability": 0.20, "reasoning": "x", "sources": []}),
            fenced({"probability": 0.40, "reasoning": "x", "sources": []}),
        ])
        assert ok and record is not None
        assert "pooled 3 independent runs" in record["reasoning"]


class TestShapes:
    def test_single_run_tier_has_no_dossier_section(self, monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
        agent, record, ok = run(
            monkeypatch, tmp_path,
            [fenced({"probability": 0.30, "reasoning": "x", "sources": []})],
            config=config_with_tiers({"low": {"draws": 1, "searches": 1, "runs": 1}}),
            effort="low",
        )
        assert ok and record is not None
        assert "Dossier (multi-run mode" not in (agent.calls[0]["system"] or "")
        assert record.get("raw_draws") is None
        assert record.get("aggregation") is None

    def test_multiple_choice_is_forced_single_run(self, monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:
        mc_question = {**QUESTION, "type": "multiple_choice", "options": ["A", "B"]}
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced({"probabilities": {"A": 0.6, "B": 0.4}, "reasoning": "x", "sources": []}),
        ], question=mc_question)
        assert ok and record is not None
        assert len(agent.calls) == 1
        assert "Dossier (multi-run mode" not in (agent.calls[0]["system"] or "")

    def test_run_models_cycle_across_reasoning_runs(self, monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced({"probability": 0.20, "reasoning": "x", "sources": []}),
            fenced({"probability": 0.40, "reasoning": "x", "sources": []}),
        ], config=config_with_tiers({"medium": {
            "draws": 5, "searches": 5, "runs": 3,
            "run_models": ["claude-opus-4-8", "claude-haiku-4-5"],
        }}))
        assert ok
        assert "--model claude-opus-4-8" in agent.calls[1]["cmd"]
        assert "--model claude-haiku-4-5" in agent.calls[2]["cmd"]
