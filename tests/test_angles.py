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
import shlex
import sys
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
    percentiles_to_cdf,
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
        budget: float = 0.0) -> tuple[ScriptedAgent, dict[str, Any] | None, bool]:
    agent = ScriptedAgent(outputs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_bot, "verify_dossier", lambda *a, **k: ("", 0.0))
    args = argparse.Namespace(
        blind=blind, effort=effort, provider="subscription", timeout=60,
        dry_run=True, comment=False, budget=budget,
        agent_cmd=("claude -p --model claude-sonnet-5 --output-format json "
                   "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch"),
    )
    journal_path = tmp_path / "j.jsonl"
    journal = Journal(str(journal_path))
    spent = {"usd": 0.0}
    ok = run_bot.forecast_question(
        client or StubClient(), POST, question or QUESTION, args, config, journal, spent,
        None,
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


# ---------------------------------------------------------------- phases 2 and 3 (v0.4.28)

def config_with_phases(
    angles: tuple[str, ...] = ("P", "P", "P"), *, share_evidence: bool = False,
    supervisor: bool = False, spread: float = 0.10, spread_iqr: float = 0.75,
    min_sources: int = 1, searches: int = 12,
) -> dict[str, Any]:
    """A full config whose high tier runs angle mode with the phase flags under test."""
    merged = json.loads(json.dumps(DEFAULTS))
    merged["tiers"] = {"high": {
        "draws": 12, "searches": searches, "runs": len(angles), "run_models": [],
        "min_sources": min_sources, "run_angles": list(angles),
        "share_evidence": share_evidence, "supervisor": supervisor,
        "supervisor_search_spread": spread, "supervisor_search_spread_iqr": spread_iqr,
    }}
    return merged


def angle_run(p: float, i: int, **extra: Any) -> dict[str, Any]:
    """A phase-1 angle run: research payload + its estimate-free dossier. The narrative is
    tagged so a test can prove it never reaches a phase-2 prompt."""
    return research_payload(
        p, **{"dossier": f"- DOSSIER-FACT-{i} (src, 2026)",
              "reasoning": f"PRIVATE-NARRATIVE-{i}", **extra},
    )


def phase2_run(p: float, i: int, **extra: Any) -> dict[str, Any]:
    """A phase-2 (shared-evidence) reasoning payload."""
    return {"probability": p, "reasoning": f"PHASE2-NARRATIVE-{i}", "sources": [],
            "named_scenarios": [], **extra}


def supervisor_run(p: float, **extra: Any) -> dict[str, Any]:
    return {"probability": p, "reasoning": "SUPERVISOR-NARRATIVE", "sources": [],
            "reconciliation": "run 2 read the wrong bulletin date; the gazette settles it",
            **extra}


def disallowed_of(call: dict[str, Any]) -> str:
    """The --disallowed-tools value of one recorded agent command."""
    tokens = shlex.split(call["cmd"])
    return tokens[tokens.index("--disallowed-tools") + 1]


class TestSharedEvidencePhase:
    """PHASE 2 (`share_evidence`): every angle run also writes the estimate-free dossier,
    then each is re-asked once with the OTHER runs' dossiers — evidence circulates, numbers
    and reasoning do not. The second round is what gets pooled; the first is journaled."""

    CONFIG = config_with_phases(share_evidence=True)

    SCRIPT = [
        fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
        fenced(phase2_run(0.34, 1)), fenced(phase2_run(0.44, 2)), fenced(phase2_run(0.39, 3)),
    ]

    def test_angle_runs_write_dossiers_and_phase2_circulates_only_evidence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, list(self.SCRIPT), config=self.CONFIG)
        assert ok and record is not None
        assert len(agent.calls) == 6  # 3 research + 3 shared-evidence
        for call in agent.calls[:3]:
            assert "Dossier (multi-run mode" in (call["system"] or "")
        for i, call in enumerate(agent.calls[3:]):
            system = call["system"] or ""
            prompt = call["prompt"]
            assert "Reasoning run (shared dossier)" in system
            assert f"## Your own research dossier (run {i + 1})" in prompt
            assert f"DOSSIER-FACT-{i + 1}" in prompt
            # every OTHER run's dossier is present, labelled by run number
            for j in range(3):
                assert f"DOSSIER-FACT-{j + 1}" in prompt
                if j != i:
                    assert f"### Run {j + 1}" in prompt
            # ...and NOTHING else of theirs: no estimate, no narrative
            assert "PRIVATE-NARRATIVE-1" not in prompt
            assert "PRIVATE-NARRATIVE-2" not in prompt
            assert "PRIVATE-NARRATIVE-3" not in prompt
            for number in ("0.30", "0.50", "0.40"):
                assert number not in prompt
            assert "Nothing above is an estimate" in prompt

    def test_phase2_pool_is_submitted_and_phase1_is_journaled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, record, ok = run(monkeypatch, tmp_path, list(self.SCRIPT), config=self.CONFIG)
        assert ok and record is not None
        assert record["probability"] == pytest.approx(geo_mean_odds([0.34, 0.44, 0.39]))
        assert record["raw_draws"] == [0.34, 0.44, 0.39]
        assert record["raw_draws_phase1"] == [0.30, 0.50, 0.40]
        assert record["pool_phase1"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        assert record["pool_phase2"] == pytest.approx(geo_mean_odds([0.34, 0.44, 0.39]))
        assert record["spread_phase1"] == pytest.approx(0.20)
        assert record["spread_phase2"] == pytest.approx(0.10)
        assert record["aggregation"] == "geo_mean_odds(shared_evidence, angles=P,P,P)"
        # the disclosure names the submitted pool first, then the one it displaced
        lines = record["reasoning"].splitlines()
        assert lines[0].startswith(
            "[pooled 3 independent research runs (angles P,P,P) after sharing evidence")
        assert lines[1].startswith("[phase-1 pool, before the dossiers circulated:")
        assert "supervisor" not in record

    def test_a_collapsed_second_round_keeps_the_phase1_pool(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # One surviving second-round estimate is not an ensemble; phase 1 already is one.
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
            fenced(phase2_run(0.34, 1)),
            "AGENT_FAILURE", "AGENT_FAILURE", "AGENT_FAILURE", "AGENT_FAILURE",
        ], config=self.CONFIG)
        assert ok and record is not None
        assert record["raw_draws"] == [0.30, 0.50, 0.40]
        assert record["aggregation"] == "geo_mean_odds(angles=P,P,P)"
        assert "raw_draws_phase1" not in record and "pool_phase2" not in record

    def test_share_evidence_is_off_by_default(self, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
        # Operator decision 2026-09-03: an experiment switch, not production. With the flag
        # unset the second round must not happen at all.
        for tier in ("low", "medium", "high"):
            assert DEFAULTS["tiers"][tier]["share_evidence"] is False
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
            fenced(supervisor_run(0.45)),
        ], config=config_with_phases(supervisor=True))
        assert ok and record is not None
        assert len(agent.calls) == 4  # 3 research + the supervisor, no second round
        for call in agent.calls:
            assert "Your own research dossier" not in call["prompt"]
            assert "Nothing above is an estimate" not in call["prompt"]
        assert "pool_phase2" not in record and "spread_phase2" not in record


