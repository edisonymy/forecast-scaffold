"""Paired test: kernel-smoothed PCHIP vs plain PCHIP on resolved continuous questions.

STATUS: PREREGISTERED 2026-09-03 for the 2026-08-24 MiniBench wave readout. Motivation:
the operator observed a cusp at the median and corners at the anchors in our submitted
PDFs (pchip is only C1, and five anchors imply piecewise densities that differ 2x between
adjacent segments). ``minibench_smooth_cdf.py`` already carries a Gaussian-kernel
smoothing of the shape PMF (sigma = c * IQR in bins, reflected at the edges); on waves
1-3 plus the summer numerics (n=97) ``pchip kernel c=0.25`` scored +4.59/q vs submitted,
CI90 [+0.84, +8.78], helping on 52/91 changed rows — but that comparison is against the
SUBMITTED (mostly linear) CDFs, and the c grid was read off the same data.

Decision rule (fixed here, before wave 4 resolves): promote ``pchip + kernel c=0.25`` to
production if, on waves 1-4 pooled with the summer numerics, the PAIRED delta vs plain
pchip has a 90% bootstrap CI excluding zero AND it helps on >= 55% of changed rows AND
the mean absolute quantile drift at the five anchors is < 0.02 (the smoother must not
silently re-widen or re-centre what the model declared). c is fixed at 0.25; the other
c values are reported for context only and cannot be promoted from this readout.

Usage: same --resolutions / --window flags as minibench_smooth_cdf.py.
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from forecast_scaffold.core import _scale_location  # noqa: E402
from bench.analysis.minibench_counterfactuals import (  # noqa: E402
    JOURNAL,
    load_resolutions,
    load_wave,
    parse_windows,
)
from bench.analysis.minibench_numeric_tails import (  # noqa: E402
    boot_ci,
    cdf_at,
    clamp01,
    location_of,
    score_row,
)
from bench.analysis.minibench_smooth_cdf import build_cdf  # noqa: E402

C_GRID = (0.15, 0.25, 0.35)
PRIMARY_C = 0.25
ANCHORS = ("10", "25", "50", "75", "90")


def quantile_drift(row: dict, cdf: list[float]) -> float:
    """Mean |CDF(anchor) - declared fraction| over the five declared percentiles, on the
    CONDITIONAL in-range scale when escape mass was declared."""
    scaling = row["scaling"]
    lo, hi = float(scaling["range_min"]), float(scaling["range_max"])
    pb = float(row.get("p_below_lower") or 0.0) if scaling.get("lower_open") else 0.0
    pa = float(row.get("p_above_upper") or 0.0) if scaling.get("upper_open") else 0.0
    inside = 1.0 - pb - pa
    drifts = []
    for k in ANCHORS:
        loc = clamp01(_scale_location(float(row["percentiles"][k]), lo, hi, scaling.get("zero_point")))
        target = pb + (float(k) / 100.0) * inside
        drifts.append(abs(cdf_at(cdf, loc) - target))
    return st.mean(drifts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolutions", action="append", required=True)
    ap.add_argument("--window", nargs=2, action="append", metavar=("START", "END"))
    ap.add_argument("--journal", default=str(JOURNAL))
    args = ap.parse_args()
    windows = parse_windows(args.window)
    resolutions = load_resolutions([Path(p) for p in args.resolutions])
    _, numerics = load_wave(Path(args.journal), windows)
    rows = [r for r in numerics
            if (r.get("source") or {}).get("question_id") in resolutions
            and r.get("submitted_cdf") and r.get("scaling")]
    rows.sort(key=lambda r: r["source"]["question_id"])
    n = len(rows)
    print(f"resolved continuous rows: {n}")

    def build(row: dict, c: float) -> list[float] | None:
        pcts = {str(k): float(v) for k, v in row["percentiles"].items()}
        return build_cdf(pcts, row["scaling"], p_below=row.get("p_below_lower"),
                         p_above=row.get("p_above_upper"), interp="pchip", kernel_c=c,
                         cdf_size=int(row["scaling"].get("cdf_size") or 201))

    base = []
    for row in rows:
        cdf = build(row, 0.0)
        y = resolutions[row["source"]["question_id"]]
        base.append(score_row(cdf, y, row["scaling"]) if cdf else None)
    print(f"\n{'variant':<22} {'mean d/q':>9} {'CI90':>20} {'helps':>9} {'drift':>7}  {'in 10-90':>8}")
    for c in C_GRID:
        deltas, drifts, pits = [], [], []
        for row, b in zip(rows, base, strict=True):
            cdf = build(row, c)
            if cdf is None or b is None:
                continue
            y = resolutions[row["source"]["question_id"]]
            deltas.append(score_row(cdf, y, row["scaling"]) - b)
            drifts.append(quantile_drift(row, cdf))
            pits.append(cdf_at(cdf, location_of(y, row["scaling"])))
        lo_ci, hi_ci = boot_ci(deltas)
        changed = [d for d in deltas if abs(d) > 1e-9]
        helps = sum(1 for d in changed if d > 0)
        tag = "  <- PRIMARY" if c == PRIMARY_C else ""
        print(f"pchip kernel c={c:<7} {st.mean(deltas):>+9.2f} [{lo_ci:>+8.2f}, {hi_ci:>+8.2f}] "
              f"{helps:>4}/{len(changed):<4} {st.mean(drifts):>7.4f}  "
              f"{sum(1 for p in pits if 0.10 <= p <= 0.90):>4}/{len(pits)}{tag}")
    plain_drift = st.mean(quantile_drift(r, build(r, 0.0)) for r in rows if build(r, 0.0))
    print(f"\nplain pchip anchor drift (reference): {plain_drift:.4f}")


if __name__ == "__main__":
    main()
