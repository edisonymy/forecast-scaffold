"""Does a SMOOTH CDF construction beat the piecewise-linear one on MiniBench numerics?

STATUS: EXPLORATORY (written 2026-08-23, after three waves resolved). Motivated by the
operator's observation that our submitted PDFs render as staircases: ``percentiles_to_cdf``
interpolates the CDF piecewise-LINEARLY through the five declared percentiles, so the PDF
is piecewise-constant — flat slab between p25-p75, flat thin slabs from p10/p90 all the
way to the bounds. The Metaculus community aggregate is a smooth mixture, visibly peaked
at its median with decaying tails.

Why shape could matter for score, not just looks: MiniBench pays log density at the
outcome. Relative to a smooth unimodal belief through the same quantiles, the staircase
(a) under-weights the region near the median, and (b) spreads each outer decile uniformly
to the bound, so a "near miss" just past p90 gets much less density than a decaying tail
would give it, while a far miss gets more. Empirically our misses cluster in PIT
0.90-0.96 (near misses), which is the regime where smooth tails pay.

Constructions compared, all built from the SAME journal rows (declared percentiles plus
the v0.4.23 declared escape masses, which ``minibench_numeric_tails.rebuild`` drops):
  - interp = linear | pchip : piecewise-linear vs monotone-cubic (Fritsch-Carlson PCHIP)
    interpolation of the cumulative fraction through the identical anchor set. PCHIP is
    C1 => continuous PDF, peaked interior, tails that decay toward the bound anchors.
  - halve | nohalve : keep or drop the inherited open-bound assumption that half the
    outermost declared decile lies outside the range (the "no open-bound halving"
    transform of minibench_numeric_tails.py, here composable with pchip).
  - w = 1.0 | 1.15 | 1.3 : global widen of the declared percentiles about the median
    before construction (w=1.3 was +244 pooled over 3 waves in the linear readout).
  - kernel c : Gaussian-kernel smoothing of the interior shape PMF, sigma =
    c * (loc(p75) - loc(p25)), reflected at the range edges. Applied to the linear shape
    it is "smooth the staircase in place" (quantiles move slightly); PCHIP needs none.

Scored with the platform's own continuous baseline formula via minibench_numeric_tails
(50*ln(mass/baseline), right-closed buckets); deltas vs submitted are leaderboard-point
deltas (the peer field term cancels in paired comparisons).

Sanity row: rebuilt-linear must reproduce the submitted CDF up to journal rounding —
its mean |delta| is printed and should be ~0. Rows whose declared escape mass the
production code honored are rebuilt with the same masses here.

Usage (the three known waves):
    python bench/analysis/minibench_smooth_cdf.py \
        --resolutions bench/analysis/minibench-2026-07-resolutions.json \
        --resolutions bench/analysis/minibench-2026-07-27-resolutions.json \
        --resolutions bench/analysis/minibench-2026-08-10-resolutions.json \
        --window 2026-07-17 2026-08-05 --window 2026-08-06 2026-08-09 \
        --window 2026-08-19 2026-08-25
"""

from __future__ import annotations

import argparse
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from forecast_scaffold.core import (  # noqa: E402
    CDF_UNIFORM_MIX,
    MIN_OPEN_TAIL,
    _cap_pmf,
    _scale_location,
)
from bench.analysis.minibench_counterfactuals import (  # noqa: E402
    JOURNAL,
    WAVE_KEY,
    load_resolutions,
    load_wave,
    parse_windows,
    wave_names,
)
from bench.analysis.minibench_numeric_tails import (  # noqa: E402
    boot_ci,
    cdf_at,
    clamp01,
    location_of,
    score_row,
    widen,
)

MAX_PMF_VALUE = 0.2


# ------------------------------------------------------------------ PCHIP (pure stdlib)

def _pchip_slopes(xs: list[float], ys: list[float]) -> list[float]:
    """Fritsch-Carlson monotone tangents for strictly increasing xs and monotone ys."""
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    delta = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            m[i] = 0.0
        else:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])

    def endpoint(h0: float, h1: float, d0: float, d1: float) -> float:
        d = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if d * d0 <= 0:
            return 0.0
        if d1 * d0 <= 0 and abs(d) > 3 * abs(d0):
            return 3 * d0
        return d

    if n == 2:
        m[0] = m[1] = delta[0]
    else:
        m[0] = endpoint(h[0], h[1], delta[0], delta[1])
        m[-1] = endpoint(h[-2], h[-3] if n > 3 else h[-2], delta[-1], delta[-2])
    return m


