"""Behavioral evals: do the skills produce the right CONDUCT, not just the right words?

Each scenario in evals/scenarios.json is run through ``claude -p`` (or any compatible
headless agent CLI via --agent-cmd) with the scenario's SKILL.md injected as extra system
prompt and a fresh temp journal exposed via $FORECAST_JOURNAL. Pure grader functions then
inspect (final answer text, journal records) — e.g. a factual question must leave the
journal EMPTY, and an already-resolved record must survive an overwrite request.

Graders are unit-tested in tests/test_graders.py; running the scenarios themselves needs a
local agent CLI and is a dev-time check, not a CI gate.

Usage:
    python scripts/behavioral_evals.py            # run all scenarios
    python scripts/behavioral_evals.py --only trivia-stays-out
    python scripts/behavioral_evals.py --agent-cmd "claude -p"
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "evals" / "scenarios.json"
CLAMP_MIN, CLAMP_MAX = 0.02, 0.98  # mirror of core DEFAULTS, asserted in tests


@dataclass
class Verdict:
    passed: bool
    reason: str


# ------------------------------------------------------------------ graders (pure)


def grade_scorable_record(answer: str, records: list[dict[str, Any]]) -> Verdict:
    if len(records) != 1:
        return Verdict(False, f"expected exactly 1 journal record, found {len(records)}")
    r = records[0]
    if r.get("status") != "open":
        return Verdict(False, f"record status is {r.get('status')!r}, expected open")
    p = r.get("probability")
    if p is None:
        return Verdict(False, "record has no probability")
    if not CLAMP_MIN <= p <= CLAMP_MAX:
        return Verdict(False, f"probability {p} outside clamp band")
    if not r.get("resolution_criterion"):
        return Verdict(False, "record has no resolution_criterion")
    if not r.get("resolve_by"):
        return Verdict(False, "record has no resolve_by")
    if "%" not in answer:
        return Verdict(False, "answer never states a probability")
    return Verdict(True, "scorable record landed with criterion, date, in-band probability")


def grade_trivia_stays_out(answer: str, records: list[dict[str, Any]]) -> Verdict:
    if records:
        return Verdict(False, f"{len(records)} record(s) written for a factual lookup")
    if "paris" not in answer.lower():
        return Verdict(False, "did not answer the factual question")
    return Verdict(True, "answered directly, journal untouched")


def grade_resolve_idempotent(answer: str, records: list[dict[str, Any]]) -> Verdict:
    if len(records) != 1:
        return Verdict(False, f"expected the 1 seeded record, found {len(records)}")
    resolution = records[0].get("resolution") or {}
    if resolution.get("outcome") is not True:
        return Verdict(False, "the seeded resolution was overwritten")
    lowered = answer.lower()
    if "already" not in lowered and "resolved" not in lowered:
        return Verdict(False, "answer does not surface that the record was already resolved")
    return Verdict(True, "refused the silent overwrite and said why")


def grade_already_resolved_trap(answer: str, records: list[dict[str, Any]]) -> Verdict:
    open_records = [r for r in records if r.get("status") == "open"]
    if open_records:
        return Verdict(False, "recorded an open forecast on already-resolved history")
    return Verdict(True, "no open forecast on a question whose answer is already known")


def grade_vague_operationalized(answer: str, records: list[dict[str, Any]]) -> Verdict:
    if not records:
        lowered = answer.lower()
        markers = ("criterion", "resolve", "operationaliz", "specific", "mean by", "define")
        if any(m in lowered for m in markers):
            return Verdict(True, "asked to operationalize instead of recording the vague ask")
        return Verdict(False, "recorded nothing but also never addressed resolvability")
    r = records[0]
    criterion = r.get("resolution_criterion", "")
    if len(criterion) < 20 or not r.get("resolve_by"):
        return Verdict(False, "recorded a question without a real criterion/date")
    return Verdict(True, "recorded an operationalized version with criterion and date")


GRADERS = {
    "grade_scorable_record": grade_scorable_record,
    "grade_trivia_stays_out": grade_trivia_stays_out,
    "grade_resolve_idempotent": grade_resolve_idempotent,
    "grade_already_resolved_trap": grade_already_resolved_trap,
    "grade_vague_operationalized": grade_vague_operationalized,
}


# ------------------------------------------------------------------ runner


def _read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _seed_journal(path: Path, seeds: list[dict[str, Any]]) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from forecast_scaffold.core import ForecastRecord  # noqa: PLC0415

    with path.open("w", encoding="utf-8") as fh:
        for seed in seeds:
            fh.write(ForecastRecord.from_dict(seed).to_json() + "\n")


def run_scenario(scenario: dict[str, Any], agent_cmd: str, timeout: int) -> Verdict:
    skill_dir = ROOT / "skills" / scenario["skill"]
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "forecasts.jsonl"
        if scenario.get("seed_journal"):
            _seed_journal(journal, scenario["seed_journal"])
        env = dict(os.environ, FORECAST_JOURNAL=str(journal))
        system = (
            f"You have this skill available (its scripts live at {skill_dir / 'scripts'}):\n\n"
            + skill_text
        )
        cmd = [
            *shlex.split(agent_cmd),
            scenario["prompt"],
            "--append-system-prompt",
            system,
            "--allowed-tools",
            "Bash,Read",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=tmp
        )
        answer = result.stdout.strip()
        return GRADERS[scenario["grader"]](answer, _read_journal(journal))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run a single scenario id")
    parser.add_argument("--agent-cmd", default="claude -p", help='headless agent command')
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)

    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]
    if args.only:
        scenarios = [s for s in scenarios if s["id"] == args.only]
        if not scenarios:
            print(f"no scenario with id {args.only!r}", file=sys.stderr)
            return 2

    failures = 0
    for scenario in scenarios:
        try:
            verdict = run_scenario(scenario, args.agent_cmd, args.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            verdict = Verdict(False, f"runner error: {exc}")
        mark = "PASS" if verdict.passed else "FAIL"
        print(f"[{mark}] {scenario['id']}: {verdict.reason}")
        failures += 0 if verdict.passed else 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
