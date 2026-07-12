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

METHODOLOGY GUARDS (second-eyes review; strengthen the registration without changing any
threshold, statistic, or the paired-bootstrap math above):
- COVERAGE: the angles arm journals a row only when ALL its per-angle sub-runs succeed, so
  its attrition is structurally higher and plausibly difficulty-correlated. Per-arm run-0
  scorable coverage is printed against the three-arm union, naming each arm's missing qids.
- COMPARABLE SUMMARY: Brier/REL/RES are printed BOTH per arm on its OWN scorable set (NOT
  comparable across arms) and on the three-way common complete-case set (n_common). The
  PAIRED deltas stay on each pair's own common set -- that is the correct paired math,
  unchanged.
- DIFFERENTIAL-ATTRITION: if angles' run-0 coverage is more than 5% below high's AND high's
  mean Brier on the angle-missing qids is >= 0.02 worse than on the angle-complete qids, the
  readout is ATTRITION-COMPROMISED -- resume the missing angles cells before promoting.
- EXCLUSION PROVENANCE: each --exclude-qid must be admitted by evidence (a memory_screen
  run-0 regex candidate in this results file, or the standard ECB memory-leak exclusion);
  anything else is an error, so the manual exclusion lever cannot be used unaccountably.
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

MEMORY_LEAK = {"btf2:516f111d-d70e-5198-95dc-5d38c0d9d789"}  # known memory claim (ECB)
ARMS = ("plain", "high", "angles")
DEFAULT_SETS = ROOT / "bench/sets/btf2-loop1.jsonl"
DEFAULT_PROBE = ROOT / "bench/results/btf2-loop1-adm.probe.jsonl"
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


def validate_exclusions(
    exclude_qids: list[str],
    rows: list[ResultRow],
    stream: TextIO = sys.stdout,
) -> None:
    """Provenance gate for --exclude-qid. Every excluded qid must be admitted by evidence:
    a memory_screen run-0 regex candidate in THIS results file, or membership in the standard
    hardcoded memory-leak set (the ECB qid). Otherwise exit, naming the offending qid. Each
    accepted exclusion is printed with its admitting evidence -- no unaccountable manual
    exclusion lever."""
    if not exclude_qids:
        return
    # Lazy import from the sibling analysis directory (memory_screen has no import-time side
    # effects in v0.4.21); only pay it when an exclusion actually needs vetting.
    analysis_dir = str(ROOT / "bench" / "analysis")
    if analysis_dir not in sys.path:
        sys.path.insert(0, analysis_dir)
    import memory_screen  # noqa: E402
    _screened, candidates = memory_screen.find_candidates(rows, run=0)
    hits: dict[str, str] = {}  # qid -> memory_screen regex text admitting the exclusion
    for row, match in candidates:
        qid = row.get("qid")
        if qid and qid not in hits:
            hits[qid] = match.group(0)
    for qid in exclude_qids:
        if qid in hits:
            print(f"exclusion {qid} accepted: memory_screen prefilter match "
                  f"{hits[qid]!r} on a run-0 row", file=stream)
        elif qid in MEMORY_LEAK:
            print(f"exclusion {qid} accepted: standard exclusion list (ECB memory leak)",
                  file=stream)
        else:
            sys.exit(f"--exclude-qid {qid} rejected: not a memory_screen prefilter "
                     "candidate in the run-0 rows and not on the standard exclusion list")


