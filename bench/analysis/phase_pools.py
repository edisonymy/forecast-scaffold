"""PREREGISTERED: do the two phases on top of parallel research earn their cost?

STATUS: PREREGISTERED 2026-09-03, BEFORE any phased row has resolved. Nothing here is
chosen after seeing outcomes — v0.4.28 added two optional phases to angle mode and made the
harness journal EVERY phase's pool beside the number it actually submitted, so this script
only reads what is already written.

THE THREE ARMS, all on the same question, at the same time, from the same research
  * ``pool_phase1``  the pool of the N independent research runs (arm: PHASE 1)
  * ``pool_phase2``  the pool of the SECOND round, taken after each run saw the others'
                     estimate-free dossiers — shared evidence, independent judgment
                     (arm: PHASE 2; present only where the tier had ``share_evidence`` on,
                     which is OFF in production: this arm accumulates only under experiment)
  * ``supervisor``   one reconciler that saw every dossier and every estimate WITH its
                     reasoning, classified the disagreements FACTUAL vs JUDGMENT, settled
                     the factual ones (with searches when the runs disagreed enough to buy
                     them) and issued its own number (arm: SUPERVISOR)
No question is spent on a control arm and there is no allocation to get wrong. The only
assumption is that the platform would have scored a non-submitted arm the way this script
does.

===============================================================================
DECISION RULES (fixed 2026-09-03, before any data — do not renegotiate after a readout)
===============================================================================
PHASE 2 (shared evidence) is KEPT only if ALL THREE hold:
  (a) its paired delta vs PHASE 1 is >= 0 (mean, in that question type's score); AND
  (b) that delta's CI90 lower bound is > 0 — the same evidential bar the supervisor faces;
      AND
  (c) the herding check passes: it is NOT the case that THIS FAMILY's mean(spread_phase2 /
      spread_phase1) < 0.5 while the paired delta is <= 0.
Clause (c) is the whole reason the spreads are journaled. Circulating other forecasters'
material is the documented way to collapse a group's variance WITHOUT improving its mean
accuracy (Lorenz et al. 2011, PNAS 108(22):9020 — N=144, "remarkably little" social
influence needed). A variant that halves the ensemble's disagreement and buys nothing has
reproduced that failure in-bot, and it will LOOK like agreement, so it has to be ruled out
by a number fixed in advance rather than by reading the readout.

RULE CHANGE 2026-09-03 (still before any phase-2 row has resolved — share_evidence is OFF in
production, so this tightens a rule on an EMPTY sample, not on a readout). Two corrections
found in review:
  * clause (b) is NEW. As written, phase 2 was KEPT on a mean delta of +0.0001 at n=3 while
    the supervisor — the arm with published supporting evidence — needed a CI90 excluding
    zero. The weaker arm must not have the easier gate, so phase 2 now mirrors
    supervisor_verdict's bound. The supervisor's separate sample-size gate is NOT copied
    across: it is calibrated to the reconciler's own cost, and the CI does the work here.
  * clause (c) is now computed PER FAMILY. The spread of a binary pool is a difference of
    probabilities and the spread of a continuous pool is a difference of medians in question
    units; averaging their ratios into one number let a handful of continuous rows decide the
    binary verdict (and vice versa). Each family's verdict now uses its own ratio.

THE SUPERVISOR is KEPT only if its paired delta VS THE PHASE IT CONSUMED (phase 2 where
that ran, else phase 1) has a CI90 excluding zero on the POSITIVE side, after two MiniBench
waves have resolved: n >= 40 scored binaries, or n >= 60 scored rows mixed across types.
Below that n the verdict is EXTEND, whatever the mean says. The reconciler is a single
point of bias over an ensemble and its only published evidence is single-lab and
self-reported (AIA Forecaster: mean-of-10 Brier 0.1140 -> 0.1125 reconciled), so it gets
the strict rule, not the permissive one.

SCORING, per type
  * binary      LOG SCORE ln(p if YES else 1-p), higher is better; the paired delta is in
                nats. The Brier score is computed too, but ONLY for the herding check's
                accuracy term, where a proper score bounded on [0,1] keeps the "delta <= 0"
                clause from being dominated by one confident miss.
  * continuous  the platform's own continuous baseline score in leaderboard points, via the
                production ``percentiles_to_cdf`` (pchip, each row's own cdf_size and
                open/closed bounds) and ``minibench_numeric_tails.score_row`` — the same
                construction ``pooled_vs_single.py`` uses, so the two readouts are
                comparable. MiniBench pays a PEER score; the field term cancels exactly in a
                paired comparison of two of our own forecasts on the same question.
  * MULTIPLE CHOICE IS OUT OF SCOPE, as in pooled_vs_single.py: the platform scores MC with
    a different formula and no MC sample here can power a rule. MC rows journal the same
    fields and can be added later under their own preregistration.

Escape masses: each continuous arm is rebuilt with the tails THAT arm declared —
``run_escapes_phase1`` pooled for phase 1, ``run_escapes`` pooled for phase 2, and the
supervisor's own declared pair for the supervisor — so no arm inherits another's tails.

Outcomes come from the resolutions overlay ``bot/journal/resolutions.jsonl``
(bench/sync_resolutions.py), with out-of-range platform strings placed just past the bound
exactly as bench/fetch_minibench_wave.py encodes them.

Usage:
    python bench/analysis/phase_pools.py
    python bench/analysis/phase_pools.py --journal bot/journal/forecasts.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from forecast_scaffold.core import percentiles_to_cdf, pool_escape_mass  # noqa: E402

from bench.analysis.minibench_numeric_tails import boot_ci, score_row  # noqa: E402

JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
OVERLAY = ROOT / "bot" / "journal" / "resolutions.jsonl"
CONTINUOUS = ("numeric", "discrete", "date")
QUANTILES = ("10", "25", "50", "75", "90")

#: The supervisor's sample-size gate: two MiniBench waves.
DECISION_N_BINARY = 40
DECISION_N_MIXED = 60
#: The herding clause: below this spread ratio, phase 2 must show a positive delta.
HERDING_RATIO = 0.5


# ------------------------------------------------------------------------------- loading

def load_rows(journal: Path) -> list[dict[str, Any]]:
    """Latest live journal row per Metaculus question id that carries a phase-1 pool.

    ``pool_phase1`` is written only in angle mode with a phase enabled, so its presence is
    exactly the inclusion criterion: a row without it never had an arm to compare."""
    latest: dict[int, dict[str, Any]] = {}
    if not journal.exists():
        return []
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
            if row.get("pool_phase1") is None:
                continue
            qid = (row.get("source") or {}).get("question_id")
            if qid is None:
                continue
            prior = latest.get(int(qid))
            if prior is None or str(row.get("forecast_at")) >= str(prior.get("forecast_at")):
                latest[int(qid)] = row
    return sorted(latest.values(), key=lambda r: r["source"]["question_id"])


def load_outcomes(overlay: Path) -> dict[int, Any]:
    """question_id -> raw outcome for every RESOLVED (not annulled) overlay row."""
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
    """Overlay outcome -> a number on the question's axis (same encoding as the other
    scorers: an out-of-range resolution arrives as a platform string)."""
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


# -------------------------------------------------------------------------------- arms

def pooled_escapes(escapes: Any) -> tuple[float | None, float | None]:
    """The escape pair one phase's runs pooled to, from its journaled per-run pairs."""
    if not isinstance(escapes, list) or not escapes:
        return None, None
    below = pool_escape_mass([pair[0] if isinstance(pair, list) and pair else None
                              for pair in escapes])
    above = pool_escape_mass([pair[1] if isinstance(pair, list) and len(pair) > 1 else None
                              for pair in escapes])
    return below, above


