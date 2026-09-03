"""Paired live test: independent RESEARCH runs vs research + reasoning LENSES (binaries).

PREREGISTERED 2026-09-03. Production pools 1 full research run + (n-1) reasoning-only runs
that re-reason over its dossier under rotating lenses. Every reasoning-side lever we have
measured was null and research agency is the measured mechanism (FutureSearch's ablation,
our tranche1: refinement lives in discovery), so the candidate spends the same budget on
INDEPENDENT research instead: angle mode with two plain replicates, `run_angles=["P","P"]`
(references/research-angles.md, Angle P), pooled by geo-mean odds exactly as production
pools. Prior evidence is mixed — the tranche found three research angles (F/D/A) no better
than the high tier at 3x cost — so this is a real test, not a sure thing.

Design (equal cost, paired, prospective, no submission from the candidate):
  - Production keeps forecasting the wave as it does today (its rows are the control arm).
  - The candidate arm runs `bot/run_bot.py --dry-run --include-forecasted` over the same
    open MiniBench binaries with a config whose medium/high tiers set run_angles=["P","P"],
    writing to its OWN journal file (never bot/journal/forecasts.jsonl), within an hour
    of the production forecast so both see the same world.
  - At resolution, score both arms with the log score ln p(outcome): the spot peer score
    is 50*(ln p - field mean ln p) and the field term cancels in a paired difference, so
    the paired log-score delta IS the paired spot-peer delta up to the factor 50.

DECISION RULE (fixed now): pool two MiniBench waves (target n >= 40 paired binaries).
PROMOTE the candidate (replace lenses with plain replicates in config) if the paired
delta (candidate - production) has a 90% bootstrap CI excluding zero on the positive
side. KILL if the mean delta is negative. Otherwise extend one wave, then stop either way.
Cost is reported per arm from the journals' cost_usd; a cost ratio outside 0.8-1.25 voids
the equal-cost claim and is reported, not hidden.

Usage:
    python bench/analysis/research_vs_lenses.py --candidate bench/sets/rvl-wave5.jsonl \
        [--production bot/journal/forecasts.jsonl] [--overlay bot/journal/resolutions.jsonl]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.analysis.minibench_numeric_tails import boot_ci  # noqa: E402

PRODUCTION = ROOT / "bot" / "journal" / "forecasts.jsonl"
OVERLAY = ROOT / "bot" / "journal" / "resolutions.jsonl"


def latest_binary_rows(path: Path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        src = row.get("source") or {}
        qid = src.get("question_id") if isinstance(src, dict) else None
        if qid is None or row.get("question_type") != "binary":
            continue
        if not isinstance(row.get("probability"), (int, float)):
            continue
        prev = out.get(int(qid))
        if prev is None or str(row.get("forecast_at")) > str(prev.get("forecast_at")):
            out[int(qid)] = row
    return out


def outcomes(path: Path) -> dict[int, bool]:
    out: dict[int, bool] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "resolved" and isinstance(row.get("outcome"), bool):
            out[int(row["question_id"])] = bool(row["outcome"])
    return out


def log_score(p: float, y: bool) -> float:
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return math.log(p if y else 1.0 - p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, help="candidate arm journal (dry-run)")
    ap.add_argument("--production", default=str(PRODUCTION))
    ap.add_argument("--overlay", default=str(OVERLAY))
    args = ap.parse_args()
    cand = latest_binary_rows(Path(args.candidate))
    prod = latest_binary_rows(Path(args.production))
    res = outcomes(Path(args.overlay))
    paired = [(q, cand[q], prod[q], res[q]) for q in cand if q in prod and q in res]
    print(f"candidate binaries: {len(cand)}  paired with production and resolved: {len(paired)}")
    if not paired:
        return 1
    deltas = [log_score(c["probability"], y) - log_score(p["probability"], y)
              for _, c, p, y in paired]
    lo, hi = boot_ci(deltas)
    cost_c = st.mean(float(c.get("cost_usd") or 0) for _, c, _, _ in paired)
    cost_p = st.mean(float(p.get("cost_usd") or 0) for _, _, p, _ in paired)
    print(f"paired log-score delta (candidate - production): mean {st.mean(deltas):+.4f} "
          f"CI90 [{lo:+.4f}, {hi:+.4f}]  (x50 = spot-peer points: {50 * st.mean(deltas):+.1f}/q)")
    print(f"helps on {sum(1 for d in deltas if d > 0)}/{len(deltas)}; "
          f"cost/q candidate ${cost_c:.2f} vs production ${cost_p:.2f} "
          f"(ratio {cost_c / cost_p if cost_p else float('nan'):.2f})")
    brier_c = st.mean((float(c["probability"]) - y) ** 2 for _, c, _, y in paired)
    brier_p = st.mean((float(p["probability"]) - y) ** 2 for _, _, p, y in paired)
    print(f"Brier candidate {brier_c:.4f} vs production {brier_p:.4f}")
    verdict = "PROMOTE" if lo > 0 else ("KILL" if st.mean(deltas) < 0 else "EXTEND")
    print("VERDICT (per the header rule, once n >= 40):", verdict, f"(n={len(paired)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