class TestSupervisorPhase:
    """PHASE 3 (`supervisor`): one reconciler sees every dossier and every estimate WITH its
    reasoning, and its number is the submission. The pools it replaced stay journaled."""

    def test_supervisor_sees_dossiers_and_estimates_and_its_number_is_submitted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
            fenced(supervisor_run(0.62)),
        ], config=config_with_phases(supervisor=True))
        assert ok and record is not None
        assert len(agent.calls) == 4
        prompt = agent.calls[3]["prompt"]
        system = agent.calls[3]["system"] or ""
        assert "Supervisor reconciliation (this run" in system
        assert "Do NOT average the runs" in system
        for i in (1, 2, 3):
            assert f"### Run {i} dossier" in prompt
            assert f"DOSSIER-FACT-{i}" in prompt
            assert f"PRIVATE-NARRATIVE-{i}" in prompt  # the reconciler DOES see reasoning
        assert "probability 0.300" in prompt and "probability 0.500" in prompt
        # the submission is the supervisor's own number, not the pool
        assert record["probability"] == pytest.approx(0.62)
        assert record["aggregation"] == "supervisor(angles=P,P,P)"
        assert record["pool_phase1"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        assert record["spread_phase1"] == pytest.approx(0.20)
        assert record["raw_draws"] == [0.30, 0.50, 0.40]  # the members stay auditable
        sup = record["supervisor"]
        assert sup["estimate"] == pytest.approx(0.62)
        assert "gazette" in sup["reconciliation"]
        assert sup["mode"] == "research"
        assert sup["spread"] == pytest.approx(0.20)
        assert sup["sources"] == []
        # the note says which number was submitted and lists the pool it displaced
        head = record["reasoning"].splitlines()[0]
        assert head.startswith("[submitted: the supervisor's reconciliation of 3 ")
        assert "0.620" in head and "phase-1 pool 0.397" in head
        assert "SUPERVISOR-NARRATIVE" in record["reasoning"]

    def test_wide_spread_buys_the_research_capable_call(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # spread 0.20 >= threshold 0.10 -> the runs genuinely disagree, so the reconciler
        # gets web tools and the full research timeout.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
            fenced(supervisor_run(0.45)),
        ], config=config_with_phases(supervisor=True, spread=0.10))
        assert ok and record is not None
        sup_call = agent.calls[3]
        assert "WebSearch" not in disallowed_of(sup_call)
        assert sup_call["timeout"] == 60  # args.timeout, the research leash
        assert "Run targeted searches — at most 12" in (sup_call["system"] or "")
        assert record["supervisor"]["mode"] == "research"

    def test_narrow_spread_takes_the_cheap_reasoning_only_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # spread 0.02 < threshold 0.10 -> nothing factual is in dispute, so no search budget:
        # web tools are DENIED at the CLI, not merely discouraged in the prompt.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.32, 2)), fenced(angle_run(0.31, 3)),
            fenced(supervisor_run(0.33)),
        ], config=config_with_phases(supervisor=True, spread=0.10))
        assert ok and record is not None
        sup_call = agent.calls[3]
        assert "WebSearch" in disallowed_of(sup_call)
        assert "WebFetch" in disallowed_of(sup_call)
        assert run_bot.ALWAYS_DISALLOWED in disallowed_of(sup_call)  # the standing denies stay
        assert "No web access is available" in (sup_call["system"] or "")
        assert "Run targeted searches" not in (sup_call["system"] or "")
        assert record["supervisor"]["mode"] == "reasoning"
        assert record["supervisor"]["spread"] == pytest.approx(0.02)
        assert record["probability"] == pytest.approx(0.33)

    def test_invalid_supervisor_twice_falls_back_to_the_pool(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A payload with no "reconciliation" is rejected; after the repair retry also fails
        # the pooled runs are submitted and the aggregation tag says so.
        no_audit = {"probability": 0.62, "reasoning": "x", "sources": []}
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
            fenced(phase2_run(0.34, 1)), fenced(phase2_run(0.44, 2)), fenced(phase2_run(0.39, 3)),
            fenced(no_audit), fenced(no_audit),
        ], config=config_with_phases(share_evidence=True, supervisor=True))
        assert ok and record is not None
        assert len(agent.calls) == 8
        assert 'must include "reconciliation"' in agent.calls[7]["prompt"]
        assert record["probability"] == pytest.approx(geo_mean_odds([0.34, 0.44, 0.39]))
        assert record["aggregation"] == "geo_mean_odds(shared_evidence, angles=P,P,P)"
        assert "supervisor" not in record
        assert record["pool_phase2"] == pytest.approx(geo_mean_odds([0.34, 0.44, 0.39]))

    def test_budget_exhausted_before_phase_three_falls_back_and_says_so(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Each scripted call costs $0.05; $0.15 is gone by the third angle run, so the
        # supervisor never starts and the pool is submitted.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
            fenced(supervisor_run(0.62)),
        ], config=config_with_phases(supervisor=True), budget=0.15)
        assert ok and record is not None
        assert len(agent.calls) == 3
        assert "budget: skipping the supervisor" in capsys.readouterr().out
        assert record["aggregation"] == "geo_mean_odds(angles=P,P,P)"
        assert "supervisor" not in record
        assert record["pool_phase1"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))

    def test_the_supervisor_call_is_charged_to_the_invocation_budget(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The phases run BEFORE the spend is folded into the invocation ledger, so a bug
        # there would give the reconciler a free call every question.
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
            fenced(supervisor_run(0.62)),
        ], config=config_with_phases(supervisor=True))
        assert ok and record is not None
        assert agent.final_spent == pytest.approx(0.20)  # 4 calls x $0.05
        assert record["cost_usd"] == pytest.approx(0.20)
        assert record["supervisor"]["cost_usd"] == pytest.approx(0.05)

    def test_a_broken_phase_never_costs_the_forecast_or_the_accounting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # A phase is an enhancement on top of a forecast that already exists. If anything in
        # it raises, the pooled runs are submitted and the spend is still accounted.
        def boom(*_a: Any, **_k: Any) -> str:
            raise ValueError("phase exploded")

        monkeypatch.setattr(run_bot, "estimate_summary", boom)
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)), fenced(angle_run(0.50, 2)), fenced(angle_run(0.40, 3)),
        ], config=config_with_phases(supervisor=True))
        assert ok and record is not None
        assert "phase 2/3 skipped (phase exploded)" in capsys.readouterr().out
        assert record["probability"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        assert record["aggregation"] == "geo_mean_odds(angles=P,P,P)"
        assert agent.final_spent == pytest.approx(0.15)

    def test_a_lone_surviving_run_never_reaches_the_reconciler(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(angle_run(0.30, 1)),
            "AGENT_FAILURE", "AGENT_FAILURE", "AGENT_FAILURE", "AGENT_FAILURE",
        ], config=config_with_phases(supervisor=True))
        assert ok and record is not None
        assert len(agent.calls) == 5  # nothing after the angle slots: no pool to reconcile
        assert record["aggregation"] == "single_run(of 3 intended)"
        assert "supervisor" not in record and "pool_phase1" not in record


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
        "dossier": f"- DOSSIER-FACT-{i} (src, 2026)", **extra,
    }