def build(pcts: dict[str, Any], scaling: dict[str, Any],
          below: float | None, above: float | None) -> list[float] | None:
    """Production CDF construction for one arm; None when the set is infeasible."""
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
            interpolation="pchip",
        )
    except (ValueError, KeyError, TypeError):
        return None


def binary_arms(row: dict[str, Any]) -> dict[str, float]:
    """arm name -> the probability that arm would have submitted."""
    arms: dict[str, float] = {"phase1": float(row["pool_phase1"])}
    if isinstance(row.get("pool_phase2"), (int, float)):
        arms["phase2"] = float(row["pool_phase2"])
    supervisor = row.get("supervisor") or {}
    if isinstance(supervisor.get("estimate"), (int, float)):
        arms["supervisor"] = float(supervisor["estimate"])
    return arms


def continuous_arms(row: dict[str, Any]) -> dict[str, list[float]]:
    """arm name -> the CDF that arm would have submitted, each with its OWN tails."""
    scaling = row.get("scaling") or {}
    arms: dict[str, list[float]] = {}
    below, above = pooled_escapes(row.get("run_escapes_phase1"))
    if row.get("run_escapes_phase1") is None:
        # No second round ran, so the row's own per-run escapes ARE phase 1's.
        below, above = pooled_escapes(row.get("run_escapes"))
    cdf = build(row["pool_phase1"], scaling, below, above)
    if cdf is not None:
        arms["phase1"] = cdf
    if isinstance(row.get("pool_phase2"), dict):
        below2, above2 = pooled_escapes(row.get("run_escapes"))
        cdf2 = build(row["pool_phase2"], scaling, below2, above2)
        if cdf2 is not None:
            arms["phase2"] = cdf2
    estimate = (row.get("supervisor") or {}).get("estimate")
    if isinstance(estimate, dict) and isinstance(estimate.get("percentiles"), dict):
        cdf3 = build(estimate["percentiles"], scaling,
                     estimate.get("p_below_lower"), estimate.get("p_above_upper"))
        if cdf3 is not None:
            arms["supervisor"] = cdf3
    return arms


