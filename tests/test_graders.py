"""The eval graders are pure functions — unit-test them on synthetic conduct."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from forecast_scaffold.core import DEFAULTS

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "behavioral_evals", ROOT / "scripts" / "behavioral_evals.py"
)
assert spec is not None and spec.loader is not None
evals = importlib.util.module_from_spec(spec)
sys.modules["behavioral_evals"] = evals
spec.loader.exec_module(evals)


GOOD_RECORD: dict[str, Any] = {
    "question": "Will Project Atlas ship by 2027-03-31?",
    "status": "open",
    "probability": 0.68,
    "resolution_criterion": "the internal 'shipped' announcement email goes out by the date",
    "resolve_by": "2027-03-31",
}


def test_grader_clamp_band_mirrors_core_defaults() -> None:
    assert DEFAULTS["clamp"]["min"] == evals.CLAMP_MIN
    assert DEFAULTS["clamp"]["max"] == evals.CLAMP_MAX


def test_scorable_record_grader() -> None:
    assert evals.grade_scorable_record("I put it at 68%.", [GOOD_RECORD]).passed
    assert not evals.grade_scorable_record("done", []).passed
    assert not evals.grade_scorable_record("68%", [dict(GOOD_RECORD, probability=0.995)]).passed
    assert not evals.grade_scorable_record("68%", [dict(GOOD_RECORD, resolve_by=None)]).passed


def test_trivia_grader() -> None:
    assert evals.grade_trivia_stays_out("The capital of France is Paris.", []).passed
    assert not evals.grade_trivia_stays_out("Paris.", [GOOD_RECORD]).passed
    assert not evals.grade_trivia_stays_out("London.", []).passed


def test_resolve_idempotent_grader() -> None:
    seeded = dict(
        GOOD_RECORD,
        status="resolved",
        resolution={"outcome": True, "resolved_on": "2026-06-02", "note": "moved"},
    )
    ok = evals.grade_resolve_idempotent("That record is already resolved (true).", [seeded])
    assert ok.passed
    overwritten = dict(
        seeded, resolution={"outcome": False, "resolved_on": "2026-06-03", "note": ""}
    )
    assert not evals.grade_resolve_idempotent("Done, resolved false.", [overwritten]).passed


def test_already_resolved_trap_grader() -> None:
    assert evals.grade_already_resolved_trap("She died in September 2022.", []).passed
    assert not evals.grade_already_resolved_trap("Recorded at 97%.", [GOOD_RECORD]).passed


def test_vague_operationalized_grader() -> None:
    ask = "What would 'really good' mean? I'd suggest a resolution criterion like..."
    assert evals.grade_vague_operationalized(ask, []).passed
    assert evals.grade_vague_operationalized("Recorded.", [GOOD_RECORD]).passed
    vague = dict(GOOD_RECORD, resolution_criterion="AI is good")
    assert not evals.grade_vague_operationalized("Recorded.", [vague]).passed
    assert not evals.grade_vague_operationalized("Sure thing!", []).passed


def test_scenarios_json_wires_to_existing_graders() -> None:
    scenarios = json.loads((ROOT / "evals" / "scenarios.json").read_text(encoding="utf-8"))
    for scenario in scenarios["scenarios"]:
        assert scenario["grader"] in evals.GRADERS
        assert (ROOT / "skills" / scenario["skill"] / "SKILL.md").exists()
