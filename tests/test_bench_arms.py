"""The decisive-experiment arms in bench/run_bench.py (plain + angles).

PLAIN is the research-capable MINIMAL-prompt baseline (FutureSearch's directive + the
shared contract/hygiene tail, none of the skill's method). ANGLES reproduces the bot's
angle mode for the bench: one INDEPENDENT full-research run per angle letter, pooled by
geo_mean_odds, as a single row per question.

Stubs follow tests/test_spine.py's ScriptedAgent pattern: run_agent is monkeypatched with
scripted fenced-JSON outputs and every call is recorded, so per-run system prompts,
commands, and briefs are assertable alongside the pooled record.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bot"))

import run_bench  # noqa: E402

from forecast_scaffold.core import geo_mean_odds  # noqa: E402

SPEC = {
    "id": "btf2:q1",
    "source": "btf2",
    "question": "Will X happen?",
    "criteria": "Resolves YES if X happens by the date.",
    "resolve_by": "2026-12-31",
    "background": "Some context about X.",
    "crowd": {"value": 0.5},
}


def fenced(payload: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


def payload(prob: float, **extra: Any) -> dict[str, Any]:
    return {"probability": prob, "reasoning": "researched", "sources": [], **extra}


PAYLOAD = fenced(payload(0.42))


class ScriptedAgent:
    """Replaces run_bench.run_agent; returns scripted replies and records every call
    (mirrors tests/test_spine.py's ScriptedAgent — cost 0.01 per call)."""

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
        return out, 0.01, "claude-sonnet-5"


def base_args(**overrides: Any) -> argparse.Namespace:
    defaults = dict(
        provider="subscription", agent_cmd="claude -p", timeout=60,
        tier_config=None, spine_text=None, spine_arm=None, spine_sha=None,
        leakfree="off", corpus=None, angle_list=None, auto_mode="router", budget=0.0,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# --- PLAIN tier --------------------------------------------------------------


class TestPlainSystemPrompt:
    def test_has_the_minimal_directive_and_the_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "plain", base_args())
        assert row is not None and row["tier"] == "plain" and row["effort"] == "plain"
        system = agent.calls[0]["system"]
        # FutureSearch's baseline directive
        assert "produce the most accurate probabilistic forecast you can" in system
        # the mandatory output contract (probability/reasoning/sources fenced json)
        assert "END your reply with exactly one fenced json block" in system
        assert '"probability"' in system and '"sources"' in system
        # the safety/leak-hygiene sections
        assert "## Untrusted input (security)" in system
        assert "## Blind mode (mandatory)" in system

    def test_omits_the_skill_method_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "plain", base_args())
        assert row is not None
        system = agent.calls[0]["system"]
        # the distinctive tier-method phrase build_system injects — absent here
        assert "Run it at effort tier" not in system
        # ...and none of the method's tiers/draws/dossier/floors leak in
        assert "reference_class" not in system
        assert "raw_draws" not in system
        assert "dossier" not in system.lower()

    def test_plain_is_a_research_tier_under_timevault_corpus(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Same tool treatment as any research tier: the time-locked vault tools (and the
        # corpus tools when --corpus is wired) reach the agent command.
        monkeypatch.setattr(run_bench, "_MCP_CONFIG_CACHE", {})
        monkeypatch.setattr(run_bench, "_MCP_CONFIG_DIR", [])
        corpus = tmp_path / "c.sqlite"
        corpus.write_text("x", encoding="utf-8")
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        spec = dict(SPEC, as_of="2025-10-23 10:54:07")
        args = base_args(
            leakfree="timevault", corpus=str(corpus),
            agent_cmd=("claude -p --model claude-sonnet-5 --output-format json "
                       "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch"),
        )
        row = run_bench.forecast_one(spec, "plain", args)
        assert row is not None and row["leakfree"] == "timevault"
        cmd = agent.calls[0]["cmd"]
        assert "mcp__timevault__search_news" in cmd            # research-capable
        assert run_bench.CORPUS_TOOLS in cmd                   # corpus discovery wired in
        assert "--strict-mcp-config" in cmd


class TestPlainAndAnglesGracefulRejection:
    """Both new research tiers skip (journal nothing) when leak control has nothing to
    anchor to — a set row with no as-of instant can't be pastcast."""

    @pytest.mark.parametrize("tier", ["plain", "angles"])
    def test_skips_when_leakfree_has_no_as_of(
        self, monkeypatch: pytest.MonkeyPatch, tier: str
    ) -> None:
        agent = ScriptedAgent([PAYLOAD, PAYLOAD, PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        # SPEC's background carries no AS-OF line and there is no as_of field.
        row = run_bench.forecast_one(SPEC, tier, base_args(leakfree="timevault"))
        assert row is None
        assert agent.calls == []  # skipped before any agent call


# --- ANGLES tier -------------------------------------------------------------


class TestAnglesTier:
    def test_three_subruns_with_their_sections_pooled_by_gmo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([
            fenced(payload(0.30)),  # F
            fenced(payload(0.50)),  # D
            fenced(payload(0.40)),  # A
        ])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "angles", base_args(angle_list=["F", "D", "A"]))
        assert row is not None
        assert len(agent.calls) == 3  # exactly one independent run per angle
        assert "Angle F — fundamentals" in agent.calls[0]["system"]
        assert "Angle D — decomposition" in agent.calls[1]["system"]
        assert "Angle A — anomaly hunt" in agent.calls[2]["system"]
        # each sub-run is a full build_system-high research run (not zero/plain)
        for call in agent.calls:
            assert "Assigned research angle" in call["system"]
        # the row: pooled probability, per-angle draws, and the angle-set provenance
        assert row["tier"] == "angles" and row["effort"] == "angles"
        assert row["angles"] == "F,D,A"
        assert row["raw_draws"] == [0.30, 0.50, 0.40]
        assert row["n_draws"] == 3
        assert row["probability"] == pytest.approx(geo_mean_odds([0.30, 0.50, 0.40]))
        # hand-computed gmo of odds: cbrt((3/7)*(1)*(4/6)) / (1 + that) ≈ 0.3971
        assert row["probability"] == pytest.approx(0.39711, abs=1e-3)

    def test_every_subrun_is_blind_and_no_crowd_reaches_any_brief(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bench is ALWAYS blind, so angle F's by-design market-blindness is ambient: every
        # angle carries the tool-level blind denylist and the blind prompt section.
        agent = ScriptedAgent([
            fenced(payload(0.30)), fenced(payload(0.50)), fenced(payload(0.40)),
        ])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "angles", base_args(angle_list=["F", "D", "A"]))
        assert row is not None
        for call in agent.calls:
            assert run_bench.BLIND_DISALLOWED in call["cmd"]        # tool-level blindness
            assert "## Blind mode (mandatory)" in call["system"]    # prompt-level blindness
            # no crowd value or crowd-scan mandate ever enters a bench brief
            assert "Community prediction" not in call["prompt"]
            assert "Crowd signals" not in call["prompt"]

    def test_honors_the_angles_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = ScriptedAgent([fenced(payload(0.20)), fenced(payload(0.60))])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "angles", base_args(angle_list=["D", "A"]))
        assert row is not None
        assert len(agent.calls) == 2
        assert "Angle D — decomposition" in agent.calls[0]["system"]
        assert "Angle A — anomaly hunt" in agent.calls[1]["system"]
        assert row["angles"] == "D,A"
        assert row["raw_draws"] == [0.20, 0.60]
        assert row["probability"] == pytest.approx(geo_mean_odds([0.20, 0.60]))

    def test_defaults_to_F_D_A_when_no_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([
            fenced(payload(0.30)), fenced(payload(0.50)), fenced(payload(0.40)),
        ])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "angles", base_args())  # angle_list=None
        assert row is not None and row["angles"] == "F,D,A"

    def test_budget_accounting_sums_every_subrun_into_one_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([
            fenced(payload(0.30)), fenced(payload(0.50)), fenced(payload(0.40)),
        ])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "angles", base_args())
        assert row is not None
        # three sub-runs at 0.01 each roll into the single row's cost
        assert row["cost_usd"] == pytest.approx(0.03)

    def test_a_failed_angle_journals_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The D angle dies both attempts; the whole question fails (no partial row).
        agent = ScriptedAgent([
            fenced(payload(0.30)),               # F ok
            "AGENT_FAILURE", "AGENT_FAILURE",    # D fails both attempts
        ])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "angles", base_args(angle_list=["F", "D", "A"]))
        assert row is None