def log_score(p: float, outcome: bool) -> float:
    """ln of the probability put on what happened. Higher is better."""
    return math.log(max(p if outcome else 1.0 - p, 1e-12))


def brier(p: float, outcome: bool) -> float:
    """Loss on [0,1]; lower is better."""
    return (p - (1.0 if outcome else 0.0)) ** 2


# ------------------------------------------------------------------------------ readout

def paired(scored: list[dict[str, Any]], left: str, right: str) -> list[float]:
    """Per-row (left - right) score deltas over the rows carrying BOTH arms."""
    return [row["scores"][left] - row["scores"][right] for row in scored
            if left in row["scores"] and right in row["scores"]]


def report_pair(label: str, deltas: list[float], unit: str) -> tuple[float, float, float]:
    """Mean, CI90 lo/hi for one paired comparison (nan-safe on tiny n)."""
    if not deltas:
        print(f"  {label:<34} n=0")
        return float("nan"), float("nan"), float("nan")
    mean = st.mean(deltas)
    lo, hi = boot_ci(deltas) if len(deltas) > 1 else (float("nan"), float("nan"))
    helps = sum(1 for d in deltas if d > 1e-12)
    print(f"  {label:<34} n={len(deltas):<4} mean {mean:+8.4f} {unit}   "
          f"CI90 [{lo:+8.4f}, {hi:+8.4f}]   better on {helps}/{len(deltas)}")
    return mean, lo, hi


def herding_line(scored: list[dict[str, Any]]) -> float | None:
    """mean(spread_phase2 / spread_phase1) over the rows where phase 2 ran."""
    ratios = [
        float(row["spread_phase2"]) / float(row["spread_phase1"])
        for row in scored
        if row.get("spread_phase1") and row.get("spread_phase2") is not None
        and float(row["spread_phase1"]) > 0
    ]
    if not ratios:
        return None
    return st.mean(ratios)


def phase2_verdict(delta_mean: float, lo: float, ratio: float | None, n: int) -> str:
    """``ratio`` is THIS family's mean spread ratio and ``lo`` this family's CI90 lower
    bound on the paired delta (see the RULE CHANGE note in the header)."""
    if n == 0:
        return "no phase-2 rows have resolved yet"
    if math.isnan(delta_mean):
        return "no paired phase-2 rows yet"
    if delta_mean < 0:
        return f"DROP phase 2 — paired delta {delta_mean:+.4f} is negative (n={n})"
    if ratio is not None and ratio < HERDING_RATIO and delta_mean <= 0:
        return (f"DROP phase 2 — HERDING: spread ratio {ratio:.2f} < {HERDING_RATIO} with a "
                f"non-positive delta (n={n})")
    spread_note = f", spread ratio {ratio:.2f}" if ratio is not None else ""
    if math.isnan(lo):
        return f"EXTEND — CI unavailable at n={n} (mean {delta_mean:+.4f}{spread_note})"
    if lo <= 0:
        return (f"EXTEND phase 2 — mean {delta_mean:+.4f} >= 0 but its CI90 lower bound "
                f"{lo:+.4f} does not exclude zero{spread_note} (n={n})")
    return (f"KEEP phase 2 so far — paired delta {delta_mean:+.4f}, CI90 lower bound "
            f"{lo:+.4f} excludes zero on the positive side{spread_note} (n={n})")


