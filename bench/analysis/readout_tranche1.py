"""Pre-registered readout of the 3-arm research A/B ("tranche1"): plain ReAct vs
our-method (high) vs angle-diverse (angles), opus-4-6, corpus+vault research,
bench/sets/btf2-loop1-adm.jsonl.

PRE-REGISTERED RULES (docs/roadmap-v05.md, set BEFORE looking at results):
- Primary statistic: paired bootstrap CI on per-question Brier deltas (tail-sensitive);
  win-rate is secondary. n=40 resolves only effects >= ~0.015 -- do NOT adjudicate
  0.005-sized differences from this tranche.
- Promote plain or angles over high if paired delta <= -0.008 with 90% bootstrap CI
  excluding 0; if |delta| < 0.005 treat as unresolved and run the substrate recall audit
  before any further research-mechanics interpretation.
- Binding critic amendment: complete the substrate recall audit (or explicitly report why
  the public artifacts make the literal audit impossible) BEFORE interpreting any branch,
  not only the near-null branch. The audit is diagnostic, not a hard gate at n~20.
- Run ``memory_screen.py RESULTS --run 0`` FIRST and pass each confirmed hit here as a
  repeatable ``--exclude-qid QID``. Exclusions apply to all arms pairwise.

Only run 0 belongs to this pre-registered experiment. Extra nonzero runs remain preserved
as paid raw data, but this readout counts and ignores them completely.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics as st
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
import contamination_probe as cp  # noqa: E402

MEMORY_LEAK = {"btf2:516f111d-d70e-5198-95dc-5d38c0d9d789"}  # known memory claim
ARMS = ("plain", "high", "angles")
DEFAULT_RESULTS = ROOT / "bench/results/btf2-loop1-adm.tranche1.results.jsonl"

ResultRow = dict[str, Any]


def load(path: Path) -> list[ResultRow]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def run_index(row: ResultRow) -> int:
    """Return the recorded run, treating a legacy missing/null run as run zero."""
    raw = row.get("run")
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid run value {raw!r} for qid {row.get('qid')!r}") from exc


def collect_run_zero(
    rows: Iterable[ResultRow],
    resolutions: dict[str, float],
    excluded_qids: set[str],
) -> tuple[dict[str, dict[str, float]], dict[str, float], Counter[str]]:
    """Build scorable run-0 arm cells and count every ignored nonzero row.

    Duplicate ``(tier, qid, run=0)`` cells raise before any value can be silently
    overwritten. Nonzero rows are counted by tier and otherwise left untouched.
    """
    arm = {name: {} for name in ARMS}
    cost = {name: 0.0 for name in ARMS}
    ignored_nonzero: Counter[str] = Counter()
    seen_run_zero: set[tuple[str, Any]] = set()

    for row in rows:
        tier = row.get("tier")
        run = run_index(row)
        if run != 0:
            ignored_nonzero[str(tier) if tier is not None else "<missing tier>"] += 1
            continue
        if tier not in arm:
            continue

        qid = row.get("qid")
        key = (tier, qid)
        if key in seen_run_zero:
            raise ValueError(f"duplicate run-0 row for tier={tier!r}, qid={qid!r}")
        seen_run_zero.add(key)

        if row.get("probability") is not None and qid not in excluded_qids and qid in resolutions:
            arm[tier][qid] = float(row["probability"])
            cost[tier] += row.get("cost_usd") or 0

    return arm, cost, ignored_nonzero


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def boot_ci(ds: list[float], iters: int = 10000, lo: int = 5, hi: int = 95) -> tuple[float, float]:
    rnd = random.Random(7)
    n = len(ds)
    means = sorted(st.mean(rnd.choices(ds, k=n)) for _ in range(iters))
    return means[int(iters * lo / 100)], means[int(iters * hi / 100)]


def murphy(probs: dict[str, float], resolutions: dict[str, float]) -> tuple[float, float]:
    pairs = [(p, resolutions[q]) for q, p in probs.items()]
    n = len(pairs)
    ybar = st.mean(y for _, y in pairs)
    bins: dict[int, list[tuple[float, float]]] = {}
    for p, y in pairs:
        bins.setdefault(min(int(p * 10), 9), []).append((p, y))
    rel = sum(len(bucket) * (st.mean(p for p, _ in bucket) - st.mean(y for _, y in bucket)) ** 2
              for bucket in bins.values()) / n
    resol = sum(len(bucket) * (st.mean(y for _, y in bucket) - ybar) ** 2
                for bucket in bins.values()) / n
    return rel, resol


def ignored_summary(counts: Counter[str]) -> str:
    total = sum(counts.values())
    detail = ", ".join(f"{tier}={count}" for tier, count in sorted(counts.items()))
    suffix = f" ({detail})" if detail else ""
    return f"Ignored {total} nonzero-run row(s){suffix}; readout uses run==0 only."


def print_readout(
    rows: Iterable[ResultRow],
    resolutions: dict[str, float],
    teacher: dict[str, float],
    excluded_qids: set[str],
    stream: TextIO = sys.stdout,
) -> None:
    """Compute and print the pre-registered readout from already-loaded data."""
    arm, cost, ignored_nonzero = collect_run_zero(rows, resolutions, excluded_qids)
    print(ignored_summary(ignored_nonzero), file=stream)
    for name in ARMS:
        print(f"{name:7s} {len(arm[name])} scorable rows, ${cost[name]:.2f}", file=stream)

    print(f"\n{'arm':7s} {'n':>3s} {'Brier':>7s} {'REL':>7s} {'RES':>7s}  vs teacher",
          file=stream)
    for name in ARMS:
        qs = sorted(set(arm[name]) & set(teacher))
        if not qs:
            continue
        bs = st.mean(brier(arm[name][q], resolutions[q]) for q in arm[name])
        rel, resol = murphy(arm[name], resolutions)
        dt = st.mean(brier(arm[name][q], resolutions[q]) - brier(teacher[q], resolutions[q])
                     for q in qs)
        print(f"{name:7s} {len(arm[name]):3d} {bs:7.4f} {rel:7.4f} {resol:7.4f}  {dt:+.4f}",
              file=stream)

    print("\nPAIRED (negative = first arm better; bootstrap 90% CI primary)", file=stream)
    for first, second in itertools.combinations(ARMS, 2):
        common = sorted(set(arm[first]) & set(arm[second]))
        if len(common) < 5:
            print(f"{first} vs {second}: insufficient overlap ({len(common)})", file=stream)
            continue
        ds = [brier(arm[first][q], resolutions[q]) - brier(arm[second][q], resolutions[q])
              for q in common]
        lo, hi = boot_ci(ds)
        wins = sum(1 for delta in ds if delta < 0)
        print(f"{first:7s} vs {second:7s}: mean {st.mean(ds):+.4f}  "
              f"CI90 [{lo:+.4f},{hi:+.4f}]  "
              f"(n={len(common)}, {first} wins {wins}/{len(ds)})", file=stream)
    print("\nReminder: screen run 0 first and pass confirmed hits via --exclude-qid.",
          file=stream)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exclude-qid", action="append", default=[], metavar="QID",
                        help="confirmed memory hit to exclude pairwise (repeatable)")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS,
                        help="tranche result JSONL (default: the registered tranche1 file)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    qrows = load(ROOT / "bench/sets/btf2-loop1.jsonl")
    resolutions = {q["id"]: float(q["resolution"])
                   for q in qrows if q.get("resolution") is not None}
    teacher = {q["id"]: float(q["crowd"]["value"]) for q in qrows
               if isinstance(q.get("crowd"), dict) and q["crowd"].get("value") is not None}
    probe = load(ROOT / "bench/results/btf2-loop1-adm.probe.jsonl")
    flagged = {row["qid"] for row in probe
               if "opus" in row.get("model", "") and cp.contaminated(row)}
    flagged |= MEMORY_LEAK
    flagged.update(args.exclude_qid)

    print_readout(load(args.results), resolutions, teacher, flagged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
