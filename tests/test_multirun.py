"""State-machine tests for forecast_question's multi-run dossier path (v0.2.1+).

run_agent is mocked with scripted responses, so these cover the loop logic the helper
unit tests can't: dossier retry, research-run failure recovery, reasoning-run failure,
suggested-angle rotation, named-scenario coherence flags, cross-model cycling, and which
run's payload feeds the record.
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


def reasoning_payload(p: float, **extra: Any) -> dict[str, Any]:
    """A minimal valid reasoning-run payload (named_scenarios is contract-required)."""
    return {"probability": p, "reasoning": "x", "sources": [],
            "named_scenarios": [], **extra}


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
        question: dict[str, Any] | None = None, budget: float = 0.0,
        with_verify: bool = False) -> tuple[ScriptedAgent, dict[str, Any] | None, bool]:
    agent = ScriptedAgent(outputs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    if not with_verify:  # most tests script only the forecast runs
        monkeypatch.setattr(run_bot, "verify_dossier", lambda *a, **k: ("", 0.0))
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
            fenced(reasoning_payload(0.20, reasoning="lens1")),
            fenced(reasoning_payload(0.40, reasoning="lens2")),
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
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ])
        assert "Dossier (multi-run mode" in (agent.calls[0]["system"] or "")
        for call in agent.calls[1:]:
            assert "Reasoning run (shared dossier)" in (call["system"] or "")
            assert "Dossier (multi-run mode" not in (call["system"] or "")

    def test_reasoning_runs_get_dossier_and_rotated_angles(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, _, _ = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ])
        for call in agent.calls[1:]:
            assert RESEARCH["dossier"] in call["prompt"]
            # v0.3.0: web stays available for bounded gap-filling (instruction-scoped)
            assert "Reasoning run (shared dossier)" in (call["system"] or "")
            # v0.4.0: the angle is suggested, not assigned
            assert "Suggested angle" in call["prompt"]
        assert run_bot.LENSES[0].split(":")[0] in agent.calls[1]["prompt"]
        assert run_bot.LENSES[1].split(":")[0] in agent.calls[2]["prompt"]

    def test_wide_spread_pools_without_a_second_guess(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # v0.4.0: no arbiter — the pool is the aggregator even under wide disagreement;
        # the spread stays auditable in raw_draws instead of being overridden by one
        # extra context.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20, reasoning="low view")),
            fenced(reasoning_payload(0.50, reasoning="high view")),
        ])
        assert ok and record is not None
        assert len(agent.calls) == 3  # research + 2 reasoning, nothing after the pool
        assert record["probability"] == pytest.approx(geo_mean_odds([0.3, 0.2, 0.5]))
        assert record["aggregation"] == "geo_mean_odds(runs=3)"
        assert record["raw_draws"] == [0.30, 0.20, 0.50]


class TestNamedScenarios:
    def test_missing_named_scenarios_triggers_repair_retry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        no_disclosure = {"probability": 0.20, "reasoning": "x", "sources": []}
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(no_disclosure),               # attempt 1: no named_scenarios
            fenced(reasoning_payload(0.20)),     # attempt 2 (repair)
            fenced(reasoning_payload(0.40)),
        ])
        assert ok and record is not None
        assert 'must include "named_scenarios"' in agent.calls[2]["prompt"]
        assert record["raw_draws"] == [0.30, 0.20, 0.40]

    def test_incoherent_scenario_mass_is_flagged_never_overridden(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The audited tail failure (issue #10): p=0.03 while the run's own pathways to
        # YES total 0.14. The harness flags the arithmetic; the number is untouched.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.03, named_scenarios=[
                {"scenario": "named pathway to YES", "p": 0.14}])),
            fenced(reasoning_payload(0.40)),
        ])
        assert ok and record is not None
        assert record["probability"] == pytest.approx(geo_mean_odds([0.3, 0.03, 0.4]))
        assert "scenario-coherence" in record["reasoning"]
        assert "0.14" in record["reasoning"]

    def test_coherent_disclosure_is_not_flagged(self, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.30, named_scenarios=[
                {"scenario": "plausible YES pathway", "p": 0.20}])),  # room 0.30 >= 0.20
            fenced(reasoning_payload(0.40)),
        ])
        assert ok and record is not None
        assert "scenario-coherence" not in record["reasoning"]

    def test_borderline_mass_within_slack_is_not_flagged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Live e2e (2026-07-06): 0.25 named vs 0.24 room got flagged — rounding noise.
        # The 0.05 slack keeps the flag for real violations only.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.76, named_scenarios=[
                {"scenario": "NO pathway", "p": 0.25}])),  # room 0.24, within slack
            fenced(reasoning_payload(0.40)),
        ])
        assert ok and record is not None
        assert "scenario-coherence" not in record["reasoning"]


class TestFailureRecovery:
    def test_missing_dossier_triggers_repair_retry(self, monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
        no_dossier = {k: v for k, v in RESEARCH.items() if k != "dossier"}
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(no_dossier),          # attempt 1: valid payload but no dossier
            fenced(RESEARCH),            # attempt 2 (repair): includes dossier
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
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
            fenced(reasoning_payload(0.20)),
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
            fenced(reasoning_payload(0.40)),
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
            fenced(reasoning_payload(0.40)),
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
            fenced(reasoning_payload(
                0.20, missing_evidence="no polling later than March")),
            fenced(reasoning_payload(0.40)),
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
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
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

    def test_verification_verdicts_reach_reasoning_prompts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # O1: the CoVe premise check runs once after the dossier and its verdicts are
        # appended so every reasoning run sees them.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced({"verification": [
                {"premise": "seat math", "verdict": "confirmed",
                 "note": "matches official count", "source": "https://example.com/v"},
            ]}),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ], with_verify=True)
        assert ok and record is not None
        assert "Verify each with ONE targeted web search" in agent.calls[1]["prompt"]
        for call in agent.calls[2:]:
            assert "Verification (independent premise check" in call["prompt"]
            assert "CONFIRMED" in call["prompt"]

    def test_fast_proxies_recorded_for_slow_questions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        slow_q = {**QUESTION, "scheduled_resolve_time": "2027-09-01T00:00:00Z"}
        research = {**RESEARCH, "fast_proxies": [
            {"question": "Will the interim report be published by August 15?",
             "criterion": "per agency site", "resolve_by": "2026-08-20",
             "probability": 0.7},
        ]}
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ], question=slow_q)
        assert ok and record is not None
        assert "Fast proxies (slow question)" in (agent.calls[0]["system"] or "")
        journal_lines = [json.loads(line) for line in
                         (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(journal_lines) == 2
        proxy = journal_lines[-1]
        # the parent record is first; the proxy links back to it
        assert proxy["fast_proxy"] is True
        assert proxy["parent_id"] == journal_lines[0]["id"]
        assert proxy["probability"] == 0.7

    def test_run_models_cycle_across_reasoning_runs(self, monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ], config=config_with_tiers({"medium": {
            "draws": 5, "searches": 5, "runs": 3,
            "run_models": ["claude-opus-4-8", "claude-haiku-4-5"],
        }}))
        assert ok
        assert "--model claude-opus-4-8" in agent.calls[1]["cmd"]
        assert "--model claude-haiku-4-5" in agent.calls[2]["cmd"]