def supervisor_verdict(mean: float, lo: float, n: int, n_binary: int, n_mixed: int) -> str:
    """``n`` is this family's paired sample; the GATE is on the whole run (n_binary
    binaries, or n_mixed rows across types) because the rule was written that way."""
    if n == 0:
        return "no supervisor rows have resolved yet"
    gate_met = n_binary >= DECISION_N_BINARY or n_mixed >= DECISION_N_MIXED
    if not gate_met:
        return (f"EXTEND — n={n} scored rows here, {n_mixed} across types "
                f"({n_binary} binary); the rule needs {DECISION_N_BINARY} binaries or "
                f"{DECISION_N_MIXED} mixed (mean {mean:+.4f})")
    if math.isnan(lo):
        return f"EXTEND — CI unavailable at n={n}"
    if lo > 0:
        return (f"KEEP the supervisor — mean {mean:+.4f}, CI90 lower bound {lo:+.4f} "
                f"excludes zero on the positive side (n={n})")
    return (f"DROP the supervisor — mean {mean:+.4f}, CI90 lower bound {lo:+.4f} does not "
            f"exclude zero (n={n}); the pool it consumed is submitted instead")


def score_group(rows: list[dict[str, Any]], outcomes: dict[int, Any],
                kind: str) -> list[dict[str, Any]]:
    """Score every arm of every resolved row of one type family."""
    scored: list[dict[str, Any]] = []
    for row in rows:
        qid = int(row["source"]["question_id"])
        if qid not in outcomes:
            continue
        entry: dict[str, Any] = {
            "qid": qid,
            "question": str(row.get("question", "")),
            "spread_phase1": row.get("spread_phase1"),
            "spread_phase2": row.get("spread_phase2"),
            "supervisor_mode": (row.get("supervisor") or {}).get("mode"),
            "consumed": "phase2" if row.get("pool_phase2") is not None else "phase1",
        }
        if kind == "binary":
            outcome = outcomes[qid]
            if not isinstance(outcome, bool):
                continue
            arms = binary_arms(row)
            entry["scores"] = {name: log_score(p, outcome) for name, p in arms.items()}
            entry["brier"] = {name: brier(p, outcome) for name, p in arms.items()}
            entry["outcome"] = outcome
        else:
            scaling = row.get("scaling") or {}
            if not scaling:
                continue
            outcome_value = numeric_outcome(outcomes[qid], scaling)
            if outcome_value is None:
                continue
            arms_cdf = continuous_arms(row)
            if "phase1" not in arms_cdf:
                continue
            entry["scores"] = {name: score_row(cdf, outcome_value, scaling)
                               for name, cdf in arms_cdf.items()}
            entry["outcome"] = outcome_value
        scored.append(entry)
    return scored


