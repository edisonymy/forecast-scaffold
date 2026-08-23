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
    # v0.4.26 dispersion contract: research runs on continuous types must state the
    # analysis-implied 10-90 width and its basis; declared 40 >= 0.75*45 passes the check.
    "dispersion_90_10": 45, "dispersion_basis": "SD of quarterly changes 17.6 x 2.56",
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


class TestRepairedOnRetryMarker:
    """A payload accepted on attempt 2 must leave a trace: today it is otherwise
    indistinguishable from a clean first attempt (``errors`` resets to [] on success and
    nothing is printed or journaled). The one_run loop knows ``attempt`` — this is the
    natural point to say so."""

    def test_marker_prints_when_accepted_on_retry(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
            capsys: pytest.CaptureFixture[str]) -> None:
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(THIN_MC), fenced(RESEARCHED_MC)],
                                tiers(min_sources=3), MC_Q)
        assert ok and record is not None
        out = capsys.readouterr().out
        assert f"repaired on retry: {MC_Q['id']}" in out
        # names what was repaired: the source-floor rejection that forced the retry.
        assert "0 distinct source(s)" in out

    def test_no_marker_on_clean_first_attempt(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
            capsys: pytest.CaptureFixture[str]) -> None:
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(RESEARCHED_MC)],
                                tiers(min_sources=3), MC_Q)
        assert ok and record is not None and len(agent.calls) == 1
        out = capsys.readouterr().out
        assert "repaired on retry" not in out


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


