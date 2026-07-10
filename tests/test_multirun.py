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
import time
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


class SubmitCaptureClient(StubClient):
    """Live-mode stub: records what would hit the Metaculus API."""

    def __init__(self, *, comment_error: bool = False) -> None:
        self.submitted: list[tuple[str, Any]] = []
        self.comments: list[tuple[int, str]] = []
        self.comment_error = comment_error

    def submit_binary(self, question_id: int, probability: float) -> None:
        self.submitted.append(("binary", probability))

    def submit_multiple_choice(self, question_id: int, by_option: dict[str, float]) -> None:
        self.submitted.append(("mc", by_option))

    def submit_cdf(self, question_id: int, cdf: list[float]) -> None:
        self.submitted.append(("cdf", cdf))

    def comment(self, post_id: int, text: str, *, private: bool = True) -> None:
        if self.comment_error:
            raise RuntimeError("comment API down")
        self.comments.append((post_id, text))


def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outputs: list[str],
        config: dict[str, Any] | None = None, effort: str = "medium",
        question: dict[str, Any] | None = None, budget: float = 0.0,
        with_verify: bool = False, blind: bool = False, dry_run: bool = True,
        client: Any = None, deadline: float | None = None,
        comment: bool = False) -> tuple[ScriptedAgent, dict[str, Any] | None, bool]:
    agent = ScriptedAgent(outputs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    if not with_verify:  # most tests script only the forecast runs
        monkeypatch.setattr(run_bot, "verify_dossier", lambda *a, **k: ("", 0.0))
    args = argparse.Namespace(
        blind=blind, effort=effort, provider="subscription", timeout=60,
        dry_run=dry_run, comment=comment, budget=budget,
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
        client or StubClient(), POST, question or QUESTION, args, config, journal, spent,
        deadline,
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
        # v0.4.8: the pooling disclosure LEADS (truncation-proof); the narrative follows
        assert record["reasoning"].startswith("[pooled 3 independent runs")
        assert "researched" in record["reasoning"]
        assert record["base_rate"] == 0.2

    def test_pooling_note_survives_a_narrative_longer_than_the_record_cap(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The record head-truncates reasoning to 4000 chars; before v0.4.8 a long
        # research narrative silently pushed the which-number-was-submitted disclosure
        # off the end of the journal and the posted comment.
        long_research = dict(RESEARCH, reasoning="researched " * 400)  # ~4400 chars
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(long_research),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ])
        assert ok and record is not None
        assert len(record["reasoning"]) <= 4000
        assert "pooled 3 independent runs" in record["reasoning"]

    def test_records_carry_dry_run_provenance(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A --dry-run/--post record never reached the platform; the journal must say so
        # (pre-0.4.8 records have dry_run=None and are assumed live).
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ])  # the harness default in these tests is dry_run=True
        assert ok and record["dry_run"] is True
        client = SubmitCaptureClient()
        _, live_record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ], dry_run=False, client=client)
        assert ok and live_record["dry_run"] is False and client.submitted

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


class TestBotCrowdBoundary:
    """v0.4.2: a bot token only ever sees other bots' aggregates — journal them as the
    benchmark, never let them into the brief (measured: the injected sandbox bot-crowd
    pulled a sighted run away from the real market consensus)."""

    def test_sighted_brief_gets_note_not_bot_aggregate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ])
        assert ok and record is not None
        for call in agent.calls:  # StubClient's 0.5 must reach no context
            assert "Community prediction" not in call["prompt"]
        assert "Crowd signals" in agent.calls[0]["prompt"]
        assert record["crowd"]["value"] == 0.5  # benchmark still journaled
        assert record["crowd"]["shown_to_agent"] is False
        assert record["crowd"]["source"] == "metaculus bot aggregate"
        # 0.4.3: shown_to_agent is pinned False, so the record itself must carry the
        # mode or `score --by blind` mislabels every sighted tournament record.
        assert record["blind"] is False

    def test_blind_brief_has_neither_value_nor_note(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ], blind=True)
        assert ok and record is not None
        for call in agent.calls:
            assert "Community prediction" not in call["prompt"]
            assert "Crowd signals" not in call["prompt"]
        assert record["crowd"]["shown_to_agent"] is False
        assert record["blind"] is True


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
        assert "Verify every other premise with ONE targeted web search" in (
            agent.calls[1]["prompt"]
        )
        # v0.4.4: the verifier gets the contract so it can text-check the dossier's
        # assumed event window against the criteria (the q44378 shrunk-window miss).
        assert "## The contract (for the event-window check only)" in (
            agent.calls[1]["prompt"]
        )
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

    def test_collapsed_ensemble_is_marked_single_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Both reasoning slots die -> the lone research number is submitted. Right call,
        # but the journal must say the intended ensemble never happened, or scoring
        # credits "the ensemble" with a forecast no ensemble made.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            "AGENT_FAILURE", "AGENT_FAILURE",
            "AGENT_FAILURE", "AGENT_FAILURE",
        ])
        assert ok and record is not None
        assert record["probability"] == 0.30
        assert record["aggregation"] == "single_run(of 3 intended)"