def pchip_eval(xs: list[float], ys: list[float], grid: list[float]) -> list[float]:
    """Monotone cubic Hermite values of the interpolant at ``grid`` (grid inside xs range)."""
    m = _pchip_slopes(xs, ys)
    out, seg = [], 0
    for x in grid:
        while seg < len(xs) - 2 and xs[seg + 1] < x:
            seg += 1
        h = xs[seg + 1] - xs[seg]
        t = (x - xs[seg]) / h
        h00 = (1 + 2 * t) * (1 - t) ** 2
        h10 = t * (1 - t) ** 2
        h01 = t * t * (3 - 2 * t)
        h11 = t * t * (t - 1)
        out.append(h00 * ys[seg] + h10 * h * m[seg] + h01 * ys[seg + 1] + h11 * h * m[seg + 1])
    return out


def smooth_shape(shape: list[float], sigma_bins: float) -> list[float]:
    """Gaussian-kernel smoothing of a [0,1]-normalized CDF shape's PMF, reflected at the
    edges, returned as a renormalized CDF shape (shape[0]=0, shape[-1]=1)."""
    if sigma_bins <= 0:
        return shape
    n = len(shape)
    pmf = [shape[i + 1] - shape[i] for i in range(n - 1)]
    half = max(1, int(math.ceil(4 * sigma_bins)))
    kern = [math.exp(-0.5 * (k / sigma_bins) ** 2) for k in range(-half, half + 1)]
    ksum = sum(kern)
    kern = [k / ksum for k in kern]
    nb = len(pmf)
    out = [0.0] * nb
    for i, p in enumerate(pmf):
        if p == 0.0:
            continue
        for k, kv in zip(range(-half, half + 1), kern, strict=True):
            j = i + k
            if j < 0:               # reflect at the lower edge
                j = -j - 1
            elif j >= nb:           # reflect at the upper edge
                j = 2 * nb - 1 - j
            out[j] += p * kv
    total = sum(out)
    cdf = [0.0]
    for p in out:
        cdf.append(cdf[-1] + p / total)
    cdf[-1] = 1.0
    return cdf


# ------------------------------------------------------- construction (mirrors core.py)

