"""Preregistered counterfactual scoring of the 2026-07 MiniBench wave.

REGISTERED 2026-07-16, BEFORE ANY RESOLUTION EXISTS (the wave resolves Jul 23-25).
The diagnostic signatures that motivated this (21 binaries: 18/21 further from 50% than
the ~125-bot crowd, mean me-crowd = -8.1pp; 12 numerics: 11/12 narrower, median width
ratio 0.61) are crowd-RELATIVE and cannot distinguish "we are overconfident" from "the
crowd is underconfident". Outcomes can. This script freezes the transforms now and
scores them only when resolutions exist.

PRE-REGISTERED TRANSFORMS
- Binary: logit shrink toward 0.5, p' = sigmoid(a * logit(p)), for a in
  {0.5, 0.573, 0.7, 0.85, 1.0}. a=1.0 is the identity (what we actually submitted);
  a=0.573 is the pastcast Platt slope from v0.4.19 (measured overconfident THERE,
  direction previously judged non-portable — this is its live out-of-sample test).
- Numeric: widen percentiles around the median, q' = median + w * (q - median), for
  w in {1.0, 1.3, 1.6, 2.0}. w=1.6 ~ 1/0.61, the inverse of the observed width ratio.

PRE-REGISTERED SCORING (run with --score once resolutions are entered)
- Binary: mean Brier per transform; primary comparison a=0.573 vs a=1.0, paired
  bootstrap 90% CI on per-question deltas (10k draws, seed 7).
- Numeric: mean pinball (quantile) loss over the journaled percentiles {10,25,50,75,90}
  per transform — proper for quantile forecasts; plus 50% central-interval coverage
  (target 0.5). Primary comparison w=1.6 vs w=1.0, same paired bootstrap.
- DECISION RULE: a transform is promoted to a production experiment only if its CI90
  excludes zero in its favor. With n~36 binaries and n~20 numerics one wave is
  underpowered for small effects; a CI straddling zero -> keep collecting waves, change
  nothing. No transform is fitted on this wave's outcomes (values above are frozen).
- Crowd reference: where the close-time crowd aggregate is known (tmp/mb_pairs.json,
  extended as more values are revealed), its score is reported alongside as context,
  not as a decision input.

SUBGROUP HYPOTHESIS (registered 2026-07-16, tags frozen outcome-blind from journal
reasoning text in ``minibench-2026-07-tags.json``: schedule/momentum/other): the live
diagnosis found our biggest crowd-gaps on schedule-backed questions were mostly OUR
wins (the crowd herds; our docket/calendar research is the edge) while the confirmed
misses were extrapolation-driven. Therefore: logit shrink should HURT the 'schedule'
group and HELP the 'other' group; 'momentum' is where institutional overdiscount lives
and is predicted to gain from shrink toward 0.5 on the LOW side specifically. Scored
per-tag alongside the global readout; same decision rule per group.

POOLING ACROSS WAVES (added 2026-08-09): a "wave" is a resolve_by window on the journal.
Both --resolutions and --window are repeatable; with no --window the two known waves are
used (2026-07-17..2026-08-05 and 2026-08-06..2026-08-09). Every block is printed PER WAVE
and then POOLED over all waves. Later --resolutions files win on a qid collision (warned).
The frozen subgroup tags (``minibench-2026-07-tags.json``) are wave-1 only; every other
row falls in "untagged", so a later wave's subgroup split is simply not reported.

Resolutions are supplied via --resolutions FILE.json: {"<qid>": 0|1|value, ...} in
display units for numerics. Rows without an entry are skipped (reported); non-numeric
entries (multiple-choice option labels) are skipped too, since neither the binary nor the
numeric scorer can consume them.

Usage:
    python bench/analysis/minibench_counterfactuals.py \
        --resolutions bench/analysis/minibench-2026-07-resolutions.json \
        --resolutions bench/analysis/minibench-2026-07-27-resolutions.json
    # one wave only:
    python bench/analysis/minibench_counterfactuals.py \
        --window 2026-08-06 2026-08-09 \
        --resolutions bench/analysis/minibench-2026-07-27-resolutions.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"

SHRINKS = (0.5, 0.573, 0.7, 0.85, 1.0)
WIDENS = (1.0, 1.3, 1.6, 2.0)
QUANTILES = (10, 25, 50, 75, 90)

# resolve_by windows that identify the waves, in order. Wave 1 is the 2026-07-13 batch,
# wave 2 the 2026-07-27 batch. Override/extend with repeated --window START END.
DEFAULT_WINDOWS: tuple[tuple[str, str], ...] = (
    ("2026-07-17", "2026-08-05"),
    ("2026-08-06", "2026-08-09"),
)
MB_WINDOW = DEFAULT_WINDOWS[0]  # DEPRECATED alias for wave 1; kept for external importers

WAVE_KEY = "_wave"  # index of the window a loaded row belongs to

# Binary calibration bands, [lo, hi) with the top band closed at 1.0. The 10-25% band is
# the standing monitor preregistered in docs/minibench-analysis-2026-07-16.md.
BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 0.10), (0.10, 0.25), (0.25, 0.50), (0.50, 0.75), (0.75, 1.0),
)
MONITOR_BAND = (0.10, 0.25)


def logit(p: float) -> float:
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def shrink(p: float, a: float) -> float:
    return sigmoid(a * logit(min(max(p, 1e-6), 1 - 1e-6)))


def widen(pcts: dict[str, float], w: float) -> dict[str, float]:
    med = pcts["50"]
    return {k: med + w * (v - med) for k, v in pcts.items()}


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def pinball(pcts: dict[str, float], y: float) -> float:
    """Mean quantile (pinball) loss across the journaled percentiles."""
    losses = []
    for q in QUANTILES:
        tau, v = q / 100.0, pcts[str(q)]
        losses.append((tau * (y - v)) if y >= v else ((1 - tau) * (v - y)))
    return st.mean(losses)


def boot_ci(deltas: list[float], iters: int = 10000) -> tuple[float, float]:
    rnd = random.Random(7)
    n = len(deltas)
    means = sorted(st.mean(rnd.choices(deltas, k=n)) for _ in range(iters))
    return means[int(iters * 0.05)], means[int(iters * 0.95)]


def parse_windows(pairs: list[list[str]] | None) -> tuple[tuple[str, str], ...]:
    """Normalise repeated ``--window START END`` into the wave list (default: both waves)."""
    if not pairs:
        return DEFAULT_WINDOWS
    windows = []
    for start, end in pairs:
        if str(start) > str(end):
            raise SystemExit(f"--window START must not exceed END: {start} {end}")
        windows.append((str(start), str(end)))
    return tuple(windows)


def window_index(resolve_by: str, windows: tuple[tuple[str, str], ...]) -> int | None:
    """Index of the first window containing ``resolve_by``, or None if it is in no wave."""
    for i, (start, end) in enumerate(windows):
        if start <= resolve_by <= end:
            return i
    return None


def load_resolutions(paths: list[Path]) -> dict[int, float]:
    """Merge repeated --resolutions files. Later files win on collision (warned).

    Non-numeric outcomes (multiple-choice option labels) are dropped: neither the binary
    nor the numeric scorer can consume them, and the corresponding journal rows are then
    simply reported as unresolved.
    """
    merged: dict[int, float] = {}
    origin: dict[int, str] = {}
    skipped: list[tuple[int, str]] = []
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, value in raw.items():
            qid = int(key)
            try:
                val = float(value)
            except (TypeError, ValueError):
                skipped.append((qid, path.name))
                continue
            if qid in merged:
                print(f"warning: qid {qid} resolution collision: {merged[qid]!r} "
                      f"({origin[qid]}) -> {val!r} ({path.name}); later file wins")
            merged[qid] = val
            origin[qid] = path.name
    if skipped:
        print(f"skipped {len(skipped)} non-numeric resolution(s) "
              f"(multiple-choice labels): {[q for q, _ in skipped]}")
    return merged


def load_wave(
    journal: Path, windows: tuple[tuple[str, str], ...] | None = None
) -> tuple[list[dict], list[dict]]:
    """Load the journal rows belonging to any of ``windows`` (default: both known waves).

    A row belongs to the wave whose resolve_by window contains it; each returned row is
    tagged with its window index under ``WAVE_KEY``. Latest forecast per qid wins.
    """
    windows = windows or DEFAULT_WINDOWS
    binaries, numerics = [], []
    seen: set[int] = set()
    rows = [json.loads(line) for line in journal.open(encoding="utf-8") if line.strip()]
    rows.sort(key=lambda r: str(r.get("forecast_at")), reverse=True)  # latest wins
    for row in rows:
        wave = window_index(str(row.get("resolve_by") or ""), windows)
        if wave is None:
            continue
        qid = (row.get("source") or {}).get("question_id")
        if qid is None or qid in seen:
            continue
        seen.add(qid)
        row[WAVE_KEY] = wave
        if row.get("question_type") == "binary" and row.get("probability") is not None:
            binaries.append(row)
        elif row.get("percentiles"):
            pcts = row["percentiles"]
            if all(str(q) in pcts for q in QUANTILES):
                numerics.append(row)
    return binaries, numerics


def wave_labels(windows: tuple[tuple[str, str], ...]) -> list[str]:
    """Long per-wave headings, e.g. ``WAVE 1 (resolve_by 2026-07-17..2026-08-05)``."""
    return [f"WAVE {i + 1} (resolve_by {start}..{end})"
            for i, (start, end) in enumerate(windows)]


def wave_names(windows: tuple[tuple[str, str], ...]) -> list[str]:
    """Short per-wave labels for inline block tags, e.g. ``wave 1``."""
    return [f"wave {i + 1}" for i in range(len(windows))]


def band_of(p: float) -> tuple[float, float] | None:
    for lo, hi in BANDS:
        if lo <= p < hi or (hi == 1.0 and p == 1.0):
            return (lo, hi)
    return None


def band_table(rows: list[dict], resolutions: dict[int, float]) -> None:
    """Realized YES rate per forecast band — the standing 10-25% monitor lives here."""
    print(f"  calibration bands   {'n':>4} {'mean p':>8} {'YES':>7} {'realized':>9}")
    for lo, hi in BANDS:
        pairs = [(float(r["probability"]), resolutions[r["source"]["question_id"]])
                 for r in rows if band_of(float(r["probability"])) == (lo, hi)]
        label = f"{lo:.0%}-{hi:.0%}".replace("%-", "-")
        flag = "  <- standing monitor" if (lo, hi) == MONITOR_BAND else ""
        if not pairs:
            print(f"    {label:>14}   {0:>4}        -       -         -{flag}")
            continue
        yes = sum(y for _, y in pairs)
        print(f"    {label:>14}   {len(pairs):>4} {st.mean(p for p, _ in pairs):>8.3f} "
              f"{f'{yes:.0f}/{len(pairs)}':>7} {yes / len(pairs):>9.0%}{flag}")


def score_binaries(rows: list[dict], resolutions: dict[int, float],
                   tags: dict[int, str], label: str) -> None:
    scored = [r for r in rows if r["source"]["question_id"] in resolutions]
    print(f"\nbinaries resolved [{label}]: {len(scored)}/{len(rows)}")
    if not scored:
        return
    subgroups = sorted({tags.get(r["source"]["question_id"], "untagged") for r in scored})
    groups = ["all"] if subgroups == ["untagged"] else ["all", *subgroups]
    for group in groups:
        rows_g = (scored if group == "all" else
                  [r for r in scored
                   if tags.get(r["source"]["question_id"], "untagged") == group])
        if not rows_g:
            continue
        per_a: dict[float, list[float]] = {a: [] for a in SHRINKS}
        for row in rows_g:
            y = float(resolutions[row["source"]["question_id"]])
            for a in SHRINKS:
                per_a[a].append(brier(shrink(float(row["probability"]), a), y))
        line = "  ".join(f"a={a}: {st.mean(per_a[a]):.4f}" for a in SHRINKS)
        print(f"  [{group}] n={len(rows_g)}  {line}")
        if len(rows_g) >= 5:
            deltas = [x - y for x, y in zip(per_a[0.573], per_a[1.0], strict=True)]
            lo, hi = boot_ci(deltas)
            print(f"    a=0.573 vs 1.0: mean delta {st.mean(deltas):+.4f} "
                  f"CI90 [{lo:+.4f},{hi:+.4f}]  (negative favors shrink)")
    band_table(scored, resolutions)


def score_numerics(rows: list[dict], resolutions: dict[int, float], label: str) -> None:
    scored = [r for r in rows if r["source"]["question_id"] in resolutions]
    print(f"\nnumerics resolved [{label}]: {len(scored)}/{len(rows)}")
    if not scored:
        return
    per_w: dict[float, list[float]] = {w: [] for w in WIDENS}
    cover: dict[float, int] = {w: 0 for w in WIDENS}
    for row in scored:
        y = float(resolutions[row["source"]["question_id"]])
        base = {str(q): float(row["percentiles"][str(q)]) for q in QUANTILES}
        for w in WIDENS:
            pcts = widen(base, w)
            per_w[w].append(pinball(pcts, y))
            if pcts["25"] <= y <= pcts["75"]:
                cover[w] += 1
    for w in WIDENS:
        if per_w[w]:
            n = len(per_w[w])
            tag = " (submitted)" if w == 1.0 else ""
            print(f"  w={w:<4} mean pinball {st.mean(per_w[w]):.4f}  "
                  f"50%CI coverage {cover[w]}/{n}{tag}")
    if len(scored) >= 5:
        deltas = [x - y for x, y in zip(per_w[1.6], per_w[1.0], strict=True)]
        lo, hi = boot_ci(deltas)
        print(f"  PRIMARY w=1.6 vs 1.0: mean delta {st.mean(deltas):+.4f} "
              f"CI90 [{lo:+.4f},{hi:+.4f}]  (negative favors widening)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--resolutions", type=Path, action="append", default=None,
                        help="JSON {qid: outcome}; repeatable, later files win on "
                             "collision; omit to just list the frozen waves")
    parser.add_argument("--window", nargs=2, metavar=("START", "END"), action="append",
                        default=None,
                        help="resolve_by window identifying one wave; repeatable. "
                             f"Default: {' and '.join(f'{a}..{b}' for a, b in DEFAULT_WINDOWS)}")
    args = parser.parse_args(argv)

    windows = parse_windows(args.window)
    labels, names = wave_labels(windows), wave_names(windows)
    binaries, numerics = load_wave(args.journal, windows)
    for i, label in enumerate(labels):
        nb = sum(1 for r in binaries if r[WAVE_KEY] == i)
        nn = sum(1 for r in numerics if r[WAVE_KEY] == i)
        print(f"frozen {label}: {nb} binaries, {nn} numerics")
    if len(windows) > 1:
        print(f"frozen POOLED (all waves): {len(binaries)} binaries, "
              f"{len(numerics)} numerics")

    if not args.resolutions:
        for i, label in enumerate(labels):
            print(f"\n=== {label} ===")
            for row in (r for r in binaries if r[WAVE_KEY] == i):
                qid = row["source"]["question_id"]
                print(f"  bin qid {qid} p={row['probability']:.4f} "
                      f"{row['question'][:60]!r}")
            for row in (r for r in numerics if r[WAVE_KEY] == i):
                qid = row["source"]["question_id"]
                print(f"  num qid {qid} p50={row['percentiles']['50']} "
                      f"{row['question'][:60]!r}")
        print("\nno --resolutions file: nothing scored (preregistration listing only)")
        return 0

    resolutions = load_resolutions(args.resolutions)

    tags_path = Path(__file__).with_name("minibench-2026-07-tags.json")
    tags: dict[int, str] = {}
    if tags_path.exists():
        raw = json.loads(tags_path.read_text(encoding="utf-8"))
        tags = {int(k): str(v) for k, v in (raw.get("tags") or {}).items()}
    # the frozen tags describe wave-1 qids only; anything else scores as "untagged"
    wave1_qids = {r["source"]["question_id"] for r in binaries + numerics
                  if r[WAVE_KEY] == 0}
    tags = {q: t for q, t in tags.items() if q in wave1_qids}

    for i, label in enumerate(labels):
        print(f"\n{'=' * 78}\n=== {label} ===")
        score_binaries([r for r in binaries if r[WAVE_KEY] == i], resolutions, tags,
                       names[i])
        score_numerics([r for r in numerics if r[WAVE_KEY] == i], resolutions, names[i])

    if len(windows) > 1:
        print(f"\n{'=' * 78}\n=== POOLED (all {len(windows)} waves) ===")
        score_binaries(binaries, resolutions, tags, "pooled")
        score_numerics(numerics, resolutions, "pooled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