def report_family(label: str, scored: list[dict[str, Any]], unit: str) -> dict[str, Any]:
    print(f"\n{'=' * 78}\n=== {label} ===")
    print(f"scored rows: {len(scored)}")
    if not scored:
        return {"n": 0}
    modes = [row["supervisor_mode"] for row in scored if row["supervisor_mode"]]
    if modes:
        print(f"supervisor modes: {modes.count('research')} research, "
              f"{modes.count('reasoning')} reasoning "
              f"(the spread gate decided each one)")
    d21 = paired(scored, "phase2", "phase1")
    mean21, lo21, _ = report_pair("PHASE 2 - PHASE 1", d21, unit)
    # This family's own herding ratio: binary spreads are probability differences and
    # continuous spreads are medians in question units, so the two never share a number.
    ratio = herding_line(scored)
    if ratio is not None:
        print(f"  {'spread ratio (phase2/phase1)':<34} {ratio:.3f}   "
              f"(< {HERDING_RATIO} means this family's disagreement more than halved)")
    consumed_deltas = [
        row["scores"]["supervisor"] - row["scores"][row["consumed"]]
        for row in scored
        if "supervisor" in row["scores"] and row["consumed"] in row["scores"]
    ]
    mean_s, lo_s, _ = report_pair("SUPERVISOR - the phase it consumed",
                                  consumed_deltas, unit)
    report_pair("SUPERVISOR - PHASE 1", paired(scored, "supervisor", "phase1"), unit)
    return {
        "n": len(scored),
        "phase2_delta": mean21,
        "phase2_lo": lo21,
        "phase2_ratio": ratio,
        "phase2_n": len(d21),
        "supervisor_delta": mean_s,
        "supervisor_lo": lo_s,
        "supervisor_n": len(consumed_deltas),
        "scored": scored,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paired phase-1 / phase-2 / supervisor scorer.")
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--resolutions", type=Path, default=OVERLAY,
                        help="the resolutions overlay written by bench/sync_resolutions.py")
    args = parser.parse_args(argv)

    print(__doc__.split("Usage:")[0].rstrip())
    rows = load_rows(args.journal)
    outcomes = load_outcomes(args.resolutions)
    resolved = sum(1 for r in rows if r["source"]["question_id"] in outcomes)
    print(f"\njournal rows carrying pool_phase1: {len(rows)}  (resolved: {resolved})")

    binaries = [r for r in rows if r.get("question_type") == "binary"]
    continuous = [r for r in rows if r.get("question_type") in CONTINUOUS]
    mc = [r for r in rows if r.get("question_type") == "multiple_choice"]

    binary_scored = score_group(binaries, outcomes, "binary")
    continuous_scored = score_group(continuous, outcomes, "continuous")
    binary_out = report_family("BINARY (log score, nats — higher is better)",
                               binary_scored, "nats")
    continuous_out = report_family(
        "CONTINUOUS (platform points — higher is better)", continuous_scored, "pts")
    if mc:
        print(f"\n{len(mc)} multiple-choice row(s) carry the same fields and are NOT scored "
              f"here — the platform scores MC with a different formula (see the header).")

    # --- the herding check. The ratio is reported (and applied) PER FAMILY: a binary spread
    # is a difference of probabilities, a continuous spread a difference of medians in
    # question units, so one pooled mean of the two ratios is not a quantity. The Brier term
    # stays binaries-only — a proper score bounded on [0,1] keeps the "delta <= 0" clause
    # from being decided by a single confident miss.
    print(f"\n{'=' * 78}\n=== HERDING CHECK (phase 2) ===")
    brier_deltas = [row["brier"]["phase1"] - row["brier"]["phase2"]
                    for row in binary_scored
                    if "phase2" in row.get("brier", {}) and "phase1" in row.get("brier", {})]
    if binary_out.get("phase2_ratio") is None and continuous_out.get("phase2_ratio") is None:
        print("  no row has both spreads — phase 2 has not run on a resolved question")
    for label, out in (("binary", binary_out), ("continuous", continuous_out)):
        if out.get("phase2_ratio") is not None:
            print(f"  {label}: mean spread_phase2 / spread_phase1 = "
                  f"{out['phase2_ratio']:.3f}  "
                  f"(< {HERDING_RATIO} means that family's disagreement more than halved)")
    if brier_deltas:
        mean_b = st.mean(brier_deltas)
        lo_b, hi_b = boot_ci(brier_deltas) if len(brier_deltas) > 1 else (
            float("nan"), float("nan"))
        print(f"  paired Brier delta (phase1 - phase2, positive favors phase 2): "
              f"{mean_b:+.4f}  CI90 [{lo_b:+.4f}, {hi_b:+.4f}]  n={len(brier_deltas)}")
    else:
        print("  paired Brier delta: n=0")

    print(f"\n{'=' * 78}\n=== VERDICTS (rules fixed 2026-09-03) ===")
    phase2_n = binary_out.get("phase2_n", 0) + continuous_out.get("phase2_n", 0)
    phase2_means = [m for m in (binary_out.get("phase2_delta"),
                                continuous_out.get("phase2_delta"))
                    if m is not None and not math.isnan(m)]
    # Scores AND spreads are in different units per family, so the phase-2 clause is applied
    # per family — each with its own delta, its own CI90 lower bound and its own spread ratio
    # — and the joint verdict is the strictest of them.
    print("  phase 2:")
    for label, out in (("binary", binary_out), ("continuous", continuous_out)):
        if out.get("phase2_n"):
            print(f"    {label}: " + phase2_verdict(
                out["phase2_delta"], out["phase2_lo"], out["phase2_ratio"],
                out["phase2_n"],
            ))
    if not phase2_means or not phase2_n:
        print("    no phase-2 rows have resolved yet "
              "(share_evidence is off in production by operator decision)")
    print("  supervisor:")
    n_supervisor = binary_out.get("supervisor_n", 0) + continuous_out.get("supervisor_n", 0)
    for label, out in (("binary", binary_out), ("continuous", continuous_out)):
        if out.get("supervisor_n"):
            print(f"    {label}: " + supervisor_verdict(
                out["supervisor_delta"], out["supervisor_lo"], out["supervisor_n"],
                binary_out.get("supervisor_n", 0), n_supervisor,
            ))
    if not n_supervisor:
        print("    no supervisor rows have resolved yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