def build_cdf(
    pcts: dict[str, float],
    scaling: dict,
    *,
    p_below: float | None,
    p_above: float | None,
    interp: str = "linear",
    halve: bool = True,
    kernel_c: float = 0.0,
    cdf_size: int = 201,
) -> list[float] | None:
    """percentiles (+ optional declared escape mass) -> platform CDF, mirroring
    ``percentiles_to_cdf`` exactly except for the swappable interpolation, the optional
    no-halving anchor rule and the optional kernel smoothing of the shape."""
    lo, hi = float(scaling["range_min"]), float(scaling["range_max"])
    zero_point = scaling.get("zero_point")
    lower_open = bool(scaling.get("lower_open"))
    upper_open = bool(scaling.get("upper_open"))
    eps = (hi - lo) * 1e-4
    clipped = {k: min(max(float(v), lo + eps), hi - eps) for k, v in pcts.items()}
    declared = sorted((float(k) / 100.0, v) for k, v in clipped.items())

    escape_declared = p_below is not None or p_above is not None
    pb = float(p_below or 0.0) if lower_open else 0.0
    pa = float(p_above or 0.0) if upper_open else 0.0

    locs = [clamp01(_scale_location(v, lo, hi, zero_point)) for _, v in declared]
    if escape_declared:
        inside = 1.0 - pb - pa
        points = [(loc, pb + f * inside) for loc, (f, _) in zip(locs, declared, strict=True)]
        lower_frac, upper_frac = pb, 1.0 - pa
    else:
        points = [(loc, f) for loc, (f, _) in zip(locs, declared, strict=True)]
        min_frac, max_frac = declared[0][0], declared[-1][0]
        if halve:
            lower_frac = 0.5 * min_frac if lower_open else 0.0
            upper_frac = 1.0 - 0.5 * (1.0 - max_frac) if upper_open else 1.0
        else:
            lower_frac, upper_frac = 0.0, 1.0
    anchors = [(0.0, lower_frac), *points, (1.0, upper_frac)]
    for (xa, _), (xb, _) in zip(anchors, anchors[1:], strict=False):
        if xa >= xb:
            return None

    locations = [i / (cdf_size - 1) for i in range(cdf_size)]
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    if interp == "pchip":
        raw = pchip_eval(xs, ys, locations)
    else:
        raw, seg = [], 0
        for x in locations:
            while seg < len(anchors) - 2 and anchors[seg + 1][0] < x:
                seg += 1
            (x0, y0), (x1, y1) = anchors[seg], anchors[seg + 1]
            raw.append(y0 + (y1 - y0) * (x - x0) / (x1 - x0))

    span = raw[-1] - raw[0]
    if span <= 0:
        return None
    shape = [(y - raw[0]) / span for y in raw]

    if kernel_c > 0:
        loc25 = clamp01(_scale_location(clipped["25"], lo, hi, zero_point))
        loc75 = clamp01(_scale_location(clipped["75"], lo, hi, zero_point))
        shape = smooth_shape(shape, kernel_c * (loc75 - loc25) * (cdf_size - 1))

    if escape_declared:
        start = max(pb, MIN_OPEN_TAIL) if lower_open else 0.0
        end = 1.0 - (max(pa, MIN_OPEN_TAIL) if upper_open else 0.0)
        interior = end - start
        if interior <= CDF_UNIFORM_MIX:
            return None
        cdf = [start + (interior - CDF_UNIFORM_MIX) * r + CDF_UNIFORM_MIX * x
               for r, x in zip(shape, locations, strict=True)]
    elif lower_open and upper_open:
        cdf = [0.988 * r + 0.01 * x + 0.001 for r, x in zip(shape, locations, strict=True)]
    elif lower_open:
        cdf = [0.989 * r + 0.01 * x + 0.001 for r, x in zip(shape, locations, strict=True)]
    elif upper_open:
        cdf = [0.989 * r + 0.01 * x for r, x in zip(shape, locations, strict=True)]
    else:
        cdf = [0.99 * r + 0.01 * x for r, x in zip(shape, locations, strict=True)]

    cap = min(1.0, MAX_PMF_VALUE * (200.0 / (cdf_size - 1)))
    pmf = [b - a for a, b in zip(cdf, cdf[1:], strict=False)]
    pmf = _cap_pmf(pmf, cap, cdf[-1] - cdf[0])
    out = [cdf[0]]
    for p in pmf:
        out.append(out[-1] + p)
    return [round(v, 10) for v in out]