class TestPhasesOnNonBinaryTypes:
    """Both phases pool with the same type-specific functions the single-phase path uses,
    and the reconciler answers in the same type contract."""

    def test_numeric_shared_evidence_pools_by_quantile_mean(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(numeric_angle(1, 10, 20, 30, 40, 50)),
            fenced(numeric_angle(2, 20, 30, 40, 50, 60)),
            fenced(numeric_angle(3, 30, 40, 50, 60, 70)),
            fenced({"percentiles": pcts(15, 25, 35, 45, 55), "reasoning": "p2a",
                    "sources": []}),
            fenced({"percentiles": pcts(25, 35, 45, 55, 65), "reasoning": "p2b",
                    "sources": [], "p_above_upper": 0.1}),
            fenced({"percentiles": pcts(20, 30, 40, 50, 60), "reasoning": "p2c",
                    "sources": []}),
        ], config=config_with_phases(share_evidence=True), question=NUMERIC_Q)
        assert ok and record is not None
        assert len(agent.calls) == 6
        assert record["percentiles"] == pcts(20, 30, 40, 50, 60)
        assert record["run_percentiles_phase1"] == [
            pcts(10, 20, 30, 40, 50), pcts(20, 30, 40, 50, 60), pcts(30, 40, 50, 60, 70)]
        assert record["run_escapes_phase1"] == [[None, None], [None, None], [None, None]]
        assert record["pool_phase1"] == pcts(20, 30, 40, 50, 60)
        assert record["pool_phase2"] == pcts(20, 30, 40, 50, 60)
        # spread is in question units on both phases: medians 30/40/50 vs 35/45/40
        assert record["spread_phase1"] == pytest.approx(20.0)
        assert record["spread_phase2"] == pytest.approx(10.0)
        assert record["aggregation"] == "quantile_mean(shared_evidence, angles=P,P,P)"
        # escape mass averages over the phase-2 runs that declared one
        assert record["p_above_upper"] == pytest.approx(0.1)

    def test_numeric_supervisor_spread_is_measured_in_iqr_units(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # medians 30/40/50 spread 20; pooled IQR 50-30 = 20 -> ratio 1.0 >= 0.75 -> research
        supervisor = {
            "percentiles": pcts(25, 35, 45, 55, 65), "reasoning": "SUPERVISOR-NARRATIVE",
            "sources": ["https://gazette.example/x"], "p_above_upper": 0.12,
            "dispersion_90_10": 40.0, "dispersion_basis": "reconciled SD 15.6 x 2.56",
            "reconciliation": "run 3 used a stale index level; the publisher's table settles it",
        }
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(numeric_angle(1, 10, 20, 30, 40, 50)),
            fenced(numeric_angle(2, 20, 30, 40, 50, 60)),
            fenced(numeric_angle(3, 30, 40, 50, 60, 70)),
            fenced(supervisor),
        ], config=config_with_phases(supervisor=True), question=NUMERIC_Q)
        assert ok and record is not None
        assert "WebSearch" not in disallowed_of(agent.calls[3])
        assert record["supervisor"]["mode"] == "research"
        assert record["supervisor"]["spread"] == pytest.approx(1.0)
        assert record["percentiles"] == pcts(25, 35, 45, 55, 65)
        # the reconciler's own tails ship with its own percentiles
        assert record["p_above_upper"] == pytest.approx(0.12)
        assert record["supervisor"]["estimate"]["percentiles"] == pcts(25, 35, 45, 55, 65)
        assert record["supervisor"]["estimate"]["p_above_upper"] == pytest.approx(0.12)
        assert record["supervisor"]["sources"] == ["https://gazette.example/x"]
        assert record["aggregation"] == "supervisor(angles=P,P,P)"
        assert record["pool_phase1"] == pcts(20, 30, 40, 50, 60)
        # the CDF that ships is built from the reconciled percentiles
        assert record["submitted_cdf"] == percentiles_to_cdf(
            pcts(25, 35, 45, 55, 65), 0.0, 200.0, lower_open=False, upper_open=True,
            zero_point=None, cdf_size=201, p_below_lower=None, p_above_upper=0.12,
            interpolation="pchip",
        )

    def test_numeric_agreement_takes_the_reasoning_only_reconciler(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # medians 30/31/32 spread 2; pooled IQR 41-21 = 20 -> ratio 0.1 < 0.75
        supervisor = {
            "percentiles": pcts(11, 21, 31, 41, 51), "reasoning": "SUPERVISOR-NARRATIVE",
            "sources": [], "reconciliation": "no factual dispute; the runs differ on weighting",
        }
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(numeric_angle(1, 10, 20, 30, 40, 50)),
            fenced(numeric_angle(2, 11, 21, 31, 41, 51)),
            fenced(numeric_angle(3, 12, 22, 32, 42, 52)),
            fenced(supervisor),
        ], config=config_with_phases(supervisor=True), question=NUMERIC_Q)
        assert ok and record is not None
        assert "WebSearch" in disallowed_of(agent.calls[3])
        assert record["supervisor"]["mode"] == "reasoning"
        assert record["supervisor"]["spread"] == pytest.approx(0.1)
        assert record["percentiles"] == pcts(11, 21, 31, 41, 51)

    def test_mc_phases_pool_by_option_and_reconcile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs = [{"A": 0.6, "B": 0.3, "C": 0.1},
                {"A": 0.5, "B": 0.3, "C": 0.2},
                {"A": 0.7, "B": 0.2, "C": 0.1}]
        second = [{"A": 0.62, "B": 0.28, "C": 0.10},
                  {"A": 0.58, "B": 0.30, "C": 0.12},
                  {"A": 0.66, "B": 0.24, "C": 0.10}]
        supervisor = {
            "probabilities": {"A": 0.75, "B": 0.20, "C": 0.05},
            "reasoning": "SUPERVISOR-NARRATIVE", "sources": [],
            "reconciliation": "the B pathway is already closed per the registry",
        }
        agent, record, ok = run(monkeypatch, tmp_path, [
            *[fenced({"probabilities": r, "reasoning": f"PRIVATE-NARRATIVE-{i}",
                      "sources": [f"https://example.com/{i}"], "reference_class": "class R",
                      "base_rate": {"A": 0.5, "B": 0.3, "C": 0.2},
                      "dossier": f"- DOSSIER-FACT-{i} (src, 2026)"})
              for i, r in enumerate(runs, start=1)],
            *[fenced({"probabilities": r, "reasoning": "p2", "sources": []})
              for r in second],
            fenced(supervisor),
        ], config=config_with_phases(share_evidence=True, supervisor=True), question=MC_Q)
        assert ok and record is not None
        assert len(agent.calls) == 7
        assert record["run_probabilities_phase1"] == runs
        assert record["run_probabilities"] == second
        assert record["pool_phase1"]["A"] == pytest.approx(pool_mc(runs)["A"])
        assert record["pool_phase2"]["A"] == pytest.approx(pool_mc(second)["A"])
        # the MC spread is the disagreement on the pooled LEADER (option A)
        assert record["spread_phase1"] == pytest.approx(0.2)
        assert record["spread_phase2"] == pytest.approx(0.08)
        assert record["supervisor"]["mode"] == "reasoning"  # 0.08 < 0.10
        assert record["probabilities"] == [pytest.approx(v) for v in (0.75, 0.20, 0.05)]
        assert record["supervisor"]["estimate"] == {"A": 0.75, "B": 0.20, "C": 0.05}
        assert record["aggregation"] == "supervisor(shared_evidence, angles=P,P,P)"


