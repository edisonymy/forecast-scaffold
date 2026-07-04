"""Run the forecast skill's effort tiers, paired, over a frozen benchmark set.

Every requested tier forecasts every question in the set (binary only), always BLIND —
the crowd value in the set file is the measurement target and never enters the prompt,
and aggregator domains are tool-blocked. Results append to
``bench/results/<setname>.results.jsonl``; finished (question, tier) pairs are skipped,
so an interrupted run resumes where it stopped.

Usage:
    python bench/run_bench.py bench/sets/2026-07-04.jsonl \
        --tiers low,medium,high,auto --provider openrouter \
        --agent-cmd "claude -p --model claude-sonnet-5 --output-format json \
                     --allowed-tools Read,Glob,Grep,WebSearch,WebFetch"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

# ruff: noqa: E402  (imports follow the sys.path bootstrap above)
from run_bot import (
    BLIND_DISALLOWED,
    PROVIDERS,
    build_system,
    extract_json,
    openrouter_model_cmd,
    run_agent,
    triage,
)

from forecast_scaffold.core import SCAFFOLD_VERSION

RESULTS_DIR = ROOT / "bench" / "results"
# The set includes RAND/INFER questions; block that aggregator too.
BENCH_DISALLOWED = BLIND_DISALLOWED + (
    ",WebFetch(domain:randforecastinginitiative.org)"
    ",WebFetch(domain:www.randforecastinginitiative.org)"
)
TIERS = ("low", "medium", "high", "auto")


def build_bench_brief(spec: dict) -> str:
    """The agent-facing brief: question text only — no market URL, no crowd value."""
    return "\n".join([
        f"# Question: {spec['question']}",
        "Type: binary",
        f"Closes: {spec.get('resolve_by') or 'unknown'}",
        "\n## Resolution criteria (verbatim — the contract)",
        spec.get("criteria", ""),
        "\n## Background",
        spec.get("background", ""),
    ])


def forecast_one(spec: dict, tier: str, args: argparse.Namespace) -> dict | None:
    brief = build_bench_brief(spec)
    base_cmd = (
        openrouter_model_cmd(args.agent_cmd)
        if args.provider == "openrouter" else args.agent_cmd
    )
    cost = 0.0
    if tier == "auto":
        resolved, triage_cost = triage(base_cmd, brief, args.timeout, args.provider)
        cost += triage_cost
        effort = f"{resolved} (auto)"
    else:
        resolved, effort = tier, tier
    system = build_system(resolved, blind=True)
    agent_cmd = f"{base_cmd} --disallowed-tools {BENCH_DISALLOWED}"

    probability: float | None = None
    model = ""
    errors: list[str] = []
    for attempt in range(2):
        prompt = brief if attempt == 0 else (
            brief + "\n\nYour previous output was invalid: "
            + "; ".join(errors) + "\nEmit a corrected fenced json block."
        )
        try:
            output, attempt_cost, model = run_agent(
                agent_cmd, prompt, system, args.timeout, args.provider
            )
            cost += attempt_cost
            candidate = extract_json(output).get("probability")
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            errors = [str(exc)[:300]]
            continue
        if isinstance(candidate, int | float) and 0 < float(candidate) < 1:
            probability = float(candidate)
            break
        errors = [f"binary needs a probability in (0,1), got {candidate!r}"]
    if probability is None:
        print(f"    FAILED after retry: {errors}")
        return None
    return {
        "qid": spec["id"],
        "source": spec["source"],
        "question": spec["question"][:200],
        "tier": tier,
        "effort": effort,
        "probability": probability,
        "crowd": spec.get("crowd"),
        "cost_usd": round(cost, 4),
        "model": model,
        "provider": args.provider,
        "scaffold_version": SCAFFOLD_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("set_file", help="question set from bench/fetch_set.py")
    parser.add_argument("--tiers", default="low,medium,high,auto")
    parser.add_argument("--limit", type=int, default=0, help="max questions (0 = all)")
    parser.add_argument("--provider", default="subscription", choices=PROVIDERS)
    parser.add_argument("--agent-cmd", default="claude -p", help="headless agent command")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args(argv)

    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    unknown = [t for t in tiers if t not in TIERS]
    if unknown:
        parser.error(f"unknown tiers {unknown}; choose from {TIERS}")

    set_path = Path(args.set_file)
    specs = [json.loads(line) for line in set_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if args.limit:
        specs = specs[: args.limit]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / f"{set_path.stem}.results.jsonl"
    done: set[tuple[str, str]] = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((row["qid"], row["tier"]))

    total = len(specs) * len(tiers)
    print(f"{len(specs)} questions x {len(tiers)} tiers = {total} runs "
          f"({len(done)} already done) -> {results_path}")
    spent = 0.0
    failures = 0
    with results_path.open("a", encoding="utf-8") as fh:
        for i, spec in enumerate(specs, 1):
            for tier in tiers:
                if (spec["id"], tier) in done:
                    continue
                title = spec["question"][:70].encode("ascii", "replace").decode()
                print(f"[{i}/{len(specs)}] {tier:<7} {title}")
                row = forecast_one(spec, tier, args)
                if row is None:
                    failures += 1
                    continue
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                spent += row["cost_usd"]
                print(f"    p={row['probability']:.2f} crowd={spec['crowd']['value']:.2f} "
                      f"${row['cost_usd']:.2f} (run total ${spent:.2f})")
    print(f"done; {failures} failure(s), ${spent:.2f} spent this invocation")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