# ----------------------------------------------------------------------------- readout

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolutions", action="append", required=True)
    ap.add_argument("--window", nargs=2, action="append", metavar=("START", "END"))
    ap.add_argument("--journal", default=str(JOURNAL))
    args = ap.parse_args()

    windows = parse_windows(args.window)
    resolutions = load_resolutions([Path(p) for p in args.resolutions])
    _, numerics = load_wave(Path(args.journal), windows)

    rows = []
    for row in numerics:
        qid = (row.get("source") or {}).get("question_id")
        if qid not in resolutions or not row.get("submitted_cdf") or not row.get("scaling"):
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["source"]["question_id"])
    names = wave_names(windows)
    print(f"resolved numerics with a submitted CDF: {len(rows)} "
          f"({', '.join(f'{names[i]}: {sum(1 for r in rows if r[WAVE_KEY] == i)}' for i in range(len(windows)))})")

    variants: list[tuple[str, dict]] = [
        ("rebuilt linear (sanity)", dict(interp="linear")),
        ("pchip", dict(interp="pchip")),
        ("linear w=1.15", dict(interp="linear", w=1.15)),
        ("pchip  w=1.15", dict(interp="pchip", w=1.15)),
        ("linear w=1.3", dict(interp="linear", w=1.3)),
        ("pchip  w=1.3", dict(interp="pchip", w=1.3)),
        ("linear nohalve", dict(interp="linear", halve=False)),
        ("pchip  nohalve", dict(interp="pchip", halve=False)),
        ("pchip  nohalve w=1.15", dict(interp="pchip", halve=False, w=1.15)),
        ("pchip  nohalve w=1.3", dict(interp="pchip", halve=False, w=1.3)),
        ("linear kernel c=0.25", dict(interp="linear", kernel_c=0.25)),
        ("linear kernel c=0.5", dict(interp="linear", kernel_c=0.5)),
        ("pchip  kernel c=0.25", dict(interp="pchip", kernel_c=0.25)),
    ]

    def cdf_for(row: dict, spec: dict) -> list[float] | None:
        pcts = {str(k): float(v) for k, v in row["percentiles"].items()}
        if "w" in spec:
            pcts = widen(pcts, spec["w"])
        return build_cdf(
            pcts, row["scaling"],
            p_below=row.get("p_below_lower"), p_above=row.get("p_above_upper"),
            interp=spec.get("interp", "linear"), halve=spec.get("halve", True),
            kernel_c=spec.get("kernel_c", 0.0),
            cdf_size=int(row["scaling"].get("cdf_size") or 201),
        )

    base_scores = [score_row(r["submitted_cdf"], resolutions[r["source"]["question_id"]],
                             r["scaling"]) for r in rows]
    base_total = sum(base_scores)
    n = len(rows)
    print(f"\n{'variant':<24} {'total':>8} {'vs sub':>8} {'mean/q':>8} {'worst':>8} "
          f"{'in 10-90':>9}  per-wave totals")
    print(f"{'submitted (identity)':<24} {base_total:>8.0f} {0:>+8.0f} "
          f"{st.mean(base_scores):>+8.1f} {min(base_scores):>+8.1f} "
          f"{sum(1 for r, _ in zip(rows, base_scores, strict=True) if 0.10 <= cdf_at(r['submitted_cdf'], location_of(resolutions[r['source']['question_id']], r['scaling'])) <= 0.90):>6}/{n}")

    deltas_by_variant: dict[str, list[float]] = {}
    for name, spec in variants:
        scores, pits, skipped = [], [], 0
        for row, base in zip(rows, base_scores, strict=True):
            cdf = cdf_for(row, spec)
            if cdf is None:
                scores.append(base)   # fall back to submitted on infeasible rebuilds
                pits.append(cdf_at(row["submitted_cdf"], location_of(
                    resolutions[row["source"]["question_id"]], row["scaling"])))
                skipped += 1
                continue
            y = resolutions[row["source"]["question_id"]]
            scores.append(score_row(cdf, y, row["scaling"]))
            pits.append(cdf_at(cdf, location_of(y, row["scaling"])))
        inside = sum(1 for p in pits if 0.10 <= p <= 0.90)
        per_wave = []
        for i in range(len(windows)):
            tot = sum(s - b for s, b, r in zip(scores, base_scores, rows, strict=True)
                      if r[WAVE_KEY] == i)
            per_wave.append(f"{names[i]}: {tot:+.0f}")
        deltas_by_variant[name] = [s - b for s, b in zip(scores, base_scores, strict=True)]
        note = f"  (skipped {skipped})" if skipped else ""
        print(f"{name:<24} {sum(scores):>8.0f} {sum(scores) - base_total:>+8.0f} "
              f"{st.mean(scores):>+8.1f} {min(scores):>+8.1f} {inside:>6}/{n}  "
              f"{'  '.join(per_wave)}{note}")

    sanity = deltas_by_variant["rebuilt linear (sanity)"]
    print(f"\nrebuild fidelity: mean |delta| {st.mean(abs(d) for d in sanity):.2f} pts, "
          f"max |delta| {max(abs(d) for d in sanity):.2f} pts")

    print(f"\npaired bootstrap vs submitted (90% CI, 10k draws, seed 7), n={n}:")
    for name, _ in variants:
        if name.startswith("rebuilt"):
            continue
        d = deltas_by_variant[name]
        lo_ci, hi_ci = boot_ci(d)
        wins = sum(1 for x in d if x > 0)
        print(f"  {name:<24} mean {st.mean(d):>+7.2f}/q  CI90 [{lo_ci:>+8.2f}, {hi_ci:>+8.2f}]  "
              f"helps on {wins}/{sum(1 for x in d if abs(x) > 1e-9)} changed")


if __name__ == "__main__":
    main()
