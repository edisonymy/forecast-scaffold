"""Angle-mode tests (v0.4.x): a tier with a non-empty run_angles list flips the bot from
one-dossier + reasoning-only runs to N INDEPENDENT full-research runs under assigned angles,
pooled by geo_mean_odds. Angle F stays market-blind by design even in sighted mode.

Stub agents follow tests/test_multirun.py's ScriptedAgent pattern: run_agent is mocked with
scripted fenced-JSON outputs and every call is recorded, so we can assert per-run prompts,
system sections, commands, and the pooled record.
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

from forecast_scaffold.core import (  # noqa: E402
    DEFAULTS,
    Journal,
    geo_mean_odds,
    pool_mc,
)

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


def research_payload(p: float, **extra: Any) -> dict[str, Any]:
    """A valid full-research-run payload: enough distinct sources to clear the floor, and a
    named reference_class (every angle run is a research run under the source floor)."""
    return {
        "probability": p,
        "reasoning": "researched",
        "sources": [f"https://example.com/{i}" for i in range(5)],
        "reference_class": "class R",
        "base_rate": 0.2,
        **extra,
    }


def reasoning_payload(p: float, **extra: Any) -> dict[str, Any]:
    """A dossier-mode reasoning payload (named_scenarios is contract-required there)."""
    return {"probability": p, "reasoning": "x", "sources": [], "named_scenarios": [], **extra}


# A dossier-mode research payload, mirroring test_multirun.RESEARCH for the regression test.
RESEARCH = {"probability": 0.30, "dossier": "- fact A (src, 2026)\n- fact B (src, 2026)",
            "reasoning": "researched", "sources": ["https://example.com/a"],
            "reference_class": "class R", "base_rate": 0.2}


class ScriptedAgent:
    """Replaces run_bot.run_agent; returns scripted outputs and records every call."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []
        self.final_spent: float | None = None

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        self.calls.append({"cmd": cmd, "prompt": prompt, "system": system,
                           "timeout": timeout})
        if not self.outputs:
            raise RuntimeError("script exhausted")
        out = self.outputs.pop(0)
        if out == "AGENT_FAILURE":
            raise RuntimeError("agent failed (1): boom")
        return out, 0.05, "claude-sonnet-5"


class StubClient:
    def community_prediction(self, question: dict[str, Any]) -> None:
        return None


class CrowdClient(StubClient):
    def __init__(self, value: float = 0.6) -> None:
        self.value = value

    def community_prediction(self, question: dict[str, Any]) -> float:
        return self.value


def config_with_angles(angles: list[str], min_sources: int = 1) -> dict[str, Any]:
    """A full config (production always merges DEFAULTS) whose high tier is in angle mode."""
    merged = json.loads(json.dumps(DEFAULTS))
    merged["tiers"] = {"high": {"draws": 12, "searches": 12, "runs": 4, "run_models": [],
                                "min_sources": min_sources, "run_angles": angles}}
    return merged


def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outputs: list[str],
        config: dict[str, Any], effort: str = "high", blind: bool = False,
        client: Any = None, question: dict[str, Any] | None = None,
        budget: float = 0.0, deadline: float | None = None,
        timeout: int = 60) -> tuple[ScriptedAgent, dict[str, Any] | None, bool]:
    agent = ScriptedAgent(outputs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_bot, "verify_dossier", lambda *a, **k: ("", 0.0))
    args = argparse.Namespace(
        blind=blind, effort=effort, provider="subscription", timeout=timeout,
        dry_run=True, comment=False, budget=budget,
        agent_cmd=("claude -p --model claude-sonnet-5 --output-format json "
                   "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch"),
    )
    journal_path = tmp_path / "j.jsonl"
    journal = Journal(str(journal_path))
    spent = {"usd": 0.0}
    ok = run_bot.forecast_question(
        client or StubClient(), POST, question or QUESTION, args, config, journal, spent,
        deadline,
    )
    agent.final_spent = spent["usd"]
    record = None
    if journal_path.exists() and journal_path.read_text(encoding="utf-8").strip():
        record = json.loads(journal_path.read_text(encoding="utf-8").splitlines()[-1])
    return agent, record, ok


