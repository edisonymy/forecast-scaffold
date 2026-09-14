"""Overnight driver: the model x harness ablation (preregistered as issue #4).

Builds a fresh v2 set from the newest ForecastBench drop, then runs the cells in order,
sleeping and resuming whenever the subscription usage window caps out (run_bench exits
nonzero on failures and skips finished (question, tier, run) jobs on retry).

Cells (all blind, all paired on the same set; SUBSCRIPTION ONLY — never a metered key).
Budget-lean: every tier is capped at a single run (multi-run pooling can be added later —
resume keys on (question, tier, run), so extra runs are pure top-up, never re-spend).
The 2x2 core runs first so a weekly-usage cap costs the cheap tail, not the fable cells:
  A1 sonnet-skill  sonnet-5 + skill   high, single run
  B  sonnet-zero   sonnet-5, no skill (zero tier, 1 run)
  C  fable-skill   fable-5 + skill    high, single run
  D  fable-zero    fable-5, no skill  (zero tier, 1 run)
  A2 sonnet tail   sonnet-5 + skill   low (1 run) + auto (router-only) — iter1 dev extras

Usage:
    python bench/overnight_ablation.py            # full overnight run
    python bench/overnight_ablation.py --n 5      # tiny smoke
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
ALLOWED = "Read,Glob,Grep,WebSearch,WebFetch"
SONNET = f"claude -p --model claude-sonnet-5 --output-format json --allowed-tools {ALLOWED}"
FABLE = f"claude -p --model claude-fable-5 --output-format json --allowed-tools {ALLOWED}"
RETRY_SLEEP_S = 1800  # capped window: wait 30 min and resume
MAX_ATTEMPTS_PER_CELL = 14  # ~7h of pure waiting at worst before giving up on a cell


def log(msg: str) -> None:
    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def run(cmd: list[str]) -> int:
    log("RUN " + " ".join(cmd[:8]) + (" ..." if len(cmd) > 8 else ""))
    return subprocess.run(cmd, cwd=ROOT).returncode


def run_until_done(cmd: list[str], label: str) -> bool:
    for attempt in range(1, MAX_ATTEMPTS_PER_CELL + 1):
        code = run(cmd)
        if code == 0:
            log(f"{label}: DONE (attempt {attempt})")
            return True
        log(f"{label}: exit {code} (attempt {attempt}) — sleeping {RETRY_SLEEP_S}s, will resume")
        time.sleep(RETRY_SLEEP_S)
    log(f"{label}: GAVE UP after {MAX_ATTEMPTS_PER_CELL} attempts")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--set-file", default="", help="reuse an existing set instead of building")
    args = parser.parse_args(argv)

    if args.set_file:
        set_file = args.set_file
    else:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        set_file = f"bench/sets/{stamp}-ablation.jsonl"
        if not (ROOT / set_file).exists():
            code = run([PY, "bench/fetch_set.py", "--n", str(args.n),
                        "--refresh-crowd", "--out", set_file])
            if code != 0:
                log("set build FAILED — aborting (nothing was spent)")
                return 1
    log(f"set: {set_file}")

    bench = [PY, "bench/run_bench.py", set_file, "--provider", "subscription",
             "--concurrency", "6"]
    cells = [
        ("A1 sonnet + skill high, single run",
         bench + ["--tiers", "high", "--max-runs", "1", "--agent-cmd", SONNET,
                  "--tag", "sonnet"]),
        ("B sonnet zero-shot",
         bench + ["--tiers", "zero", "--agent-cmd", SONNET, "--tag", "sonnet"]),
        ("C fable + skill high, single run",
         bench + ["--tiers", "high", "--max-runs", "1", "--timeout", "1800",
                  "--agent-cmd", FABLE, "--tag", "fable"]),
        ("D fable zero-shot",
         bench + ["--tiers", "zero", "--timeout", "1800",
                  "--agent-cmd", FABLE, "--tag", "fable"]),
        ("A2 sonnet + skill low + auto router (iter1 tail)",
         bench + ["--tiers", "low,auto", "--max-runs", "1", "--agent-cmd", SONNET,
                  "--tag", "sonnet"]),
    ]
    results = {label: run_until_done(cmd, label) for label, cmd in cells}

    for tag in ("sonnet", "fable"):
        run([PY, "bench/report.py", set_file, "--tag", tag])
    log("ablation driver finished: " + ", ".join(
        f"{label.split(' ')[0]}={'ok' if ok else 'INCOMPLETE'}" for label, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
