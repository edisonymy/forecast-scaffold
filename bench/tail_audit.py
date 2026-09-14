"""Tail-calibration audit: do extreme forecasts resolve as often as they claim?

Issue #10 alleges blind aggregation is overconfident at the tails — but its evidence is
distance to a teacher on 4 selected cases. This audits the same claim against RESOLVED
outcomes only (the honest metric) on any run_bench results whose set carries resolutions
(pastcast sets like btf2). No model calls, no network: pure counting over files that
already exist.

For each tier and probability bucket: n, the mean forecast (the claimed hit rate), the
observed YES frequency, and an exact one-sided Poisson-binomial tail probability — how
surprising the observed count is IF the forecasts were calibrated. Detection, not
fitting: this can flag gross tail miscalibration at n~85 where fitting a recalibration
curve (issue #2) would be premature. The same table is printed for the teacher (the
set's frozen crowd/SOTA value on identical questions), so "our tails are overconfident"
can be separated from "everyone's tails are overconfident on this corpus".

Usage:
    python bench/tail_audit.py bench/sets/2026-07-05-btf2.jsonl
    (reads bench/results/<setname>*.results.jsonl; or pass --results explicitly)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "bench" / "results"

# Bucket edges chosen for the tail question, not for curve-fitting: the two outermost
# buckets on each side are where issue #10 lives; the middle is a lump on purpose.
BUCKET_EDGES = (0.0, 0.05, 0.10, 0.25, 0.75, 0.90, 0.95, 1.0)


def poisson_binomial_tail(probs: list[float], k: int) -> float:
    """Exact P(X >= k) where X = sum of independent Bernoulli(p_i) — DP, stdlib only."""
    dist = [1.0]
    for p in probs:
        nxt = [0.0] * (len(dist) + 1)
        for i, mass in enumerate(dist):
            nxt[i] += mass * (1.0 - p)
            nxt[i + 1] += mass * p
        dist = nxt
    return sum(dist[max(k, 0):])


def bucket_of(p: float) -> int:
    for i in range(len(BUCKET_EDGES) - 1):
        if p < BUCKET_EDGES[i + 1] or i == len(BUCKET_EDGES) - 2:
            return i
    return len(BUCKET_EDGES) - 2  # unreachable; keeps mypy/readers calm


def load_forecasts(results_paths: list[Path]) -> dict[tuple[str, str, int], float]:
    """Last row wins per (qid, tier, run) — matching run_bench's append/resume semantics.
    Router-only rows (probability=None) are skipped."""
    latest: dict[tuple[str, str, int], float] = {}
    for path in results_paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("probability") is None:
                continue
            key = (str(row["qid"]), str(row["tier"]), int(row.get("run") or 0))
            latest[key] = float(row["probability"])
    return latest


def audit_lines(label: str, pairs: list[tuple[float, int]]) -> list[str]:
    """pairs = [(forecast, outcome 0/1), ...] -> one markdown table for this forecaster.

    tail-p is one-sided in the OVERCONFIDENT direction for that side of the range:
    low buckets are surprised by YES resolutions, high buckets by NO resolutions. The
    middle bucket has no overconfident direction, so it gets no p-value.
    """
    lines = [f"### {label} (n={len(pairs)})", "",
             "| bucket | n | mean forecast | observed YES | tail-p (overconf.) |",
             "|---|---|---|---|---|"]
    buckets: dict[int, list[tuple[float, int]]] = {}
    for p, y in pairs:
        buckets.setdefault(bucket_of(p), []).append((p, y))
    for i in range(len(BUCKET_EDGES) - 1):
        members = buckets.get(i, [])
        lo, hi = BUCKET_EDGES[i], BUCKET_EDGES[i + 1]
        name = f"[{lo:.2f}, {hi:.2f})"
        if not members:
            lines.append(f"| {name} | 0 | — | — | — |")
            continue
        ps = [p for p, _ in members]
        ys = [y for _, y in members]
        mean_p, obs = sum(ps) / len(ps), sum(ys) / len(ys)
        mid = (lo + hi) / 2.0
        if mid < 0.25:
            tail = poisson_binomial_tail(ps, sum(ys))  # too many YES = overconfident low
            tail_s = f"{tail:.3f}"
        elif mid > 0.75:
            misses = [1.0 - p for p in ps]  # too many NO = overconfident high
            tail = poisson_binomial_tail(misses, len(ys) - sum(ys))
            tail_s = f"{tail:.3f}"
        else:
            tail_s = "—"
        lines.append(f"| {name} | {len(members)} | {mean_p:.3f} | {obs:.3f} ({sum(ys)}) "
                     f"| {tail_s} |")
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("set_file", help="question set with resolutions (btf2-style)")
    parser.add_argument("--results", nargs="*", help="results jsonl paths "
                        "(default: bench/results/<setname>*.results.jsonl)")
    args = parser.parse_args(argv)

    set_path = Path(args.set_file)
    specs = [json.loads(line) for line in set_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    outcome_of = {str(s["id"]): int(s["resolution"]) for s in specs
                  if s.get("resolution") is not None}
    if not outcome_of:
        print("set has no resolutions — the audit needs a pastcast set", file=sys.stderr)
        return 1

    results_paths = ([Path(p) for p in args.results] if args.results
                     else sorted(RESULTS_DIR.glob(f"{set_path.stem}*.results.jsonl")))
    results_paths = [p for p in results_paths if p.exists()]
    if not results_paths:
        print(f"no results files found for {set_path.stem} under {RESULTS_DIR}",
              file=sys.stderr)
        return 1

    latest = load_forecasts(results_paths)
    lines = [f"# Tail-calibration audit: {set_path.stem}", "",
             f"results: {', '.join(p.name for p in results_paths)}", "",
             "tail-p = exact Poisson-binomial P(seeing at least this many surprises | the "
             "forecasts are calibrated); small = the tail is overcommitted.", ""]

    tiers = sorted({tier for _, tier, _ in latest})
    for tier in tiers:
        pairs = [(p, outcome_of[qid]) for (qid, t, _run), p in sorted(latest.items())
                 if t == tier and qid in outcome_of]
        if pairs:
            lines += audit_lines(f"tier {tier}", pairs)

    teacher_pairs = [(float(s["crowd"]["value"]), outcome_of[str(s["id"])]) for s in specs
                     if str(s["id"]) in outcome_of
                     and s.get("crowd") and s["crowd"].get("value") is not None]
    if teacher_pairs:
        lines += audit_lines("teacher (set's frozen crowd/SOTA value)", teacher_pairs)

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