class TestAngleParsing:
    def test_sections_parse_from_the_reference_file(self) -> None:
        sections = run_bot.load_angle_sections()
        assert {"F", "D", "A"} <= set(sections)
        # each section carries the operator's own header + body, verbatim
        assert "Angle F — fundamentals (market-blind by design)" in sections["F"]
        assert "Angle D — decomposition (bottom-up)" in sections["D"]
        assert "Angle A — anomaly hunt" in sections["A"]
        # F's brief is the blind one; it must not bleed into D
        assert "market-blind" in sections["F"]
        assert "market-blind" not in sections["D"]

    def test_unknown_angle_letter_raises_at_startup(self) -> None:
        with pytest.raises(ValueError, match="unknown research angle"):
            run_bot.validate_run_angles(config_with_angles(["F", "Z"]))

    def test_known_angles_validate_and_return_sections(self) -> None:
        sections = run_bot.validate_run_angles(config_with_angles(["F", "D", "A"]))
        assert {"F", "D", "A"} <= set(sections)

    def test_main_validates_angles_before_touching_the_network(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The startup guard runs before MetaculusClient() — a bad angle can't reach the API.
        monkeypatch.setattr(run_bot, "load_config", lambda *a, **k: config_with_angles(["Q"]))

        def boom() -> None:  # pragma: no cover - must never be reached
            raise AssertionError("MetaculusClient constructed despite a bad angle config")

        monkeypatch.setattr(run_bot, "MetaculusClient", boom)
        with pytest.raises(ValueError, match="unknown research angle"):
            run_bot.main(["--tournament", "t", "--dry-run",
                          "--journal", str(tmp_path / "j.jsonl")])


class TestAngleMode:
    ANGLES = ["F", "D", "A"]

    def test_one_run_per_angle_with_its_section_no_dossier_machinery(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)),
            fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_with_angles(self.ANGLES))
        assert ok and record is not None
        assert len(agent.calls) == 3  # exactly one run per angle
        assert "Angle F — fundamentals" in (agent.calls[0]["system"] or "")
        assert "Angle D — decomposition" in (agent.calls[1]["system"] or "")
        assert "Angle A — anomaly hunt" in (agent.calls[2]["system"] or "")
        # each angle run is a FULL research run (source floor present), not a reasoning run
        for call in agent.calls:
            assert "Research floor (this run" in (call["system"] or "")
            assert "Dossier (multi-run mode" not in (call["system"] or "")
            assert "Reasoning run (shared dossier)" not in (call["system"] or "")

    def test_pooling_note_and_aggregation_name_the_angles(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)),
            fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_with_angles(self.ANGLES))
        assert ok and record is not None
        # per-angle probabilities land in raw_draws, pooled by geo_mean_odds
        assert record["raw_draws"] == [0.30, 0.50, 0.40]
        assert record["probability"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        # the aggregation tag and the disclosure note both name the angles
        assert record["aggregation"] == "geo_mean_odds(angles=F,D,A)"
        assert record["reasoning"].startswith(
            "[pooled 3 independent research runs (angles F,D,A)")
        # the spokesperson's own narrative still follows the note
        assert "researched" in record["reasoning"]
        assert record["reference_class"] == "class R"

    def test_angle_F_is_blind_even_when_the_run_is_sighted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)),
            fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_with_angles(self.ANGLES), client=CrowdClient(0.6))
        assert ok and record is not None
        fcall, dcall, acall = agent.calls
        # F: blind denylist on the command, blind section in the system, no crowd-scan brief
        assert run_bot.BLIND_DISALLOWED in fcall["cmd"]
        assert "Blind mode (mandatory)" in (fcall["system"] or "")
        assert "Crowd signals" not in fcall["prompt"]
        # D and A: ambient sighted — no blind denylist, and the market-scan mandate is present
        for call in (dcall, acall):
            assert run_bot.BLIND_DISALLOWED not in call["cmd"]
            assert "Blind mode (mandatory)" not in (call["system"] or "")
            assert "Crowd signals" in call["prompt"]
        # the bot-aggregate crowd value reaches NO agent context (journaled as benchmark only)
        for call in agent.calls:
            assert "Community prediction" not in call["prompt"]
        assert record["crowd"]["value"] == 0.6
        assert record["crowd"]["shown_to_agent"] is False
        assert record["blind"] is False  # the overall run is sighted; F is just one member

    def test_blind_overall_run_keeps_every_angle_blind(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)),
            fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_with_angles(self.ANGLES), blind=True, client=CrowdClient(0.6))
        assert ok and record is not None
        for call in agent.calls:
            assert run_bot.BLIND_DISALLOWED in call["cmd"]
            assert "Crowd signals" not in call["prompt"]
        assert record["blind"] is True

    def test_failed_angle_shrinks_the_pool(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The D-angle run dies both attempts; the pool is F + A and names only those.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)),      # F
            "AGENT_FAILURE", "AGENT_FAILURE",    # D dies
            fenced(research_payload(0.40)),      # A
        ], config=config_with_angles(self.ANGLES))
        assert ok and record is not None
        assert record["raw_draws"] == [0.30, 0.40]
        assert record["aggregation"] == "geo_mean_odds(angles=F,A)"

    def test_source_floor_applies_to_each_angle_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # min_sources=3: an angle run listing too few sources is rejected and repaired.
        thin = {"probability": 0.30, "reasoning": "x", "sources": ["https://only.one"],
                "reference_class": "class R", "base_rate": 0.2}
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(thin),                    # F attempt 1: 1 source, below the floor
            fenced(research_payload(0.30)),  # F attempt 2 (repair): 5 sources
            fenced(research_payload(0.50)),  # D
            fenced(research_payload(0.40)),  # A
        ], config=config_with_angles(self.ANGLES, min_sources=3))
        assert ok and record is not None
        assert "at least 3" in agent.calls[1]["prompt"]
        assert record["raw_draws"] == [0.30, 0.50, 0.40]


