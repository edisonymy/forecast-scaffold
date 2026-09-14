"""PREREGISTERED: does pooling independent runs beat the single research run on continuous
questions?

STATUS: PREREGISTERED 2026-09-03, BEFORE any pooled continuous row has resolved. The
transform is not chosen after seeing outcomes — v0.4.28 made the bot pool numeric/discrete/
date questions across the tier's independent runs (quantile averaging, escape masses
averaged over the runs that declared one) and journals the counterfactual alongside it, so
this script only reads what the harness already wrote.

WHY THIS IS A CLEAN PAIRED TEST, NOT AN A/B ARM
Every pooled record carries both arms of the comparison on the SAME question, at the SAME
time, from the SAME research:
  - ``percentiles``      the pooled set that was actually submitted (arm: POOLED)
  - ``percentiles_run1`` the research run's own set, which is exactly what the
                         pre-v0.4.28 harness would have submitted (arm: RUN-1)
  - ``run_escapes[0]``   that research run's own declared escape masses, so the RUN-1 CDF
                         is rebuilt with the tails run 1 actually declared rather than the
                         pooled ones
No question is spent on a control arm and there is no allocation to get wrong; the only
assumption is that the platform would have scored the RUN-1 CDF the way this script does.

===============================================================================
DECISION RULE (fixed 2026-09-03, before any data — do not renegotiate it after a readout)
===============================================================================
After TWO MiniBench waves have resolved, at n >= 60 scored continuous rows:
  * KEEP pooling   if the paired delta (POOLED minus RUN-1) has a CI90 that excludes zero
                   ON THE POSITIVE SIDE;
  * REVERT to single runs if the paired mean delta is NEGATIVE;
  * otherwise (mean positive, CI90 straddling zero) EXTEND by one more wave and re-read.
The delta is in Metaculus leaderboard points per question. MiniBench pays a PEER score —
this log mass minus the field's average — and the field term does not depend on our
forecast, so it cancels exactly in a paired comparison of two of OUR OWN forecasts on the
same question. Absolute levels printed here are baseline scores, not leaderboard positions.

SCOPE
Continuous only (numeric / discrete / date). MULTIPLE CHOICE IS OUT OF SCOPE for this
scorer: MC rows journal ``probabilities_run1`` and pool the same way, but the platform's MC
score is not the continuous log-density formula implemented here, and the summer MC sample
is far too small to power a rule. It needs its own script and its own preregistration.

Both arms are rebuilt through the production ``percentiles_to_cdf`` with
``interpolation="pchip"`` (what production submits since v0.4.25), honoring each row's own
``cdf_size`` and open/closed bounds. Rebuilding BOTH arms — rather than scoring the
journaled ``submitted_cdf`` against a rebuilt counterfactual — keeps the comparison free of
construction differences; the fidelity line at the end reports how far the rebuilt pooled
CDF lands from the one actually submitted (it should be ~0).

Outcomes come from the resolutions overlay ``bot/journal/resolutions.jsonl``
(bench/sync_resolutions.py). Numeric outcomes are floats; an outcome the platform reports
out of range arrives as the string "above_upper_bound" / "below_lower_bound" and is placed
just past that bound, the same encoding bench/fetch_minibench_wave.py uses for the summer
resolutions file (bench/analysis/summer-2026-numeric-resolutions-2026-09-03.json).

Usage:
    python bench/analysis/pooled_vs_single.py
    python bench/analysis/pooled_vs_single.py \
        --window 2026-09-14 2026-09-27 --window 2026-09-28 2026-10-11
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from forecast_scaffold.core import percentiles_to_cdf  # noqa: E402

from bench.analysis.minibench_numeric_tails import (  # noqa: E402
    boot_ci,
    cdf_at,
    location_of,
    score_row,
)

JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
OVERLAY = ROOT / "bot" / "journal" / "resolutions.jsonl"
CONTINUOUS = ("numeric", "discrete", "date")
QUANTILES = ("10", "25", "50", "75", "90")

#: The decision rule's sample-size gate: two MiniBench waves of continuous questions.
DECISION_N = 60


# ------------------------------------------------------------------------------- loading

def load_rows(journal: Path) -> list[dict[str, Any]]:
    """Latest live journal row per Metaculus question id that carries a pooled continuous
    forecast AND its single-run counterfactual."""
    latest: dict[int, dict[str, Any]] = {}
    with journal.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("dry_run"):  # never scored as the live track record
                continue
            if row.get("question_type") not in CONTINUOUS:
                continue
            if not row.get("percentiles_run1") or not row.get("percentiles"):
                continue
            if not row.get("scaling"):
                continue
            qid = (row.get("source") or {}).get("question_id")
            if qid is None:
                continue
            prior = latest.get(int(qid))
            if prior is None or str(row.get("forecast_at")) >= str(prior.get("forecast_at")):
                latest[int(qid)] = row
    return sorted(latest.values(), key=lambda r: r["source"]["question_id"])


def load_outcomes(overlay: Path) -> dict[int, Any]:
    """question_id -> raw outcome for every RESOLVED (not annulled) overlay row, latest wins."""
    outcomes: dict[int, Any] = {}
    if not overlay.exists():
        return outcomes
    with overlay.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                res = json.loads(line)
            except json.JSONDecodeError:
                continue
            qid = res.get("question_id")
            if qid is None or res.get("annulled") or res.get("status") != "resolved":
                continue
            if res.get("outcome") is None:
                continue
            outcomes[int(qid)] = res["outcome"]
    return outcomes


def numeric_outcome(raw: Any, scaling: dict[str, Any]) -> float | None:
    """Overlay outcome -> a number on the question's axis.

    Out-of-range resolutions arrive as platform strings; they are placed just past the bound
    exactly as bench/fetch_minibench_wave.py does, which is the encoding the summer
    resolutions file already uses — so a row that escaped the range is scored in the
    out-of-bound bucket both arms have to price, not silently dropped."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw)
    if text == "below_lower_bound":
        lo = float(scaling["range_min"])
        return lo - max(1.0, abs(lo) * 0.01)
    if text == "above_upper_bound":
        hi = float(scaling["range_max"])
        return hi + max(1.0, abs(hi) * 0.01)
    return None


