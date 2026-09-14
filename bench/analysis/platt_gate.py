"""Platt activation gate on the tournament record: fit EARLY, score LATE, ship only if it helps.

The recalibration layer (``fit_platt`` / ``apply_recalibration`` in core.py) has shipped inert
since v0.4.15 because ``fsj calibrate-fit`` only had the pastcast to fit on and its slope
(0.573, overconfident THERE) was declared non-portable. The roadmap's activation rule is a
TEMPORAL cross-validation on live tournament binaries: fit on the early resolved record,
score on the later one, activate only if out-of-sample Brier improves. This script is that
gate, reading the resolutions overlay written by bench/sync_resolutions.py.

Three checks are printed; ``--write`` emits bot/journal/recalibration.json (which
run_bot.py loads at submission time) only when ALL pass:
  1. temporal: fit on forecasts made before --cutoff, score on forecasts at/after it;
     out-of-sample Brier delta must be negative (recal helps) by more than --min-gain.
  2. interleaved 5-fold CV over the whole set (core.recalibration_cv) must also be negative.
  3. n_train >= RECAL_MIN_N and n_test >= 20.
The fitted parameters written are from the FULL set (both halves), as calibrate-fit does.

Usage:
    python bench/analysis/platt_gate.py --cutoff 2026-08-10
    python bench/analysis/platt_gate.py --cutoff 2026-08-10 --write
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bench"))

from forecast_scaffold.core import (  # noqa: E402
    RECAL_MIN_N,
    SCAFFOLD_VERSION,
    apply_recalibration,
    fit_platt,
    recalibration_cv,
)
from sync_resolutions import load_journal, load_overlay  # noqa: E402

JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
OVERLAY = ROOT / "bot" / "journal" / "resolutions.jsonl"
PARAMS = ROOT / "bot" / "journal" / "recalibration.json"
BAND = (0.01, 0.99)  # the bot's own submission band (run_bot.py clamps to it)


def resolved_binaries(journal: Path, overlay: Path) -> list[tuple[str, float, bool, float | None]]:
    """(forecast_at, probability, outcome, spot_peer) for every resolved binary."""
    rows = load_journal(journal)
    out = []
    for qid, res in load_overlay(overlay).items():
        if res.get("status") != "resolved" or res.get("annulled"):
            continue
        if res.get("question_type") != "binary" or not isinstance(res.get("outcome"), bool):
            continue
        row = rows.get(qid)
        if row is None or not isinstance(row.get("probability"), (int, float)):
            continue
        out.append((str(row.get("forecast_at")), float(row["probability"]), bool(res["outcome"]),
                    res.get("spot_peer_score")))
    out.sort()
    return out


def brier(pairs: list[tuple[float, bool]]) -> float:
    return st.mean((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--overlay", default=str(OVERLAY))
    ap.add_argument("--cutoff", required=True, help="ISO date: fit before, score at/after")
    ap.add_argument("--min-gain", type=float, default=0.002,
                    help="required out-of-sample Brier improvement (default 0.002)")
    ap.add_argument("--write", action="store_true", help="write recalibration.json if all pass")
    args = ap.parse_args()

    data = resolved_binaries(Path(args.journal), Path(args.overlay))
    train = [(p, y) for t, p, y, _ in data if t[:10] < args.cutoff]
    test = [(p, y) for t, p, y, _ in data if t[:10] >= args.cutoff]
    print(f"resolved binaries: {len(data)}  train (< {args.cutoff}): {len(train)}  "
          f"test (>= {args.cutoff}): {len(test)}")
    if not train or not test:
        print("nothing to gate")
        return 1

    a, b = fit_platt(train, min_n=1)
    raw = brier(test)
    recal = brier([(apply_recalibration(p, a, b, BAND), y) for p, y in test])
    temporal_delta = recal - raw
    print(f"temporal fit on train: a={a:.3f} b={b:+.3f}  "
          f"({'shrink toward 0.5' if a < 1 else 'stretch'}; b>0 lifts toward YES)")
    print(f"temporal test Brier: raw {raw:.4f} -> recal {recal:.4f}  delta {temporal_delta:+.4f} "
          f"(negative = helps)")
    # what the fitted map does at a few probabilities
    print("  map:", "  ".join(f"{p:.2f}->{apply_recalibration(p, a, b, BAND):.2f}"
                              for p in (0.03, 0.05, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.95)))

    cv = recalibration_cv([(p, y) for _, p, y, _ in data])
    print(f"5-fold CV on all {len(data)}: raw {cv['raw_brier']:.4f} -> recal {cv['recal_brier']:.4f} "
          f"delta {cv['delta']:+.4f}  mean slope {cv['mean_slope']:.3f}")

    a_all, b_all = fit_platt([(p, y) for _, p, y, _ in data])
    print(f"full-set fit: a={a_all:.3f} b={b_all:+.3f}  (n={len(data)}, RECAL_MIN_N={RECAL_MIN_N})")

    checks = {
        "temporal helps": temporal_delta < -args.min_gain,
        "cv helps": cv["delta"] < 0,
        "enough data": len(train) >= RECAL_MIN_N and len(test) >= 20,
    }
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    verdict = all(checks.values())
    print("VERDICT:", "ACTIVATE" if verdict else "STAY INERT")
    if verdict and args.write:
        params = {
            "a": a_all, "b": b_all, "n": len(data),
            "cv_delta": cv["delta"], "temporal_delta": temporal_delta,
            "cutoff": args.cutoff, "fit_at": datetime.now(tz=UTC).isoformat(),
            "scaffold_version": SCAFFOLD_VERSION, "source": "bench/analysis/platt_gate.py",
        }
        PARAMS.write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {PARAMS}")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