class TestEmptyRunAnglesPreservesDossierFlow:
    """The regression guard: run_angles=[] must leave the dossier path byte-for-byte
    unchanged. Expectations mirror test_multirun.TestHappyPath.test_three_runs_pool_and_record."""

    def test_empty_run_angles_is_the_old_dossier_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        merged = json.loads(json.dumps(DEFAULTS))
        merged["tiers"] = {"medium": {"draws": 5, "searches": 5, "runs": 3,
                                      "run_models": [], "min_sources": 1, "run_angles": []}}
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20, reasoning="lens1")),
            fenced(reasoning_payload(0.40, reasoning="lens2")),
        ], config=merged, effort="medium")
        assert ok and record is not None
        # identical numbers/aggregation/note to the pre-angle dossier path
        assert record["raw_draws"] == [0.30, 0.20, 0.40]
        assert record["probability"] == pytest.approx(geo_mean_odds([0.3, 0.2, 0.4]))
        assert record["aggregation"] == "geo_mean_odds(runs=3)"
        assert record["reasoning"].startswith("[pooled 3 independent runs")
        assert "researched" in record["reasoning"]
        # dossier machinery present; angle machinery absent
        assert "Dossier (multi-run mode" in (agent.calls[0]["system"] or "")
        assert "Reasoning run (shared dossier)" in (agent.calls[1]["system"] or "")
        for call in agent.calls:
            assert "Assigned research angle" not in (call["system"] or "")

    def test_defaults_ship_parallel_research(self) -> None:
        """Operator decision 2026-09-03: research is parallelized — medium/high launch
        independent plain research runs (Angle P) and pool them; low stays single-run."""
        assert DEFAULTS["tiers"]["low"]["run_angles"] == []
        assert DEFAULTS["tiers"]["medium"]["run_angles"] == ["P", "P", "P"]
        assert DEFAULTS["tiers"]["high"]["run_angles"] == ["P", "P", "P", "P"]

    def _retired_test_defaults_ship_run_angles_dark(self) -> None:
        for tier in ("low", "medium", "high"):
            assert DEFAULTS["tiers"][tier]["run_angles"] == []


# ------------------------------------------------- parallel research runs (plain angle mode)

def config_parallel(
    angles: tuple[str, ...] = ("P", "P", "P"), *, min_sources: int = 1, searches: int = 12,
) -> dict[str, Any]:
    """A full config whose high tier runs angle mode with the given angles."""
    merged = json.loads(json.dumps(DEFAULTS))
    merged["tiers"] = {"high": {
        "draws": 12, "searches": searches, "runs": len(angles), "run_models": [],
        "min_sources": min_sources, "run_angles": list(angles),
    }}
    return merged


def angle_run(p: float, i: int, **extra: Any) -> dict[str, Any]:
    """An angle research payload whose narrative is tagged by run number."""
    return research_payload(p, **{"reasoning": f"PRIVATE-NARRATIVE-{i}", **extra})


