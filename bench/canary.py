#!/usr/bin/env python3
"""A candidate model against the incumbent, on the same live questions.

When a tier's ``run_models`` gives a candidate one of the research runs, every question it
forecasts carries an answer from each model, made at the same time from the same brief.
This scores each run against the resolution and reports the paired difference, candidate
minus incumbent, in leaderboard points per question:

  * binary and multiple choice: 100 * ln(probability given to what happened);
  * continuous: the platform's own formula on the CDF each run's percentiles build.

The field's term cancels in a difference between two of our own forecasts on one question,
so a difference here is a difference on the leaderboard. Accuracy is compared this way,
live, because a backtest flatters a newer model: its training data can hold the answer.

Usage:
    python bench/canary.py
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bench.analysis.minibench_numeric_tails import boot_ci, score_row  # noqa: E402
from bench.analysis.phase_pools import build, load_outcomes, numeric_outcome  # noqa: E402

JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
OVERLAY = ROOT / "bot" / "journal" / "resolutions.jsonl"
FLOOR = 0.001  # the platform's own floor on a probability


def run_models(trace: dict[str, Any]) -> list[str] | None:
    """The model of each successful research run, in run order; None unless run 1 is among
    them (run 1 is the incumbent: without it there is nothing to compare against)."""
    by_run: dict[int, str] = {}
    for call in trace.get("calls") or []:
        if call.get("stage") == "angle_research" and call.get("ok") and call.get("run_index"):
            by_run[int(call["run_index"])] = str(call.get("model") or "?")
    return [by_run[index] for index in sorted(by_run)] if 1 in by_run else None


def run_scores(row: dict[str, Any], outcome: Any) -> list[float] | None:
    """One score per pooled run of a journal row, in run order; None when unscorable."""
    kind = row.get("question_type")
    if kind == "binary" and isinstance(outcome, bool):
        return [100 * math.log(max(FLOOR, p if outcome else 1 - p))
                for p in row.get("raw_draws") or []]
    if kind == "multiple_choice" and isinstance(outcome, str):
        return [100 * math.log(max(FLOOR, float(probs.get(outcome) or 0)))
                for probs in row.get("run_probabilities") or []]
    scaling = row.get("scaling") or {}
    value = numeric_outcome(outcome, scaling) if scaling else None
    if value is None:
        return None
    runs = row.get("run_percentiles") or []
    escapes = row.get("run_escapes") or [[None, None]] * len(runs)
    cdfs = [build(pcts, scaling, *(list(pair or []) + [None, None])[:2])
            for pcts, pair in zip(runs, escapes, strict=False)]
    return None if any(cdf is None for cdf in cdfs) else [
        score_row(cdf, value, scaling) for cdf in cdfs if cdf is not None]


def paired_deltas(journal: Path, outcomes: dict[int, Any]) -> dict[str, list[float]]:
    """candidate model -> its per-question score minus the incumbent's (run 1's model)."""
    latest: dict[int, dict[str, Any]] = {}  # a refreshed question counts once: its last row
    for line in journal.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(ValueError, KeyError, TypeError):
            row = json.loads(line)
            if not row.get("dry_run"):
                latest[int(row["source"]["question_id"])] = row
    deltas: dict[str, list[float]] = defaultdict(list)
    for qid, row in latest.items():
        try:
            trace = json.loads((journal.parent / row["trace_path"]).read_text(encoding="utf-8"))
        except (ValueError, KeyError, TypeError, OSError):
            continue
        models = run_models(trace)
        if qid not in outcomes or not models or len(set(models)) < 2:
            continue
        scores = run_scores(row, outcomes[qid])
        if not scores or len(scores) != len(models):
            continue  # a failed run broke the alignment: leave the question out
        by_model: dict[str, list[float]] = defaultdict(list)
        for model, score in zip(models, scores, strict=True):
            by_model[model].append(score)
        incumbent = statistics.fmean(by_model.pop(models[0]))
        for model, own in by_model.items():
            deltas[model].append(statistics.fmean(own) - incumbent)
    return deltas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--overlay", type=Path, default=OVERLAY)
    args = parser.parse_args(argv)
    deltas = paired_deltas(args.journal, load_outcomes(args.overlay))
    if not deltas:
        print("no resolved question carries runs from two models yet")
        return 0
    for model, values in sorted(deltas.items()):
        mean = statistics.fmean(values)
        interval = "n too small"
        if len(values) > 1:
            low, high = boot_ci(values)
            interval = f"CI90 [{low:+.1f}, {high:+.1f}]"
        ahead = sum(v > 0 for v in values)
        print(f"{model}: {mean:+.1f} points per question against the incumbent, "
              f"n={len(values)}, {interval}, ahead on {ahead}/{len(values)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