class TestAnglesCliValidation:
    def test_unknown_angle_letter_errors_at_startup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        set_path = tmp_path / "set.jsonl"
        set_path.write_text(json.dumps(SPEC) + "\n", encoding="utf-8")
        monkeypatch.setattr(run_bench, "RESULTS_DIR", tmp_path / "results")

        def boom(*_a: Any, **_k: Any) -> tuple[str, float, str]:
            raise AssertionError("no agent should run when an angle letter is unknown")

        monkeypatch.setattr(run_bench, "run_agent", boom)
        with pytest.raises(SystemExit):
            run_bench.main([str(set_path), "--tiers", "angles", "--angles", "F,Z"])
        err = capsys.readouterr().err
        assert "unknown research angle" in err and "Z" in err


class TestBenchWiringAndResumability:
    def test_angles_row_written_via_main(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        set_path = tmp_path / "set.jsonl"
        set_path.write_text(json.dumps(SPEC) + "\n", encoding="utf-8")
        monkeypatch.setattr(run_bench, "RESULTS_DIR", tmp_path / "results")
        agent = ScriptedAgent([
            fenced(payload(0.30)), fenced(payload(0.50)), fenced(payload(0.40)),
        ])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        code = run_bench.main([str(set_path), "--tiers", "angles"])
        assert code == 0
        results_path = tmp_path / "results" / "set.results.jsonl"
        rows = [json.loads(line) for line in
                results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 1  # one row per question, not one per angle
        assert rows[0]["tier"] == "angles" and rows[0]["angles"] == "F,D,A"
        assert rows[0]["raw_draws"] == [0.30, 0.50, 0.40]

    def test_plain_row_written_via_main(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        set_path = tmp_path / "set.jsonl"
        set_path.write_text(json.dumps(SPEC) + "\n", encoding="utf-8")
        monkeypatch.setattr(run_bench, "RESULTS_DIR", tmp_path / "results")
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        code = run_bench.main([str(set_path), "--tiers", "plain"])
        assert code == 0
        results_path = tmp_path / "results" / "set.results.jsonl"
        row = json.loads(results_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["tier"] == "plain" and row["probability"] == 0.42
        assert "angles" not in row  # plain rows carry no angle-set field

    def test_done_angles_row_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        set_path = tmp_path / "set.jsonl"
        set_path.write_text(json.dumps(SPEC) + "\n", encoding="utf-8")
        results = tmp_path / "results"
        results.mkdir()
        done_row = {"qid": SPEC["id"], "source": "btf2", "tier": "angles", "run": 0,
                    "probability": 0.4, "angles": "F,D,A", "cost_usd": 0.03}
        (results / "set.results.jsonl").write_text(
            json.dumps(done_row) + "\n", encoding="utf-8")
        monkeypatch.setattr(run_bench, "RESULTS_DIR", results)

        def boom(*_a: Any, **_k: Any) -> tuple[str, float, str]:
            raise AssertionError("a completed (qid, angles) row must not re-run any angle")

        monkeypatch.setattr(run_bench, "run_agent", boom)
        code = run_bench.main([str(set_path), "--tiers", "angles"])
        assert code == 0
        rows = [json.loads(line) for line in
                (results / "set.results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        assert len(rows) == 1  # still just the pre-existing row; nothing new ran