class TestPhasesLeaveTheUnflaggedPathAlone:
    """Flags off = the v0.4.28 behaviour, byte for byte. The rest of this file's suites are
    the real regression guard (they all run with both flags absent); this pins the record."""

    def test_no_flags_means_no_phase_fields_and_no_extra_calls(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [
            fenced(research_payload(0.30)), fenced(research_payload(0.50)),
            fenced(research_payload(0.40)),
        ], config=config_with_phases())
        assert ok and record is not None
        assert len(agent.calls) == 3
        for call in agent.calls:
            assert "Dossier (multi-run mode" not in (call["system"] or "")
            assert "Supervisor reconciliation" not in (call["system"] or "")
        assert record["aggregation"] == "geo_mean_odds(angles=P,P,P)"
        assert record["reasoning"].startswith(
            "[pooled 3 independent research runs (angles P,P,P)")
        for field in ("pool_phase1", "pool_phase2", "spread_phase1", "spread_phase2",
                      "raw_draws_phase1", "supervisor"):
            assert field not in record

    def test_uniform_research_depth_across_tiers(self) -> None:
        """Operator decision 2026-09-03: a tier sets how much independent JUDGMENT a
        question gets, not how carefully one run reads the world."""
        for tier in ("low", "medium", "high"):
            assert DEFAULTS["tiers"][tier]["searches"] == 5
            assert DEFAULTS["tiers"][tier]["min_sources"] == 3
        assert [DEFAULTS["tiers"][t]["runs"] for t in ("low", "medium", "high")] == [1, 3, 4]

    def test_defaults_ship_the_conditional_supervisor(self) -> None:
        """Operator decision 2026-09-03: the reconciler is production at medium/high; its
        research budget is conditional on the runs actually disagreeing."""
        assert DEFAULTS["tiers"]["low"]["supervisor"] is False
        assert DEFAULTS["tiers"]["medium"]["supervisor"] is True
        assert DEFAULTS["tiers"]["high"]["supervisor"] is True
        assert DEFAULTS["tiers"]["medium"]["supervisor_search_spread"] == 0.10
        assert DEFAULTS["tiers"]["high"]["supervisor_search_spread"] == 0.08
        assert DEFAULTS["tiers"]["medium"]["supervisor_search_spread_iqr"] == 0.75
        assert DEFAULTS["tiers"]["high"]["supervisor_search_spread_iqr"] == 0.5


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
            fenced(phase2_run(0.34, 1)), fenced(phase2_run(0.54, 2)), fenced(phase2_run(0.39, 3)),
            fenced(supervisor_run(0.62, disagreements=[
                {"claim": "the bulletin date", "kind": "factual", "verdict": "gazette wins"}])),
        ], config=config_with_phases(share_evidence=True, supervisor=True))
        assert ok and record is not None
        assert record["trace_path"] == f"traces/{record['id']}.json"
        trace = self.trace_of(tmp_path, record)
        assert len(trace["calls"]) == len(agent.calls) == 7
        assert trace["architecture"] == "angle"
        assert [c["stage"] for c in trace["calls"]] == (
            ["angle_research"] * 3 + ["shared_evidence"] * 3 + ["supervisor"])
        assert [c["phase"] for c in trace["calls"]] == [1, 1, 1, 2, 2, 2, 3]
        # each phase-1 run carries its OWN estimate, narrative, sources and dossier
        for i, call in enumerate(trace["calls"][:3], start=1):
            assert call["mode"] == "research" and call["angle"] == "P"
            assert call["run_index"] == i
            assert call["reasoning"] == f"PRIVATE-NARRATIVE-{i}"
            assert f"DOSSIER-FACT-{i}" in call["dossier"]
            assert call["sources"] and call["model"] == "claude-sonnet-5"
            assert call["cost_usd"] == pytest.approx(0.05)
            assert isinstance(call["seconds"], (int, float))
        assert [c["estimate"] for c in trace["calls"][:3]] == [0.30, 0.50, 0.40]
        assert [c["estimate"] for c in trace["calls"][3:6]] == [0.34, 0.54, 0.39]
        supervisor = trace["calls"][6]
        assert supervisor["mode"] == "research" and supervisor["estimate"] == 0.62
        assert "gazette" in supervisor["reconciliation"]
        assert supervisor["disagreements"][0]["kind"] == "factual"
        # ...and the file also carries the pools and what was actually submitted
        assert trace["pools"]["phase1"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        assert trace["pools"]["phase2"] == pytest.approx(geo_mean_odds([0.34, 0.54, 0.39]))
        assert trace["pools"]["spread_phase1"] == pytest.approx(0.20)
        assert trace["pools"]["pooled_runs"] == pytest.approx(
            geo_mean_odds([0.34, 0.54, 0.39]))
        assert trace["submitted"]["probability"] == pytest.approx(0.62)
        assert trace["aggregation"] == "supervisor(shared_evidence, angles=P,P,P)"

    def test_failed_runs_and_repairs_are_in_the_trace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        thin = {"probability": 0.30, "reasoning": "x", "sources": ["https://only.one"],
                "reference_class": "class R", "base_rate": 0.2,
                "dossier": "- DOSSIER-FACT-1 (src, 2026)"}
        _, record, ok = run(monkeypatch, tmp_path, [
            fenced(thin), fenced(angle_run(0.30, 1)),   # run 1 repairs
            "AGENT_FAILURE", "AGENT_FAILURE",           # run 2 dies outright
            fenced(angle_run(0.40, 3)),
        ], config=config_with_phases(supervisor=False, min_sources=3))
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
        ], config=config_with_phases(supervisor=False))
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
        ], config=config_with_phases(supervisor=False))
        assert ok and record is not None  # the forecast is recorded regardless
        assert "trace not written" in capsys.readouterr().out
