"""Why the 2026-07-13 MiniBench numerics lost points: a TAIL diagnostic, not a width one.

STATUS: EXPLORATORY. Written 2026-07-26, AFTER the wave resolved. Nothing here is a
preregistered test; the transforms below are motivated by the failure mechanism this
script measures, and the honest test of any of them is the NEXT wave (see the
pre-registration block at the bottom of docs/minibench-analysis-2026-07-16.md).

The preregistered counterfactual (``minibench_counterfactuals.py``) asked "are our
numeric distributions too NARROW?" and answered no: 50% central-interval coverage was
11/21 = 52% (target 50%), and uniformly widening every percentile made mean pinball loss
WORSE. That answer is correct for the question it asked and misleading for the
tournament, for two reasons:

1. Mean pinball over raw units is scale-dominated — one question priced in units of
   10^5 (TAC TVL) swamps twenty priced in units of 1-100, so the preregistered primary
   comparison is effectively a one-question test with a meaningless CI.
2. MiniBench does not score pinball. It scores the LOG DENSITY the submitted CDF puts
   at the outcome. Interior width is nearly irrelevant to that; what matters is how much
   mass sits where the outcome actually landed. A forecast can have textbook 50%
   coverage and still be destroyed by the 20% of outcomes that land outside 10-90.

So this script scores the thing the tournament scores. For each resolved numeric it
maps the outcome onto the question's internal [0,1] location scale (linear or
log/zero_point, exactly as ``percentiles_to_cdf`` does), reads the submitted CDF's
density there, and reports it against the uniform reference — i.e. Metaculus's own
baseline-style log score, in which a peer score moves one-for-one.

Counterfactual CDFs, all rebuilt through the production ``percentiles_to_cdf`` so the
platform's own standardization/tail rules apply:
  - global widen w   : q' = med + w*(q - med) for all five percentiles (preregistered)
  - tail-only widen t : same, but ONLY p10/p90 move — the calibrated 25-75 core is left
                        alone, which is what the coverage evidence says to do
  - uniform mixture e : cdf' = (1-e)*cdf + e*location — a hard floor under tail density,
                        applied to the SUBMITTED CDF (no percentile round-trip)

Usage:
    python bench/analysis/minibench_numeric_tails.py --resolutions FILE.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from forecast_scaffold.core import _scale_location, percentiles_to_cdf  # noqa: E402

sys.path.insert(0, str(ROOT))
from bench.analysis.minibench_counterfactuals import (  # noqa: E402
    JOURNAL,
    QUANTILES,
    load_wave,
)

GLOBAL_WIDENS = (1.3, 1.6, 2.0)
TAIL_WIDENS = (1.5, 2.0, 3.0)
MIXTURES = (0.02, 0.05, 0.10)
RIGHT_WIDENS = (1.5, 2.0, 3.0)   # only p90 moves out — the asymmetry the PIT points at
SHIFTS = (0.10, 0.20, 0.30)      # add d*(p90-p10) to every percentile


def clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def location_of(value: float, scaling: dict) -> float:
    return clamp01(_scale_location(
        value,
        float(scaling["range_min"]),
        float(scaling["range_max"]),
        scaling.get("zero_point"),
    ))


def cdf_at(cdf: list[float], loc: float) -> float:
    """Linearly interpolated CDF value at a [0,1] location."""
    n = len(cdf)
    x = loc * (n - 1)
    i = min(int(x), n - 2)
    return cdf[i] + (cdf[i + 1] - cdf[i]) * (x - i)


def log_density_score(cdf: list[float], loc: float) -> float:
    """100 * log2(density / uniform) at ``loc``, densities taken in location space.

    This is Metaculus's continuous baseline score up to the constant it adds; peer score
    is own-log-score minus the field's, so DIFFERENCES here transfer one-for-one.
    """
    n = len(cdf)
    i = min(int(loc * (n - 1)), n - 2)
    density = (cdf[i + 1] - cdf[i]) * (n - 1)  # mass per unit location
    return 100.0 * math.log2(max(density, 1e-12))


def widen(pcts: dict[str, float], w: float, *, tails_only: bool = False) -> dict[str, float]:
    med = pcts["50"]
    moved = ("10", "90") if tails_only else tuple(str(q) for q in QUANTILES)
    return {k: (med + w * (v - med) if k in moved else v) for k, v in pcts.items()}


def right_widen(pcts: dict[str, float], r: float) -> dict[str, float]:
    """Stretch ONLY the upper tail: p90 moves out from the median, p10/p25/p50/p75 stay."""
    med = pcts["50"]
    return {k: (med + r * (v - med) if k == "90" else v) for k, v in pcts.items()}


def shift(pcts: dict[str, float], d: float) -> dict[str, float]:
    """Translate the whole distribution up by ``d`` of its own 10-90 width."""
    step = d * (pcts["90"] - pcts["10"])
    return {k: v + step for k, v in pcts.items()}


def rebuild(pcts: dict[str, float], scaling: dict) -> list[float] | None:
    """percentiles -> platform CDF, or None if the widened values leave the range."""
    lo, hi = float(scaling["range_min"]), float(scaling["range_max"])
    eps = (hi - lo) * 1e-4
    clipped = {k: min(max(v, lo + eps), hi - eps) for k, v in pcts.items()}
    try:
        return percentiles_to_cdf(
            clipped, lo, hi,
            lower_open=bool(scaling.get("lower_open")),
            upper_open=bool(scaling.get("upper_open")),
            zero_point=scaling.get("zero_point"),
            cdf_size=int(scaling.get("cdf_size") or 201),
        )
    except ValueError:
        return None


def mix_uniform(cdf: list[float], eps: float) -> list[float]:
    n = len(cdf)
    return [(1 - eps) * v + eps * (i / (n - 1)) for i, v in enumerate(cdf)]


def boot_ci(deltas: list[float], iters: int = 10000) -> tuple[float, float]:
    rnd = random.Random(7)
    means = sorted(st.mean(rnd.choices(deltas, k=len(deltas))) for _ in range(iters))
    return means[int(iters * 0.05)], means[int(iters * 0.95)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--resolutions", type=Path, required=True)
    args = parser.parse_args(argv)

    resolutions = {int(k): float(v) for k, v in
                   json.loads(args.resolutions.read_text(encoding="utf-8")).items()}
    _, numerics = load_wave(args.journal)
    rows = [r for r in numerics
            if r["source"]["question_id"] in resolutions and r.get("submitted_cdf")
            and r.get("scaling")]
    print(f"resolved numerics with a submitted CDF: {len(rows)}")

    print("\n=== per-question: where the outcome landed in OUR distribution ===")
    print(f"{'qid':>6}  {'PIT':>6}  {'logscore':>9}  {'p10':>10} {'p50':>10} {'p90':>10} "
          f"{'outcome':>10}  question")
    pit_values, base_scores = [], []
    for row in sorted(rows, key=lambda r: r["source"]["question_id"]):
        qid = row["source"]["question_id"]
        y = resolutions[qid]
        loc = location_of(y, row["scaling"])
        pit = cdf_at(row["submitted_cdf"], loc)
        score = log_density_score(row["submitted_cdf"], loc)
        pit_values.append(pit)
        base_scores.append(score)
        p = row["percentiles"]
        print(f"{qid:>6}  {pit:>6.3f}  {score:>9.1f}  {p['10']:>10.4g} {p['50']:>10.4g} "
              f"{p['90']:>10.4g} {y:>10.4g}  {row['question'][:44]}")

    n = len(rows)
    outside_10_90 = sum(1 for p in pit_values if p < 0.10 or p > 0.90)
    outside_25_75 = sum(1 for p in pit_values if p < 0.25 or p > 0.75)
    outside_02_98 = sum(1 for p in pit_values if p < 0.02 or p > 0.98)
    print(f"\n=== calibration of the submitted distributions (n={n}) ===")
    print(f"  outcome inside 25-75 : {n - outside_25_75}/{n} "
          f"({(n - outside_25_75) / n:.0%})   target 50%")
    print(f"  outcome inside 10-90 : {n - outside_10_90}/{n} "
          f"({(n - outside_10_90) / n:.0%})   target 80%")
    print(f"  outcome beyond 2/98  : {outside_02_98}/{n} "
          f"({outside_02_98 / n:.0%})   target 4%")
    print(f"  mean log score {st.mean(base_scores):+.1f}  "
          f"median {st.median(base_scores):+.1f}  worst {min(base_scores):+.1f}")

    print("\n=== counterfactual transforms (total log score over the same n) ===")
    base_total = sum(base_scores)
    print(f"  {'transform':<26} {'total':>10} {'vs submitted':>13} {'mean/q':>9} "
          f"{'worst':>9}  inside 10-90")

    def report(label: str, cdfs: list[list[float] | None]) -> list[float] | None:
        scores, pits = [], []
        for row, cdf in zip(rows, cdfs, strict=True):
            if cdf is None:
                return None
            loc = location_of(resolutions[row["source"]["question_id"]], row["scaling"])
            scores.append(log_density_score(cdf, loc))
            pits.append(cdf_at(cdf, loc))
        inside = sum(1 for p in pits if 0.10 <= p <= 0.90)
        print(f"  {label:<26} {sum(scores):>10.0f} {sum(scores) - base_total:>+13.0f} "
              f"{st.mean(scores):>+9.1f} {min(scores):>+9.1f}  {inside:>7}/{n}")
        return scores

    report("submitted (identity)", [r["submitted_cdf"] for r in rows])
    for w in GLOBAL_WIDENS:
        report(f"global widen w={w}",
               [rebuild(widen(r["percentiles"], w), r["scaling"]) for r in rows])
    tail_scores: dict[float, list[float]] = {}
    for t in TAIL_WIDENS:
        got = report(f"tail-only widen t={t}",
                     [rebuild(widen(r["percentiles"], t, tails_only=True), r["scaling"])
                      for r in rows])
        if got:
            tail_scores[t] = got
    mix_scores: dict[float, list[float]] = {}
    for e in MIXTURES:
        got = report(f"uniform mixture e={e}",
                     [mix_uniform(r["submitted_cdf"], e) for r in rows])
        if got:
            mix_scores[e] = got
    right_scores: dict[float, list[float]] = {}
    for r_ in RIGHT_WIDENS:
        got = report(f"right-tail only r={r_}",
                     [rebuild(right_widen(r["percentiles"], r_), r["scaling"]) for r in rows])
        if got:
            right_scores[r_] = got
    shift_scores: dict[float, list[float]] = {}
    for d in SHIFTS:
        got = report(f"shift up d={d}",
                     [rebuild(shift(r["percentiles"], d), r["scaling"]) for r in rows])
        if got:
            shift_scores[d] = got

    above = sum(1 for p in pit_values if p > 0.5)
    print(f"\n=== median bias (sign test) ===\n  outcome above our median in "
          f"{above}/{n} questions ({above / n:.0%}); a calibrated median gives 50%")

    print("\n=== paired bootstrap vs submitted (90% CI, 10k draws, seed 7) ===")
    print("  EXPLORATORY: these transforms were chosen after seeing this wave.")
    for label, scores in ([(f"tail-only t={t}", s) for t, s in tail_scores.items()]
                          + [(f"mixture e={e}", s) for e, s in mix_scores.items()]
                          + [(f"right-tail r={r_}", s) for r_, s in right_scores.items()]
                          + [(f"shift up d={d}", s) for d, s in shift_scores.items()]):
        deltas = [a - b for a, b in zip(scores, base_scores, strict=True)]
        lo, hi = boot_ci(deltas)
        print(f"  {label:<20} mean {st.mean(deltas):+8.1f} per question  "
              f"CI90 [{lo:+8.1f},{hi:+8.1f}]  (positive favors the transform)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
