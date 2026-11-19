#!/usr/bin/env python3
"""How much work does one research run do, and did that just change?

A model, a CLI version or a provider can change how hard an identical prompt is worked
without any line of this repository changing: on 2026-09-22 a model switch moved the
default effort down a level and every research run shrank to about a third (296 s -> 80 s,
9.7 sources -> 5.6) for two months, unnoticed, because nothing measured it.

This reads the per-call telemetry already in ``bot/journal/traces/`` and compares the
research runs of the last ``--days`` days with those of the ``--baseline-days`` before
them. It prints the median of each measure per window and per model, and exits 1 when any
measure moved by more than ``--factor`` in either direction. No network, no writes.

Usage:
    python bench/depth.py
    python bench/depth.py --as-of 2026-10-06     # replay: flags the September switch
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

TRACES = Path(__file__).resolve().parents[1] / "bot" / "journal" / "traces"

#: measure -> how to read it off one research call. None = not recorded on that call.
MEASURES: dict[str, Callable[[dict[str, Any]], float | None]] = {
    "seconds": lambda c: c.get("seconds"),
    "sources": lambda c: len(c.get("sources") or []),
    "dossier chars": lambda c: len(c.get("dossier") or ""),
    "turns": lambda c: (c.get("usage") or {}).get("num_turns"),
    "output tokens": lambda c: (c.get("usage") or {}).get("output"),
    "cost usd": lambda c: c.get("cost_usd"),
}


def research_calls(traces: Path) -> Iterable[tuple[datetime, dict[str, Any]]]:
    """(forecast time, call) for every successful research run in the trace directory."""
    for path in sorted(traces.glob("*.json")):
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(trace["forecast_at"])
        except (ValueError, KeyError, OSError):
            continue  # a truncated trace must not break the check
        for call in trace.get("calls") or []:
            if call.get("mode") == "research" and call.get("ok"):
                yield at, call


def medians(calls: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, read in MEASURES.items():
        values = [v for v in map(read, calls) if v is not None]
        if values:
            out[name] = statistics.median(values)
    return out


def drift(recent: dict[str, float], baseline: dict[str, float],
          factor: float) -> dict[str, float]:
    """measure -> recent/baseline ratio, for the measures that moved by more than factor."""
    moved: dict[str, float] = {}
    for name in recent.keys() & baseline.keys():
        if baseline[name] > 0:
            ratio = recent[name] / baseline[name]
            if ratio > factor or ratio < 1 / factor:
                moved[name] = ratio
    return moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--traces", type=Path, default=TRACES)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--baseline-days", type=int, default=28)
    parser.add_argument("--factor", type=float, default=1.5)
    parser.add_argument("--as-of", type=datetime.fromisoformat, default=None)
    args = parser.parse_args(argv)

    now = (args.as_of or datetime.now(UTC)).astimezone(UTC)
    split = now - timedelta(days=args.days)
    start = split - timedelta(days=args.baseline_days)
    windows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for at, call in research_calls(args.traces):
        if start <= at <= now:
            windows["recent" if at > split else "baseline"].append(call)
            if at > split:
                by_model[str(call.get("model") or "?")].append(call)

    recent, baseline = medians(windows["recent"]), medians(windows["baseline"])
    rows = [("baseline", windows["baseline"], baseline), ("recent", windows["recent"], recent),
            *((f"  {model}", calls, medians(calls)) for model, calls in sorted(by_model.items()))]
    print(f"research runs, medians; recent = {args.days} days to {now:%Y-%m-%d}")
    for label, calls, row in rows:
        cells = "  ".join(f"{name} {row[name]:,.{0 if row[name] >= 100 else 2}f}"
                          for name in MEASURES if name in row)
        print(f"{label:28s} n={len(calls):4d}  {cells}")
    if not windows["recent"] or not windows["baseline"]:
        print("not enough runs in both windows to compare")
        return 0
    moved = drift(recent, baseline, args.factor)
    for name, ratio in sorted(moved.items()):
        print(f"DRIFT {name}: x{ratio:.2f} against the baseline window")
    return 1 if moved else 0


if __name__ == "__main__":
    sys.exit(main())
