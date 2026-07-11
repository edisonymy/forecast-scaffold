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
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

import run_bot  # noqa: E402

from forecast_scaffold.core import DEFAULTS, Journal, geo_mean_odds  # noqa: E402

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
        client: Any = None) -> tuple[ScriptedAgent, dict[str, Any] | None, bool]:
    agent = ScriptedAgent(outputs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_bot, "verify_dossier", lambda *a, **k: ("", 0.0))
    args = argparse.Namespace(
        blind=blind, effort=effort, provider="subscription", timeout=60,
        dry_run=True, comment=False, budget=0.0,
        agent_cmd=("claude -p --model claude-sonnet-5 --output-format json "
                   "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch"),
    )
    journal_path = tmp_path / "j.jsonl"
    journal = Journal(str(journal_path))
    spent = {"usd": 0.0}
    ok = run_bot.forecast_question(
        client or StubClient(), POST, QUESTION, args, config, journal, spent, None,
    )
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

    def test_defaults_ship_run_angles_dark(self) -> None:
        for tier in ("low", "medium", "high"):
            assert DEFAULTS["tiers"][tier]["run_angles"] == []
