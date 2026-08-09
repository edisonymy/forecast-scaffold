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

So this script scores the thing the tournament scores. For each resolved numeric it maps
the outcome onto the question's internal [0,1] location scale (linear or log/zero_point,
exactly as ``percentiles_to_cdf`` does), finds the resolution BUCKET, and scores the mass
in it against the platform's baseline — Metaculus's own formula, in leaderboard points.

CORRECTED 2026-07-26 against Metaculus/metaculus source (scoring/score_math.py,
utils/the_math/formulas.py). The first version of this file got three things wrong:
  - it used 100*log2 where the platform uses 50*ln, overstating every figure by 2.885x;
  - it picked the bucket LEFT-closed where the platform is RIGHT-closed, which matters
    precisely at declared percentiles (35 of this wave's 105 sit exactly on a bucket
    edge) and was biased pessimistic there;
  - it used a uniform reference of 1.0 instead of (1 - 0.05*open_bounds).
MiniBench pays a PEER score (this log mass minus the field's average). The field term is
independent of our forecast, so it cancels in any paired comparison of two of our own
forecasts on the same question: deltas printed here are deltas in leaderboard points.
Absolute levels are baseline scores and are NOT what the leaderboard shows.

Counterfactual CDFs, all rebuilt through the production ``percentiles_to_cdf`` so the
platform's own standardization/tail rules apply:
  - global widen w   : q' = med + w*(q - med) for all five percentiles (preregistered)
  - tail-only widen t : same, but ONLY p10/p90 move — the calibrated 25-75 core is left
                        alone, which is what the coverage evidence says to do
  - uniform mixture e : cdf' = (1-e)*cdf + e*location — a hard floor under tail density,
                        applied to the SUBMITTED CDF (no percentile round-trip)

POOLING ACROSS WAVES (added 2026-08-09): --resolutions and --window are both repeatable
and mean exactly what they mean in ``minibench_counterfactuals.py`` (a wave is a resolve_by
window; with no --window the two known waves are used). The per-question table, the
coverage block, the transform table and the median-bias sign test are printed PER WAVE and
then POOLED; the paired bootstrap is pooled only, since that is the powered look.

Usage:
    python bench/analysis/minibench_numeric_tails.py --resolutions FILE.json
    python bench/analysis/minibench_numeric_tails.py \
        --resolutions bench/analysis/minibench-2026-07-resolutions.json \
        --resolutions bench/analysis/minibench-2026-07-27-resolutions.json
    # one wave only:
    python bench/analysis/minibench_numeric_tails.py \
        --window 2026-08-06 2026-08-09 --resolutions FILE.json
"""

from __future__ import annotations

import argparse
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
    DEFAULT_WINDOWS,
    JOURNAL,
    QUANTILES,
    WAVE_KEY,
    load_resolutions,
    load_wave,
    parse_windows,
    wave_labels,
)

GLOBAL_WIDENS = (1.3, 1.6, 2.0)
TAIL_WIDENS = (1.5, 2.0, 3.0)
MIXTURES = (0.02, 0.05, 0.10)
RIGHT_WIDENS = (1.5, 2.0, 3.0)   # only p90 moves out — the asymmetry the PIT points at
SHIFTS = (0.10, 0.20, 0.30)      # add d*(p90-p10) to every percentile


def clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def raw_location(value: float, scaling: dict) -> float:
    """Internal [0,1] location, UNclamped — <0 or >1 means the outcome fell out of bounds."""
    return _scale_location(
        value,
        float(scaling["range_min"]),
        float(scaling["range_max"]),
        scaling.get("zero_point"),
    )


def location_of(value: float, scaling: dict) -> float:
    return clamp01(raw_location(value, scaling))


def cdf_at(cdf: list[float], loc: float) -> float:
    """Linearly interpolated CDF value at a [0,1] location."""
    n = len(cdf)
    x = loc * (n - 1)
    i = min(int(x), n - 2)
    return cdf[i] + (cdf[i + 1] - cdf[i]) * (x - i)


def bucket_index(u: float, n_buckets: int) -> int:
    """Metaculus's resolution bucket for internal location ``u`` (Metaculus/metaculus,
    utils/the_math/formulas.py). RIGHT-CLOSED: an outcome exactly on a bucket edge scores
    in the bucket BELOW. Index 0 is mass under the lower bound, n_buckets+1 over the upper.
    """
    if u < 0:
        return 0
    if u > 1:
        return n_buckets + 1
    if u == 1:
        return n_buckets
    return max(int(u * n_buckets + 1 - 1e-10), 1)


def platform_pmf(cdf: list[float]) -> list[float]:
    """The scored mass array: [below-bound, ...per-bucket..., above-bound], length N+2."""
    return [cdf[0], *[cdf[i] - cdf[i - 1] for i in range(1, len(cdf))], 1.0 - cdf[-1]]


def platform_score(cdf: list[float], u: float, open_bounds: int) -> float:
    """Metaculus's continuous BASELINE score, in leaderboard points (scoring/score_math.py):

        baseline = 0.05                        if the outcome fell out of bounds
                 = (1 - 0.05*open_bounds) / N  otherwise
        score    = 50 * ln(mass_in_outcome_bucket / baseline)

    Note the units: 50*ln, NOT the 100*log2 this file used before 2026-07-26 (which
    overstated every figure by 2.885x). MiniBench pays a PEER score — this same log mass
    minus the field's average. The field term does not depend on our forecast, so for a
    PAIRED comparison of two of our own forecasts on the same question it cancels exactly:
    a delta computed here is a delta in leaderboard points.
    """
    n_buckets = len(cdf) - 1
    pmf = platform_pmf(cdf)
    k = bucket_index(u, n_buckets)
    baseline = 0.05 if k in (0, len(pmf) - 1) else (1.0 - 0.05 * open_bounds) / n_buckets
    return 50.0 * math.log(max(pmf[k], 1e-12) / baseline)


def open_bounds_of(scaling: dict) -> int:
    return int(bool(scaling.get("lower_open"))) + int(bool(scaling.get("upper_open")))


def score_row(cdf: list[float], outcome: float, scaling: dict) -> float:
    return platform_score(cdf, raw_location(outcome, scaling), open_bounds_of(scaling))


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


def cdf_without_open_bound_halving(pcts: dict[str, float], scaling: dict) -> list[float] | None:
    """``percentiles_to_cdf`` with ONE change: an open bound no longer assumes that half of
    the outermost declared decile lies outside the question's range.

    core.py:1046-1048 sets the bound anchor to ``0.5*min_frac`` / ``1 - 0.5*(1-max_frac)``,
    so a declared p90 under an open upper bound is placed as if it were p95 and the
    standardization then rescales the interior. Measured across this wave, our declared p90
    actually lands at a mean CDF of 0.930: every distribution we submit is SHARPER than the
    one we elicited. This variant pins the declared percentiles where they were declared and
    lets the platform's own min-step/tail terms supply the out-of-bound mass. Requires no new
    elicitation — it is a pure harness change.
    """
    lo, hi = float(scaling["range_min"]), float(scaling["range_max"])
    zero_point, cdf_size = scaling.get("zero_point"), int(scaling.get("cdf_size") or 201)
    lower_open, upper_open = bool(scaling.get("lower_open")), bool(scaling.get("upper_open"))
    declared = sorted((float(k) / 100.0, float(v)) for k, v in pcts.items())
    if not all(lo < v < hi for _, v in declared):
        return None
    points = [(clamp01(_scale_location(v, lo, hi, zero_point)), f) for f, v in declared]
    anchors = [(0.0, 0.0), *points, (1.0, 1.0)]   # <-- the only change: no halving
    if any(a[0] >= b[0] for a, b in zip(anchors, anchors[1:], strict=False)):
        return None
    locations = [i / (cdf_size - 1) for i in range(cdf_size)]
    raw, seg = [], 0
    for x in locations:
        while seg < len(anchors) - 2 and anchors[seg + 1][0] < x:
            seg += 1
        (x0, y0), (x1, y1) = anchors[seg], anchors[seg + 1]
        raw.append(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    span = raw[-1] - raw[0]
    rescaled = [(y - raw[0]) / span for y in raw]
    if lower_open and upper_open:
        cdf = [0.988 * r + 0.01 * x + 0.001 for r, x in zip(rescaled, locations, strict=True)]
    elif lower_open:
        cdf = [0.989 * r + 0.01 * x + 0.001 for r, x in zip(rescaled, locations, strict=True)]
    elif upper_open:
        cdf = [0.989 * r + 0.01 * x for r, x in zip(rescaled, locations, strict=True)]
    else:
        cdf = [0.99 * r + 0.01 * x for r, x in zip(rescaled, locations, strict=True)]
    return [round(v, 10) for v in cdf]


def mix_uniform(cdf: list[float], eps: float) -> list[float]:
    n = len(cdf)
    return [(1 - eps) * v + eps * (i / (n - 1)) for i, v in enumerate(cdf)]


def boot_ci(deltas: list[float], iters: int = 10000) -> tuple[float, float]:
    rnd = random.Random(7)
    means = sorted(st.mean(rnd.choices(deltas, k=len(deltas))) for _ in range(iters))
    return means[int(iters * 0.05)], means[int(iters * 0.95)]


def analyze(rows: list[dict], resolutions: dict[int, float], label: str,
            *, bootstrap: bool) -> None:
    """Full readout for one set of rows (one wave, or all waves pooled)."""
    print(f"\n{'=' * 78}\n=== {label} ===")
    print(f"resolved numerics with a submitted CDF: {len(rows)}")
    if not rows:
        return

    print("\n=== per-question: where the outcome landed in OUR distribution ===")
    print(f"{'qid':>6}  {'PIT':>6}  {'logscore':>9}  {'p10':>10} {'p50':>10} {'p90':>10} "
          f"{'outcome':>10}  question")
    pit_values, base_scores = [], []
    for row in sorted(rows, key=lambda r: r["source"]["question_id"]):
        qid = row["source"]["question_id"]
        y = resolutions[qid]
        loc = location_of(y, row["scaling"])
        pit = cdf_at(row["submitted_cdf"], loc)
        score = score_row(row["submitted_cdf"], y, row["scaling"])
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

    def report(name: str, cdfs: list[list[float] | None]) -> list[float] | None:
        scores, pits = [], []
        for row, cdf in zip(rows, cdfs, strict=True):
            if cdf is None:
                return None
            loc = location_of(resolutions[row["source"]["question_id"]], row["scaling"])
            scores.append(score_row(cdf, resolutions[row["source"]["question_id"]],
                                    row["scaling"]))
            pits.append(cdf_at(cdf, loc))
        inside = sum(1 for p in pits if 0.10 <= p <= 0.90)
        print(f"  {name:<26} {sum(scores):>10.0f} {sum(scores) - base_total:>+13.0f} "
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
    nohalve = report("no open-bound halving",
                     [cdf_without_open_bound_halving(r["percentiles"], r["scaling"])
                      for r in rows])
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

    if not bootstrap:
        return
    print("\n=== paired bootstrap vs submitted (90% CI, 10k draws, seed 7) ===")
    print("  EXPLORATORY: these transforms were chosen after seeing the 2026-07 wave.")
    for name, scores in ([(f"tail-only t={t}", s) for t, s in tail_scores.items()]
                         + [(f"mixture e={e}", s) for e, s in mix_scores.items()]
                         + [(f"right-tail r={r_}", s) for r_, s in right_scores.items()]
                         + [(f"shift up d={d}", s) for d, s in shift_scores.items()]
                         + ([("no-halving", nohalve)] if nohalve else [])):
        deltas = [a - b for a, b in zip(scores, base_scores, strict=True)]
        lo, hi = boot_ci(deltas)
        print(f"  {name:<20} mean {st.mean(deltas):+8.1f} per question  "
              f"CI90 [{lo:+8.1f},{hi:+8.1f}]  (positive favors the transform)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--resolutions", type=Path, action="append", required=True,
                        help="JSON {qid: outcome}; repeatable, later files win on collision")
    parser.add_argument("--window", nargs=2, metavar=("START", "END"), action="append",
                        default=None,
                        help="resolve_by window identifying one wave; repeatable. "
                             f"Default: {' and '.join(f'{a}..{b}' for a, b in DEFAULT_WINDOWS)}")
    args = parser.parse_args(argv)

    windows = parse_windows(args.window)
    resolutions = load_resolutions(args.resolutions)
    _, numerics = load_wave(args.journal, windows)
    rows = [r for r in numerics
            if r["source"]["question_id"] in resolutions and r.get("submitted_cdf")
            and r.get("scaling")]

    labels = wave_labels(windows)
    for i, label in enumerate(labels):
        analyze([r for r in rows if r[WAVE_KEY] == i], resolutions, label,
                bootstrap=len(windows) == 1)
    if len(windows) > 1:
        analyze(rows, resolutions, f"POOLED (all {len(windows)} waves)", bootstrap=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