# ------------------------------------------------------------------------------ rebuilds

def build(pcts: dict[str, Any], scaling: dict[str, Any],
          below: float | None, above: float | None) -> list[float] | None:
    """Production CDF construction for one arm; None when the declared set is infeasible."""
    try:
        return percentiles_to_cdf(
            {str(k): float(v) for k, v in pcts.items() if str(k) in QUANTILES},
            float(scaling["range_min"]), float(scaling["range_max"]),
            lower_open=bool(scaling.get("lower_open")),
            upper_open=bool(scaling.get("upper_open")),
            zero_point=scaling.get("zero_point"),
            cdf_size=int(scaling.get("cdf_size") or 201),
            p_below_lower=below,
            p_above_upper=above,
            # Both arms share one construction on purpose: this test is about the
            # percentiles, not the interpolation (which has its own preregistration).
            interpolation="pchip",
        )
    except ValueError:
        return None


def run1_escapes(row: dict[str, Any]) -> tuple[float | None, float | None]:
    """The research run's OWN declared escape masses (run_escapes[0]), falling back to the
    pooled ones on a row written before run_escapes existed."""
    escapes = row.get("run_escapes")
    if isinstance(escapes, list) and escapes and isinstance(escapes[0], list):
        pair = escapes[0]
        below = pair[0] if len(pair) > 0 else None
        above = pair[1] if len(pair) > 1 else None
        return (None if below is None else float(below),
                None if above is None else float(above))
    return row.get("p_below_lower"), row.get("p_above_upper")


# ------------------------------------------------------------------------------- readout

def in_any_window(resolve_by: str, windows: list[tuple[str, str]]) -> bool:
    return any(start <= resolve_by <= end for start, end in windows)


def report(label: str, scored: list[dict[str, Any]], *, table: bool) -> list[float]:
    print(f"\n{'=' * 78}\n=== {label} ===")
    print(f"scored continuous rows with a journaled counterfactual: {len(scored)}")
    if not scored:
        return []
    if table:
        print(f"\n{'qid':>6} {'runs':>4} {'run1 p50':>11} {'pool p50':>11} {'outcome':>11} "
              f"{'PIT':>6} {'run1':>8} {'pooled':>8} {'delta':>8}  question")
        for s in scored:
            print(f"{s['qid']:>6} {s['n_runs']:>4} {s['run1_p50']:>11.4g} "
                  f"{s['pooled_p50']:>11.4g} {s['outcome']:>11.4g} {s['pit']:>6.3f} "
                  f"{s['run1_score']:>8.1f} {s['pooled_score']:>8.1f} {s['delta']:>+8.1f}  "
                  f"{s['question'][:40]}")
    deltas = [s["delta"] for s in scored]
    changed = [d for d in deltas if abs(d) > 1e-9]
    helps = sum(1 for d in changed if d > 0)
    lo, hi = boot_ci(deltas) if len(deltas) > 1 else (float("nan"), float("nan"))
    print(f"\n  mean POOLED - RUN-1 {st.mean(deltas):+8.2f} pts/question   "
          f"median {st.median(deltas):+8.2f}")
    print(f"  CI90 (paired bootstrap, 10k draws, seed 7) [{lo:+8.2f}, {hi:+8.2f}]  "
          f"(positive favors pooling)")
    print(f"  helps on {helps}/{len(changed)} rows the pool actually changed "
          f"({len(deltas) - len(changed)} identical)")
    return deltas


