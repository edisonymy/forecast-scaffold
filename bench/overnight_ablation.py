"""Overnight driver: the model x harness ablation (preregistered as issue #4).

Builds a fresh v2 set from the newest ForecastBench drop, then runs the cells in order,
sleeping and resuming whenever the subscription usage window caps out (run_bench exits
nonzero on failures and skips finished (question, tier, run) jobs on retry).

Cells (all blind, all paired on the same set; SUBSCRIPTION ONLY — never a metered key):
  A  iter1        sonnet-5 + skill   low(1) + high(runs from config) + auto(router)
  B  sonnet-zero  sonnet-5, no skill (zero tier, 1 run)
  C  fable-high1  fable-5 + skill    high, single run (--max-runs 1)
  D  fable-zero   fable-5, no skill  (zero tier, 1 run)
Cell A's high run #0 doubles as "sonnet + skill, single run" for the 2x2 ablation.

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
        ("A iter1 (sonnet + skill, preregistered #3)",
         bench + ["--tiers", "low,high,auto", "--agent-cmd", SONNET, "--tag", "sonnet"]),
        ("B sonnet zero-shot",
         bench + ["--tiers", "zero", "--agent-cmd", SONNET, "--tag", "sonnet"]),
        ("C fable + skill high, single run",
         bench + ["--tiers", "high", "--max-runs", "1", "--timeout", "1800",
                  "--agent-cmd", FABLE, "--tag", "fable"]),
        ("D fable zero-shot",
         bench + ["--tiers", "zero", "--timeout", "1800",
                  "--agent-cmd", FABLE, "--tag", "fable"]),
    ]
    results = {label: run_until_done(cmd, label) for label, cmd in cells}

    for tag in ("sonnet", "fable"):
        run([PY, "bench/report.py", set_file, "--tag", tag])
    log("ablation driver finished: " + ", ".join(
        f"{label.split(' ')[0]}={'ok' if ok else 'INCOMPLETE'}" for label, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