class TestPooledAngleRuns:
    """The submission is the pool of the independent research runs; the pool and its spread
    are journaled beside the per-run values."""

    def test_pool_and_spread_are_journaled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
        ], config=config_parallel())
        assert ok and record is not None
        assert len(agent.calls) == 3
        assert record["probability"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        assert record["raw_draws"] == [0.30, 0.50, 0.40]
        assert record["pool_phase1"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        assert record["spread_phase1"] == pytest.approx(0.20)
        assert record["aggregation"] == "geo_mean_odds(angles=P,P,P)"
        assert agent.final_spent == pytest.approx(0.15)  # 3 calls x $0.05
        assert record["cost_usd"] == pytest.approx(0.15)

    def test_a_lone_surviving_run_is_not_pooled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)),
            "AGENT_FAILURE", "AGENT_FAILURE", "AGENT_FAILURE", "AGENT_FAILURE",
        ], config=config_parallel())
        assert ok and record is not None
        assert len(agent.calls) == 5  # nothing after the angle slots
        assert record["aggregation"] == "single_run(of 3 intended)"
        assert "pool_phase1" not in record


NUMERIC_Q = {
    **QUESTION, "type": "numeric",
    "scaling": {"range_min": 0.0, "range_max": 200.0, "zero_point": None},
    "open_lower_bound": False, "open_upper_bound": True,
}
MC_Q = {**QUESTION, "type": "multiple_choice", "options": ["A", "B", "C"]}
KEYS = ("10", "25", "50", "75", "90")


def pcts(*values: float) -> dict[str, float]:
    return dict(zip(KEYS, [float(v) for v in values], strict=True))


def numeric_angle(i: int, *values: float, **extra: Any) -> dict[str, Any]:
    """A numeric angle run: percentiles + the research-run floors it is gated on."""
    return {
        "percentiles": pcts(*values), "reasoning": f"PRIVATE-NARRATIVE-{i}",
        "sources": [f"https://example.com/{i}"], "reference_class": "class R",
        "base_rate": 30.0, "dispersion_90_10": 40.0, "dispersion_basis": "SD 15.6 x 2.56",
        **extra,
    }


def mc_angle(i: int, probs: dict[str, float]) -> dict[str, Any]:
    """An MC angle run: option probabilities + the research-run floors it is gated on."""
    return {"probabilities": probs, "reasoning": f"PRIVATE-NARRATIVE-{i}",
            "sources": [f"https://example.com/{i}"], "reference_class": "class R",
            "base_rate": {"A": 0.5, "B": 0.3, "C": 0.2}}


class TestNonBinaryPoolsAreJournaled:
    """The pool and its spread use the same type-specific functions as the submission."""

    def test_numeric_pool_and_spread_in_question_units(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(numeric_angle(1, 10, 20, 30, 40, 50)),
            fenced(numeric_angle(2, 20, 30, 40, 50, 60, p_above_upper=0.1)),
            fenced(numeric_angle(3, 30, 40, 50, 60, 70)),
        ], config=config_parallel(), question=NUMERIC_Q)
        assert ok and record is not None
        assert len(agent.calls) == 3
        assert record["percentiles"] == pcts(20, 30, 40, 50, 60)
        assert record["pool_phase1"] == pcts(20, 30, 40, 50, 60)
        # spread is in question units: medians 30/40/50
        assert record["spread_phase1"] == pytest.approx(20.0)
        assert record["aggregation"] == "quantile_mean(angles=P,P,P)"
        # escape mass averages over the runs that declared one
        assert record["p_above_upper"] == pytest.approx(0.1)
        assert record["run_escapes"] == [[None, None], [None, 0.1], [None, None]]

    def test_mc_pool_and_leader_spread(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs = [{"A": 0.6, "B": 0.3, "C": 0.1},
                {"A": 0.5, "B": 0.3, "C": 0.2},
                {"A": 0.7, "B": 0.2, "C": 0.1}]
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(mc_angle(i, r)) for i, r in enumerate(runs, start=1)
        ], config=config_parallel(), question=MC_Q)
        assert ok and record is not None
        assert len(agent.calls) == 3
        assert record["run_probabilities"] == runs
        assert record["pool_phase1"]["A"] == pytest.approx(pool_mc(runs)["A"])
        # the MC spread is the disagreement on the pooled LEADER (option A)
        assert record["spread_phase1"] == pytest.approx(0.2)
        assert record["aggregation"] == "geo_mean_mc(angles=P,P,P)"