def verdict(deltas: list[float]) -> None:
    print(f"\n{'=' * 78}\n=== DECISION RULE ===")
    print("  KEEP pooling if CI90 excludes zero on the positive side at n >= "
          f"{DECISION_N}; REVERT if the mean is negative; otherwise EXTEND one more wave.")
    n = len(deltas)
    if n < 2:
        print(f"  VERDICT: not enough scored rows yet (n={n}).")
        return
    mean = st.mean(deltas)
    lo, hi = boot_ci(deltas)
    if mean < 0:
        print(f"  VERDICT: REVERT — paired mean {mean:+.2f} pts/question is negative "
              f"(n={n}, CI90 [{lo:+.2f}, {hi:+.2f}]).")
    elif n < DECISION_N:
        print(f"  VERDICT: EXTEND — only n={n} of the required {DECISION_N} scored rows "
              f"(mean {mean:+.2f}, CI90 [{lo:+.2f}, {hi:+.2f}]).")
    elif lo > 0:
        print(f"  VERDICT: KEEP — mean {mean:+.2f} pts/question, CI90 [{lo:+.2f}, "
              f"{hi:+.2f}] excludes zero on the positive side (n={n}).")
    else:
        print(f"  VERDICT: EXTEND one more wave — mean {mean:+.2f} is positive but CI90 "
              f"[{lo:+.2f}, {hi:+.2f}] straddles zero (n={n}).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paired pooled-vs-single-run scorer.")
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--resolutions", type=Path, default=OVERLAY,
                        help="the resolutions overlay written by bench/sync_resolutions.py")
    parser.add_argument("--window", nargs=2, metavar=("START", "END"), action="append",
                        help="resolve_by window identifying one wave; repeatable. "
                             "Omit to report every scored row as one group.")
    args = parser.parse_args(argv)

    rows = load_rows(args.journal)
    outcomes = load_outcomes(args.resolutions)
    print(__doc__.split("Usage:")[0].rstrip())
    print(f"\njournal rows carrying percentiles_run1: {len(rows)}  "
          f"(resolved of those: {sum(1 for r in rows if r['source']['question_id'] in outcomes)})")

    scored: list[dict[str, Any]] = []
    skipped: list[tuple[int, str]] = []
    fidelity: list[float] = []
    for row in rows:
        qid = int(row["source"]["question_id"])
        if qid not in outcomes:
            continue
        scaling = row["scaling"]
        outcome = numeric_outcome(outcomes[qid], scaling)
        if outcome is None:
            skipped.append((qid, f"unscoreable outcome {outcomes[qid]!r}"))
            continue
        below_1, above_1 = run1_escapes(row)
        pooled_cdf = build(row["percentiles"], scaling,
                           row.get("p_below_lower"), row.get("p_above_upper"))
        run1_cdf = build(row["percentiles_run1"], scaling, below_1, above_1)
        if pooled_cdf is None or run1_cdf is None:
            skipped.append((qid, "infeasible rebuild"))
            continue
        pooled_score = score_row(pooled_cdf, outcome, scaling)
        run1_score = score_row(run1_cdf, outcome, scaling)
        submitted = row.get("submitted_cdf")
        # Fidelity is only a fair check on rows built the way this script rebuilds them:
        # a pre-v0.4.25 (piecewise-linear) row legitimately differs from its pchip rebuild.
        if submitted and scaling.get("interpolation") == "pchip":
            fidelity.append(abs(score_row(submitted, outcome, scaling) - pooled_score))
        scored.append({
            "qid": qid,
            "wave": None,
            "resolve_by": str(row.get("resolve_by") or ""),
            "question": str(row.get("question", "")),
            "n_runs": len(row.get("run_percentiles") or []),
            "run1_p50": float(row["percentiles_run1"]["50"]),
            "pooled_p50": float(row["percentiles"]["50"]),
            "outcome": outcome,
            "pit": cdf_at(pooled_cdf, location_of(outcome, scaling)),
            "pooled_score": pooled_score,
            "run1_score": run1_score,
            "delta": pooled_score - run1_score,
        })

    if skipped:
        print(f"skipped {len(skipped)}: " + ", ".join(f"{q} ({why})" for q, why in skipped))

    windows = [(str(a), str(b)) for a, b in (args.window or [])]
    if windows:
        for i, (start, end) in enumerate(windows):
            in_wave = [s for s in scored if start <= s["resolve_by"] <= end]
            report(f"WAVE {i + 1} (resolve_by {start}..{end})", in_wave, table=True)
        outside = [s for s in scored if not in_any_window(s["resolve_by"], windows)]
        if outside:
            print(f"\n{len(outside)} scored row(s) fall in no --window and are excluded from "
                  f"the per-wave tables but NOT from the pooled figure below: "
                  f"{[s['qid'] for s in outside]}")
        deltas = report(f"POOLED (all {len(windows)} wave(s))", scored, table=False)
    else:
        deltas = report("ALL SCORED ROWS (no --window given)", scored, table=True)

    if fidelity:
        print(f"\nrebuild fidelity vs the CDF actually submitted: mean |delta| "
              f"{st.mean(fidelity):.2f} pts, max {max(fidelity):.2f} pts "
              f"(should be ~0; a large value means the rebuild is not the submission)")
    print("\nMULTIPLE CHOICE IS OUT OF SCOPE here — MC rows journal probabilities_run1 and "
          "pool identically, but the platform scores them with a different formula.")
    verdict(deltas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