def attrition_diagnostic(
    arm: dict[str, dict[str, float]],
    resolutions: dict[str, float],
    stream: TextIO = sys.stdout,
) -> None:
    """Differential-attrition guard. angles' missingness can be difficulty-correlated (a row
    exists only when every angle sub-run succeeded). Split high's Brier -- high completed the
    qid regardless -- by whether angles also completed it, then apply the pre-registered rule:
    coverage gap strictly > 5% AND missing-vs-complete Brier split >= 0.02 => compromised."""
    print("\n== DIFFERENTIAL-ATTRITION DIAGNOSTIC (high's Brier split by angles coverage) ==",
          file=stream)
    high, angles = set(arm["high"]), set(arm["angles"])
    if not high:
        print("high arm has no scorable rows; diagnostic not computable", file=stream)
        return
    complete = sorted(high & angles)
    missing = sorted(high - angles)
    b_complete = (st.mean(brier(arm["high"][q], resolutions[q]) for q in complete)
                  if complete else None)
    b_missing = (st.mean(brier(arm["high"][q], resolutions[q]) for q in missing)
                 if missing else None)
    gap = (len(high) - len(angles)) / len(high)
    print(f"high on angle-complete qids (n={len(complete)}): "
          + (f"{b_complete:.4f}" if b_complete is not None else "n/a"), file=stream)
    print(f"high on angle-missing  qids (n={len(missing)}): "
          + (f"{b_missing:.4f}" if b_missing is not None else "n/a"), file=stream)
    print(f"angles run-0 coverage {len(angles)} vs high {len(high)} ({gap:+.1%} below high)",
          file=stream)
    split = (b_missing - b_complete
             if (b_missing is not None and b_complete is not None) else None)
    if gap > 0.05 and split is not None and split >= 0.02:
        print("VERDICT: ATTRITION-COMPROMISED -- angles coverage is >5% below high's AND high "
              "scores >=0.02 worse Brier on the angle-missing qids; resume the missing angles "
              "cells to completion before applying the promote rule.", file=stream)
    else:
        print("VERDICT: attrition non-differential (rule: coverage gap >5% AND "
              f"missing-vs-complete Brier split >= 0.02; got gap {gap:+.1%}, split "
              + (f"{split:+.4f})" if split is not None else "n/a)"), file=stream)


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

    # COVERAGE: name each arm's missing run-0 qids against the three-arm union so
    # angles-only attrition cannot hide behind bare per-arm counts.
    union = sorted(set().union(*(set(arm[name]) for name in ARMS)))
    print(f"\n== COVERAGE (run-0 scorable qids per arm vs three-arm union, "
          f"n_union={len(union)}) ==", file=stream)
    for name in ARMS:
        missing = [q for q in union if q not in arm[name]]
        print(f"{name:7s} {len(arm[name])}/{len(union)}  missing: "
              + (", ".join(missing) if missing else "none"), file=stream)

    print("\n== SUMMARY on each arm's OWN scorable set (NOT comparable across arms) ==",
          file=stream)
    print(f"{'arm':7s} {'n':>3s} {'Brier':>7s} {'REL':>7s} {'RES':>7s}  vs teacher",
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

    # COMPARABLE SUMMARY: same stats on the three-way common complete-case set (identical
    # qids for every arm), so cross-arm Brier/REL/RES read on equal footing.
    common = sorted(set(arm["plain"]) & set(arm["high"]) & set(arm["angles"]))
    if common:
        print(f"\n== SUMMARY on the three-way common complete-case set "
              f"(comparable; n_common={len(common)}) ==", file=stream)
        print(f"{'arm':7s} {'n':>3s} {'Brier':>7s} {'REL':>7s} {'RES':>7s}  vs teacher",
              file=stream)
        qs_common = sorted(set(common) & set(teacher))
        for name in ARMS:
            probs = {q: arm[name][q] for q in common}
            bs = st.mean(brier(probs[q], resolutions[q]) for q in common)
            rel, resol = murphy(probs, resolutions)
            if qs_common:
                dt = st.mean(brier(probs[q], resolutions[q]) - brier(teacher[q], resolutions[q])
                             for q in qs_common)
                dt_s = f"{dt:+.4f}"
            else:
                dt_s = "    n/a"
            print(f"{name:7s} {len(common):3d} {bs:7.4f} {rel:7.4f} {resol:7.4f}  {dt_s}",
                  file=stream)
    else:
        print("\nno three-way common complete-case set (an arm has zero scorable overlap)",
              file=stream)

    attrition_diagnostic(arm, resolutions, stream)

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
    qrows = load(DEFAULT_SETS)
    resolutions = {q["id"]: float(q["resolution"])
                   for q in qrows if q.get("resolution") is not None}
    teacher = {q["id"]: float(q["crowd"]["value"]) for q in qrows
               if isinstance(q.get("crowd"), dict) and q["crowd"].get("value") is not None}
    probe = load(DEFAULT_PROBE)
    flagged = {row["qid"] for row in probe
               if "opus" in row.get("model", "") and cp.contaminated(row)}
    flagged |= MEMORY_LEAK

    rows = load(args.results)
    # Pass sys.stdout explicitly (resolved now, not at def time) so the readout is captured
    # correctly when main() is driven in-process under a redirected stdout.
    validate_exclusions(args.exclude_qid, rows, sys.stdout)  # provenance-gate before excluding
    flagged.update(args.exclude_qid)

    print_readout(rows, resolutions, teacher, flagged, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