class TestNoPhaseMachinery:
    """Angle runs are independent research runs and nothing else: no dossier request, no
    second round, no reconciler, and no phase-2 or supervisor fields in the record."""

    def test_only_the_angle_runs_and_no_phase_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)), fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_parallel())
        assert ok and record is not None
        assert len(agent.calls) == 3
        for call in agent.calls:
            assert "Dossier (multi-run mode" not in (call["system"] or "")
            assert "Supervisor reconciliation" not in (call["system"] or "")
        assert record["aggregation"] == "geo_mean_odds(angles=P,P,P)"
        assert record["reasoning"].startswith(
            "[pooled 3 independent research runs (angles P,P,P)")
        for field in ("pool_phase2", "spread_phase2", "raw_draws_phase1", "supervisor"):
            assert field not in record

    def test_uniform_research_depth_across_tiers(self) -> None:
        """Operator decision 2026-09-03: a tier sets how much independent JUDGMENT a
        question gets, not how carefully one run reads the world."""
        for tier in ("low", "medium", "high"):
            assert DEFAULTS["tiers"][tier]["searches"] == 5
            assert DEFAULTS["tiers"][tier]["min_sources"] == 3
        assert [DEFAULTS["tiers"][t]["runs"] for t in ("low", "medium", "high")] == [1, 3, 4]

    def test_defaults_carry_no_phase_keys(self) -> None:
        """The shared-evidence and supervisor switches are gone from every tier."""
        for tier in ("low", "medium", "high"):
            for key in ("share_evidence", "supervisor", "supervisor_search_spread",
                        "supervisor_search_spread_iqr"):
                assert key not in DEFAULTS["tiers"][tier]