class TestResearchChecklist:
    """v0.4.24: the record-only checklist ships in the research run's guidance and in the
    skill's references/research.md. The reasoning TIPS drafted beside it failed their
    preregistered A/B and must never appear in a prompt; what makes these items shippable is
    precisely that they are record-only — so the direction ban is a test, not a convention.

    DELIBERATE SURFACE DIVERGENCE (post-ship red team, 2026-08-09): the market-metadata item
    appears ONLY in the skill reference. The bot constant reaches blind runs and the
    market-blind angle F, where "record the market price" would contradict BLIND_SECTION;
    sighted bot runs get the recording detail inside ``crowd_signals`` (which carries the
    contract-match guardrail). Tests below pin both the shared sentences and the divergence."""

    # Whitespace-normalized, `**` stripped: the bot prompt wraps at ~95 chars with no bold,
    # research.md wraps at ~98 with bold lead-ins, and the SENTENCES must still be identical.
    ITEMS = (
        'Deciding-body calendar: term dates, recesses, scheduled sessions, bulletin cadence. '
        'Record a found schedule as a fact; record a not-found schedule as "searched, absent" '
        "— nothing more.",
        "Trend questions: current level, current rate, the rate's own trajectory, one named "
        "regime-break candidate in each direction, and whether simple continuation exits the "
        "range by the deadline.",
        "Named resolution source: when it next updates relative to the deadline.",
    )
    MARKET_ITEM_SKILL = (
        "Any relevant market: price, venue, liquidity/volume, timestamp of the last meaningful "
        "move — after checking the contract actually matches the question's resolution terms "
        "(threshold, deadline, source, fine print); a near-miss contract is evidence, not an "
        "anchor."
    )

    @staticmethod
    def flat(text: str) -> str:
        return " ".join(text.replace("**", "").split())

    def test_checklist_reaches_the_research_run(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        agent, _, ok = run(monkeypatch, tmp_path, [fenced(RESEARCHED_MC)],
                           tiers(min_sources=3), MC_Q)
        assert ok
        system = self.flat(agent.calls[0]["system"])
        assert "## Research checklist (record-only)" in system
        for item in self.ITEMS:
            assert item in system, item

    def test_reasoning_runs_do_not_carry_it(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Prompt real estate is a measured risk: research guidance goes to the run that
        # researches, not to the reasoning runs working from its dossier.
        research = {"probability": 0.30, "dossier": "- fact A (src, 2026)",
                    "reasoning": "researched", "reference_class": "R", "base_rate": 0.2,
                    "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        reasoning = {"probability": 0.35, "reasoning": "x", "sources": [],
                     "named_scenarios": []}
        agent, _, ok = run(monkeypatch, tmp_path,
                           [fenced(research), fenced(reasoning), fenced(reasoning)],
                           tiers(min_sources=3, runs=3), BINARY_Q)
        assert ok
        assert "Research checklist" in agent.calls[0]["system"]
        assert "Research checklist" not in agent.calls[1]["system"]

    def test_skill_reference_carries_the_same_items(self) -> None:
        text = self.flat(
            (ROOT / "skills" / "forecast" / "references" / "research.md")
            .read_text(encoding="utf-8")
        )
        assert "## Record these facts" in text
        for item in self.ITEMS:
            assert item in text, item
        assert self.MARKET_ITEM_SKILL in text

    def test_market_item_stays_off_the_bot_constant_but_in_crowd_signals(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # The bot checklist must stay blind-safe: no market instruction in the constant
        # (it reaches blind runs and angle F). Sighted runs get the recording detail via
        # crowd_signals in the BRIEF instead, with the dossier-anchor warning attached.
        from bot.run_bot import RESEARCH_CHECKLIST_SECTION
        assert "market" not in RESEARCH_CHECKLIST_SECTION.lower()
        research = {"probability": 0.30, "dossier": "- fact A (src, 2026)",
                    "reasoning": "researched", "reference_class": "R", "base_rate": 0.2,
                    "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        agent, _, ok = run(monkeypatch, tmp_path, [fenced(research)],
                           tiers(min_sources=3), BINARY_Q)
        assert ok
        brief = self.flat(agent.calls[0]["prompt"])
        assert "Record the price, venue, liquidity/volume" in brief
        assert "not the dossier body" in brief

    def test_no_probability_direction_on_either_surface(self) -> None:
        """Record-only means record-only: nothing here may tell the forecaster which way to
        move, in numbers or in prose (imperative caution text flattens LLM forecasts toward
        50 — arXiv 2506.01578, and the red team flagged directional variants of the
        not-found-schedule bullet specifically)."""
        banned = (
            "single digit", "floor", "ceiling", " cap", "at least", "at most", "no more than",
            "no lower", "no higher", "%", "probability", "likely", "unlikely", "odds",
            "increase", "decrease", "raise", "lower ", "shade", "should be", "treat as",
        )
        research_md = (ROOT / "skills" / "forecast" / "references" / "research.md").read_text(
            encoding="utf-8")
        start = research_md.index("## Record these facts")
        surfaces = {
            "bot": run_bot.RESEARCH_CHECKLIST_SECTION,
            "research.md": research_md[start:research_md.index("## Red-team")],
        }
        for name, surface in surfaces.items():
            lowered = surface.lower()
            for word in banned:
                assert word not in lowered, f"{name}: directional wording {word!r}"


class TestDefaults:
    def test_every_tier_ships_a_floor(self) -> None:
        assert DEFAULTS["tiers"]["low"]["min_sources"] == 1
        assert DEFAULTS["tiers"]["medium"]["min_sources"] == 3
        assert DEFAULTS["tiers"]["high"]["min_sources"] == 5

    def test_floor_never_exceeds_the_search_budget(self) -> None:
        for tier, params in DEFAULTS["tiers"].items():
            assert params["min_sources"] <= params["searches"], tier


class TestDispersionFloor:
    """v0.4.26: research runs on continuous types must state dispersion_90_10 (the 10-90
    width their own analysis implies) with a named basis, and validate_payload refuses a
    percentile set materially narrower than it. Motivated by the 2026-08-10 Parana miss
    (q45325, -142.4): the run's reasoning stated an 11-day SD of 0.5 m and its declared
    10-90 implied 0.34 — the prose held the right dispersion, the numbers clipped it."""

    def test_numeric_missing_dispersion_repairs(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        no_disp = {"percentiles": {"10": 10, "25": 20, "50": 30, "75": 40, "90": 50},
                   "reasoning": "researched", "reference_class": "past quarters",
                   "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(no_disp), fenced(RESEARCHED_NUMERIC)],
                                tiers(min_sources=3), NUMERIC_Q)
        assert ok and record is not None
        assert "dispersion_90_10" in agent.calls[1]["prompt"]
        assert record["dispersion_90_10"] == 45
        assert record["dispersion_basis"].startswith("SD of quarterly")

    def test_dispersion_without_basis_repairs(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        no_basis = {**RESEARCHED_NUMERIC}
        del no_basis["dispersion_basis"]
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(no_basis), fenced(RESEARCHED_NUMERIC)],
                                tiers(min_sources=3), NUMERIC_Q)
        assert ok and record is not None
        assert "dispersion_basis" in agent.calls[1]["prompt"]

    def test_percentiles_narrower_than_own_dispersion_repair_keeps_pre_guard(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Declared 10-90 width 8 against self-stated 45: the Parana failure shape. The
        # repair widens; the record must keep the pre-guard percentiles for paired scoring.
        narrow = {**RESEARCHED_NUMERIC,
                  "percentiles": {"10": 26, "25": 28, "50": 30, "75": 32, "90": 34}}
        agent, record, ok = run(monkeypatch, tmp_path,
                                [fenced(narrow), fenced(RESEARCHED_NUMERIC)],
                                tiers(min_sources=3), NUMERIC_Q)
        assert ok and record is not None
        retry = agent.calls[1]["prompt"]
        assert "narrower than 0.75x your own stated dispersion_90_10" in retry
        assert record["percentiles"]["90"] == 50.0
        assert record["percentiles_pre_guard"] == {
            "10": 26.0, "25": 28.0, "50": 30.0, "75": 32.0, "90": 34.0}

    def test_consistent_tight_forecast_passes_untouched(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # A pegged-FX-style question: tight percentiles WITH a tight self-stated dispersion
        # pass on the first attempt — the guard enforces consistency, not width.
        tight = {**RESEARCHED_NUMERIC,
                 "percentiles": {"10": 29, "25": 29.5, "50": 30, "75": 30.5, "90": 31},
                 "dispersion_90_10": 2.2,
                 "dispersion_basis": "central-bank band, daily SD 0.85 x 2.56"}
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(tight)],
                                tiers(min_sources=3), NUMERIC_Q)
        assert ok and record is not None and len(agent.calls) == 1
        assert record.get("percentiles_pre_guard") is None

    def test_large_declared_escape_mass_skips_the_width_check(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # With >5% declared escape the percentiles are conditional-on-inside and may be
        # legitimately narrower than the unconditional dispersion (the Nino q45299 shape).
        q = {**NUMERIC_Q, "open_upper_bound": True}
        conditional = {**RESEARCHED_NUMERIC,
                       "percentiles": {"10": 26, "25": 28, "50": 30, "75": 32, "90": 34},
                       "p_above_upper": 0.45}
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(conditional)],
                                tiers(min_sources=3), q)
        assert ok and record is not None and len(agent.calls) == 1

    def test_min_sources_zero_does_not_require_dispersion(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        no_disp = {"percentiles": {"10": 10, "25": 20, "50": 30, "75": 40, "90": 50},
                   "reasoning": "from dossier", "sources": []}
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(no_disp)],
                                tiers(min_sources=0), NUMERIC_Q)
        assert ok and record is not None and len(agent.calls) == 1

    def test_binary_research_run_needs_no_dispersion(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        payload = {"probability": 0.4, "reasoning": "researched",
                   "sources": ["https://s/1", "https://s/2", "https://s/3"]}
        agent, record, ok = run(monkeypatch, tmp_path, [fenced(payload)],
                                tiers(min_sources=3), BINARY_Q)
        assert ok and record is not None and len(agent.calls) == 1

    def test_nonsense_dispersion_is_repairable_feedback(self) -> None:
        pcts = {"percentiles": {"10": 10.0, "25": 20.0, "50": 30.0, "75": 40.0, "90": 50.0}}
        q = {**NUMERIC_Q, "scaling": {"range_min": 0.0, "range_max": 100.0}}
        errors = run_bot.validate_payload({**pcts, "dispersion_90_10": "wide"}, q)
        assert errors == ["dispersion_90_10 must be a number, got 'wide'"]
        errors = run_bot.validate_payload({**pcts, "dispersion_90_10": -3}, q)
        assert errors and "must be > 0" in errors[0]

    def test_contract_offers_the_dispersion_fields(self) -> None:
        assert "dispersion_90_10" in run_bot.CONTRACT
        assert "dispersion_basis" in run_bot.CONTRACT