class TestLiveSubmission:
    """dry_run=False — the branch the tournament actually exercises (the review fleet
    found it had never once run under test). The invariant throughout: the public
    preregistration journal records exactly the numbers the platform received."""

    LOW = {"low": {"draws": 1, "searches": 1, "runs": 1}}

    def test_binary_journal_records_the_submitted_clamped_number(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = SubmitCaptureClient()
        agent, record, ok = run(
            monkeypatch, tmp_path,
            [fenced({"probability": 0.998, "reasoning": "x", "sources": []})],
            config=config_with_tiers(self.LOW), effort="low",
            dry_run=False, client=client,
        )
        assert ok
        kind, submitted = client.submitted[0]
        assert kind == "binary"
        assert submitted == pytest.approx(0.99)
        assert record is not None and record["probability"] == pytest.approx(0.99)

    def test_mc_normalized_before_the_record_and_identical_at_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        mc_question = {**QUESTION, "type": "multiple_choice", "options": ["A", "B"]}
        client = SubmitCaptureClient()
        agent, record, ok = run(
            monkeypatch, tmp_path,
            [fenced({"probabilities": {"A": 0.999, "B": 0.0},
                     "reasoning": "x", "sources": []})],
            config=config_with_tiers(self.LOW), effort="low",
            question=mc_question, dry_run=False, client=client,
        )
        assert ok
        kind, submitted = client.submitted[0]
        assert kind == "mc"
        assert submitted["B"] == pytest.approx(0.001)  # floored into the API band
        assert sum(submitted.values()) == pytest.approx(1.0)
        assert record is not None
        assert record["probabilities"] == [pytest.approx(v)
                                           for v in (submitted["A"], submitted["B"])]

    def test_discrete_submits_a_cdf_sized_by_outcome_count(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        question = {
            **QUESTION, "type": "discrete", "inbound_outcome_count": 100,
            "scaling": {"range_min": 0.0, "range_max": 100.0, "zero_point": None},
            "open_lower_bound": False, "open_upper_bound": False,
        }
        client = SubmitCaptureClient()
        agent, record, ok = run(
            monkeypatch, tmp_path,
            [fenced({"percentiles": {"10": 5.0, "25": 20.0, "50": 50.0,
                                     "75": 75.0, "90": 95.0},
                     "reasoning": "x", "sources": []})],
            config=config_with_tiers(self.LOW), effort="low",
            question=question, dry_run=False, client=client,
        )
        assert ok
        kind, cdf = client.submitted[0]
        assert kind == "cdf"
        assert len(cdf) == 101  # inbound_outcome_count + 1
        assert all(b >= a for a, b in zip(cdf, cdf[1:], strict=False))

    def test_comment_failure_never_fails_the_question(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = SubmitCaptureClient(comment_error=True)
        agent, record, ok = run(
            monkeypatch, tmp_path,
            [fenced({"probability": 0.4, "reasoning": "why", "sources": []})],
            config=config_with_tiers(self.LOW), effort="low",
            dry_run=False, client=client, comment=True,
        )
        assert ok  # forecast submitted; the comment is cosmetic
        assert client.submitted and client.comments == []


class TestFailureLedger:
    """Per-question backoff for the hourly cron: question-content failures count,
    infra failures (auth outage, session limit) must not poison the ledger."""

    LOW = {"low": {"draws": 1, "searches": 1, "runs": 1}}

    def test_invalid_payload_after_agent_reply_is_ledgered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bad = fenced({"probability": 7.3, "reasoning": "x", "sources": []})
        agent, record, ok = run(monkeypatch, tmp_path, [bad, bad],
                                config=config_with_tiers(self.LOW), effort="low")
        assert not ok and record is None
        entries = [json.loads(line) for line in
                   (tmp_path / "failures.jsonl").read_text(encoding="utf-8").splitlines()]
        assert entries[0]["question_id"] == QUESTION["id"]
        assert "probability" in entries[0]["error"]

    def test_infra_failure_is_not_ledgered(self, monkeypatch: pytest.MonkeyPatch,
                                           tmp_path: Path) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, ["AGENT_FAILURE"] * 6)
        assert not ok
        assert not (tmp_path / "failures.jsonl").exists()

    def test_deadline_already_passed_skips_without_calls_or_ledger(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(RESEARCH)],
                                deadline=time.monotonic() - 1)
        assert not ok and record is None
        assert agent.calls == []  # the clock ran out — no agent spend
        assert not (tmp_path / "failures.jsonl").exists()

    def test_recent_failure_counts_respects_window_and_garbage(self, tmp_path: Path) -> None:
        path = tmp_path / "failures.jsonl"
        for _ in range(3):
            run_bot.record_failure(path, 42, "boom")
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"question_id": 42, "at": "2020-01-01T00:00:00+00:00"}\n')
            fh.write("not json at all\n")
            fh.write('{"at": "2026-07-06T00:00:00+00:00"}\n')  # no id -> ignored
        counts = run_bot.recent_failure_counts(path)
        assert counts == {42: 3}  # the 2020 entry aged out; garbage ignored