class TestTraces:
    """Per-question traces (v0.4.28). The journal says what the bot forecast; once a
    question is three-to-seven agent calls deep, the trace is the only place a reviewer can
    read what each RUN actually thought. Written after the record, best-effort."""

    @staticmethod
    def trace_of(tmp_path: Path, record: dict[str, Any]) -> dict[str, Any]:
        path = tmp_path / record["trace_path"]
        assert path.exists(), f"no trace at {record['trace_path']}"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_angle_mode_trace_has_one_entry_per_call(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
        ], config=config_parallel())
        assert ok and record is not None
        assert record["trace_path"] == f"traces/{record['id']}.json"
        trace = self.trace_of(tmp_path, record)
        assert len(trace["calls"]) == len(agent.calls) == 3
        assert trace["architecture"] == "angle"
        assert [c["stage"] for c in trace["calls"]] == ["angle_research"] * 3
        assert [c["phase"] for c in trace["calls"]] == [1, 1, 1]
        # each run carries its OWN estimate, narrative and sources
        for i, call in enumerate(trace["calls"], start=1):
            assert call["mode"] == "research" and call["angle"] == "P"
            assert call["run_index"] == i
            assert call["reasoning"] == f"PRIVATE-NARRATIVE-{i}"
            assert call["sources"] and call["model"] == "claude-sonnet-5"
            assert call["cost_usd"] == pytest.approx(0.05)
            assert isinstance(call["seconds"], (int, float))
        assert [c["estimate"] for c in trace["calls"]] == [0.30, 0.50, 0.40]
        # ...and the file also carries the pool and what was actually submitted
        assert trace["pools"]["phase1"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        assert trace["pools"]["spread_phase1"] == pytest.approx(0.20)
        assert trace["pools"]["pooled_runs"] == pytest.approx(
            geo_mean_odds([0.30, 0.50, 0.40]))
        assert "phase2" not in trace["pools"]
        assert trace["submitted"]["probability"] == pytest.approx(
            geo_mean_odds([0.30, 0.50, 0.40]))
        assert trace["aggregation"] == "geo_mean_odds(angles=P,P,P)"
        for key in ("share_evidence", "supervisor_enabled"):
            assert key not in trace

    def test_failed_runs_and_repairs_are_in_the_trace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        thin = {"probability": 0.30, "reasoning": "x", "sources": ["https://only.one"],
                "reference_class": "class R", "base_rate": 0.2}
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(thin), fenced(angle_run(0.30, 1)),   # run 1 repairs
            "AGENT_FAILURE", "AGENT_FAILURE",           # run 2 dies outright
            fenced(angle_run(0.40, 3)),
        ], config=config_parallel(min_sources=3))
        assert ok and record is not None
        trace = self.trace_of(tmp_path, record)
        assert len(trace["calls"]) == 3  # one entry per RUN, retries folded in
        assert trace["calls"][0]["ok"] is True
        assert "at least 3" in trace["calls"][0]["validation_errors_first_attempt"][0]
        assert trace["calls"][1]["ok"] is False and trace["calls"][1]["errors"]
        assert "estimate" not in trace["calls"][1]
        assert trace["calls"][2]["ok"] is True

    def test_dossier_path_is_traced_too(self, monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
        merged = json.loads(json.dumps(DEFAULTS))
        merged["tiers"] = {"medium": {"draws": 5, "searches": 5, "runs": 3,
                                      "run_models": [], "min_sources": 1, "run_angles": []}}
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(RESEARCH),
            fenced(reasoning_payload(0.20)),
            fenced(reasoning_payload(0.40)),
        ], config=merged, effort="medium")
        assert ok and record is not None
        trace = self.trace_of(tmp_path, record)
        assert trace["architecture"] == "dossier"
        assert [c["stage"] for c in trace["calls"]] == (
            ["dossier_research", "dossier_reasoning", "dossier_reasoning"])
        assert trace["calls"][0]["dossier"] == RESEARCH["dossier"]
        assert "dossier" not in trace["calls"][1]  # reasoning runs write none
        assert trace["calls"][1]["lens"] == run_bot.LENSES[0].split(":")[0]
        assert trace["submitted"]["probability"] == pytest.approx(
            geo_mean_odds([0.30, 0.20, 0.40]))

    def test_long_text_is_capped_and_the_file_stays_small(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A runaway dossier/narrative is committed on every hourly run, so both the per-field
        # cap and the whole-file cap have to bite.
        huge = "x" * 40_000
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1, reasoning=huge)),
            fenced(angle_run(0.50, 2, reasoning=huge)),
            fenced(angle_run(0.40, 3, reasoning=huge)),
        ], config=config_parallel())
        assert ok and record is not None
        path = tmp_path / record["trace_path"]
        assert path.stat().st_size <= run_bot.MAX_TRACE_BYTES
        trace = self.trace_of(tmp_path, record)
        for call in trace["calls"]:
            assert len(call["reasoning"]) <= run_bot.MAX_TRACE_TEXT

    def test_a_trace_write_failure_never_fails_the_forecast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def boom(*_a: Any, **_k: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(run_bot, "write_trace", boom)
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
        ], config=config_parallel())
        assert ok and record is not None  # the forecast is recorded regardless
        assert "trace not written" in capsys.readouterr().out


# ------------------------------------------------- review fixes, 2026-09-03 (two reviewers)

FIXED_MARKET_SECTION = (
    "## Market candidates found by the harness (record-only data)\n\n"
    "- Polymarket: \"FIXED-CANDIDATE\" — Yes price 0.42\n"
)


def stub_market_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """markets.market_facts_section replaced by a fixed section, so a prompt assertion is
    about the WIRING and not about whatever the (network-disabled) lookup returns."""
    monkeypatch.setattr(run_bot.markets, "market_facts_section",
                        lambda title, **kwargs: FIXED_MARKET_SECTION)