class ListClient:
    """main()-level stub: a fixed post list, no already-forecasted state."""

    def __init__(self, posts: list[dict[str, Any]]) -> None:
        self._posts = posts

    def open_posts(self, tournament: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return self._posts

    def post_detail(self, post_id: int) -> dict[str, Any]:
        return next(p for p in self._posts if p.get("id") == post_id)

    questions_of = staticmethod(run_bot.MetaculusClient.questions_of)
    already_forecasted = staticmethod(run_bot.MetaculusClient.already_forecasted)

    def community_prediction(self, question: dict[str, Any]) -> None:
        return None


def run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
             posts: list[dict[str, Any]]) -> tuple[int, list[Any]]:
    forecasted: list[Any] = []
    monkeypatch.setattr(run_bot, "MetaculusClient", lambda: ListClient(posts))

    def fake_forecast(client: Any, post: Any, question: Any, args: Any, config: Any,
                      journal: Any, spent: Any = None, deadline: Any = None) -> bool:
        forecasted.append(question.get("id"))
        return True

    monkeypatch.setattr(run_bot, "forecast_question", fake_forecast)
    code = run_bot.main(["--tournament", "t", "--dry-run",
                         "--journal", str(tmp_path / "j.jsonl")])
    return code, forecasted


class TestMainPrefilters:
    """Structurally unforecastable questions must be skipped BEFORE any agent spend —
    and as skips, not failures: a nonzero exit re-runs the batch on the paid fallback."""

    def test_unsupported_closed_and_unbounded_are_skipped_free(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        posts = [
            {"id": 1, "question": {"id": 11, "type": "binary", "title": "ok",
                                   "status": "open"}},
            {"id": 2, "question": {"id": 12, "type": "conditional", "title": "odd type"}},
            {"id": 3, "question": {"id": 13, "type": "binary", "title": "closed sub-q",
                                   "status": "closed"}},
            {"id": 4, "question": {"id": 14, "type": "numeric", "title": "no bounds",
                                   "scaling": {}}},
        ]
        code, forecasted = run_main(monkeypatch, tmp_path, posts)
        assert code == 0
        assert forecasted == [11]

    def test_post_backtests_a_closed_question_in_dry_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # --post targets one post via post_detail and bypasses the open-status /
        # already-forecasted filters, so a CLOSED question (the q44378 backtest case) is
        # forecast; it must force dry-run so the closed target is never submitted to.
        posts = [{"id": 99, "question": {"id": 44378, "type": "binary",
                                         "title": "closed backtest target",
                                         "status": "closed"}}]
        forecasted: list[Any] = []
        seen_args: dict[str, Any] = {}
        monkeypatch.setattr(run_bot, "MetaculusClient", lambda: ListClient(posts))

        def fake_forecast(client, post, question, args, config, journal,
                          spent=None, deadline=None):
            forecasted.append(question.get("id"))
            seen_args["dry_run"] = args.dry_run
            return True

        monkeypatch.setattr(run_bot, "forecast_question", fake_forecast)
        code = run_bot.main(["--post", "99", "--journal", str(tmp_path / "j.jsonl")])
        assert code == 0
        assert forecasted == [44378]      # closed question forecast anyway
        assert seen_args["dry_run"] is True  # never submits

    def test_backoff_after_repeated_failures(self, monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path) -> None:
        ledger = tmp_path / "failures.jsonl"
        for _ in range(run_bot.MAX_QUESTION_FAILURES):
            run_bot.record_failure(ledger, 11, "still failing")
        posts = [
            {"id": 1, "question": {"id": 11, "type": "binary", "title": "flaky"}},
            {"id": 2, "question": {"id": 15, "type": "binary", "title": "fresh"}},
        ]
        code, forecasted = run_main(monkeypatch, tmp_path, posts)
        assert code == 0
        assert forecasted == [15]

    def test_live_run_without_metaculus_token_fails_before_any_spend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("METACULUS_TOKEN", raising=False)
        code = run_bot.main(["--tournament", "t",
                             "--journal", str(tmp_path / "j.jsonl")])
        assert code == 1


class TestPayloadValidation:
    MC_Q = {**QUESTION, "type": "multiple_choice", "options": ["A", "B"]}

    def test_extra_option_key_is_an_error_not_an_api_400(self) -> None:
        errors = run_bot.validate_payload(
            {"probabilities": {"A": 0.5, "B": 0.4, "C": 0.1}}, self.MC_Q)
        assert errors and "unknown options" in errors[0]

    def test_non_numeric_option_probability_is_an_error_not_a_crash(self) -> None:
        # An exception here would skip the repair retry and fail a fixable payload.
        errors = run_bot.validate_payload(
            {"probabilities": {"A": "likely", "B": 0.4}}, self.MC_Q)
        assert errors == ["every option probability must be a number"]

    def test_non_numeric_percentile_is_an_error_not_a_crash(self) -> None:
        q = {**QUESTION, "type": "numeric",
             "scaling": {"range_min": 0.0, "range_max": 10.0}}
        errors = run_bot.validate_payload({"percentiles": {"50": "five"}}, q)
        assert errors == ["every percentile value must be a number"]