class TestMarketFactsReachEveryRun:
    """FIX A: the harness's own market candidates used to be appended to `brief` only, which
    the angle-mode research runs never read — they rebuild their own. The whole point of the
    harness-side lookup is to inform the runs that do the market check."""

    ANGLES = ["F", "D", "A"]
    HEADER = "## Market candidates found by the harness"

    def test_every_sighted_angle_run_sees_the_section_and_angle_f_does_not(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stub_market_facts(monkeypatch)
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)),
            fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_with_angles(self.ANGLES))
        assert ok and record is not None
        assert len(agent.calls) == 3
        # angle F is market-blind by design: no mandate, and no harness candidates either
        assert self.HEADER not in agent.calls[0]["prompt"]
        assert "FIXED-CANDIDATE" not in agent.calls[0]["prompt"]
        # every OTHER angle run gets the identical section the dossier path builds
        for call in agent.calls[1:]:
            assert self.HEADER in call["prompt"]
            assert "FIXED-CANDIDATE" in call["prompt"]

    def test_plain_angle_mode_gives_it_to_all_runs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stub_market_facts(monkeypatch)
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
        ], config=config_parallel())
        assert ok and record is not None
        assert len(agent.calls) == 3
        for call in agent.calls:  # the three P runs
            assert self.HEADER in call["prompt"]

    def test_blind_mode_still_sees_no_market_section(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stub_market_facts(monkeypatch)
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)),
            fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_with_angles(self.ANGLES), blind=True)
        assert ok and record is not None
        for call in agent.calls:
            assert self.HEADER not in call["prompt"]
            assert "FIXED-CANDIDATE" not in call["prompt"]

    def test_the_dossier_path_brief_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Byte-identical placement: mandate, then the market section, at the end of the brief.
        stub_market_facts(monkeypatch)
        merged = json.loads(json.dumps(DEFAULTS))
        merged["tiers"] = {"medium": {"draws": 8, "searches": 5, "runs": 1, "run_models": [],
                                      "min_sources": 1, "run_angles": []}}
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(research_payload(0.30))],
                                config=merged, effort="medium")
        assert ok and record is not None
        prompt = agent.calls[0]["prompt"]
        assert prompt.endswith("\n" + FIXED_MARKET_SECTION)
        assert "\n\n## Crowd signals\n" in prompt.split(self.HEADER)[0]


class TestSingleRunCounterfactual:
    """*_run1 is the single-run counterfactual: the first independent research run."""

    def test_mc_probabilities_run1_is_the_first_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs = [{"A": 0.6, "B": 0.3, "C": 0.1},
                {"A": 0.5, "B": 0.3, "C": 0.2},
                {"A": 0.7, "B": 0.2, "C": 0.1}]
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(mc_angle(i, r)) for i, r in enumerate(runs, start=1)
        ], config=config_parallel(), question=MC_Q)
        assert ok and record is not None
        assert record["probabilities_run1"] == runs[0]
        assert record["probabilities_run1"] == record["run_probabilities"][0]

    def test_numeric_percentiles_run1_is_the_first_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(numeric_angle(1, 10, 20, 30, 40, 50)),
            fenced(numeric_angle(2, 20, 30, 40, 50, 60)),
            fenced(numeric_angle(3, 30, 40, 50, 60, 70)),
        ], config=config_parallel(), question=NUMERIC_Q)
        assert ok and record is not None
        assert "run_percentiles_phase1" not in record
        assert record["percentiles_run1"] == record["run_percentiles"][0]
        assert record["percentiles_run1"] == pcts(10, 20, 30, 40, 50)


class TestEveryCallIsBoundedByTheWallClock:
    """FIX E: --timeout is per call and a question makes several of them, so one hung call could
    outlive the tick's deadline. Each call is now also capped by the remaining wall clock."""

    def test_a_near_deadline_run_shortens_every_agent_timeout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        deadline = time.monotonic() + 200.0
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
        ], config=config_parallel(), deadline=deadline, timeout=3600)
        assert ok and record is not None
        assert len(agent.calls) == 3
        for call in agent.calls:
            assert 60 <= call["timeout"] <= 200

    def test_without_a_deadline_the_timeout_is_the_flag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)), fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_parallel(), timeout=3600)
        assert ok and record is not None
        assert {call["timeout"] for call in agent.calls} == {3600}

    def test_the_floor_keeps_an_almost_spent_clock_from_killing_a_call_instantly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # 10 s left: the slot is still allowed to start (the deadline has not passed), and
        # the call gets the 60 s floor rather than a 10 s — or negative — timeout.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
        ], config=config_parallel(), deadline=time.monotonic() + 10.0, timeout=3600)
        assert ok and record is not None
        assert agent.calls and {call["timeout"] for call in agent.calls} == {60}

    def test_a_spent_clock_starts_no_run_at_all(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The pre-existing gates still win: past the deadline, no slot is started.
        agent, _, ok = run(monkeypatch, tmp_path, [fenced(research_payload(0.30))],
                           config=config_parallel(),
                           deadline=time.monotonic() - 5.0, timeout=3600)
        assert not ok and agent.calls == []
