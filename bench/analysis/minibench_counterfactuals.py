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

Resolutions are supplied via --resolutions FILE.json: {"<qid>": 0|1|value, ...} in
display units for numerics. Rows without an entry are skipped (reported).
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
MB_WINDOW = ("2026-07-17", "2026-08-05")  # resolve_by window that identifies the wave


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


def load_wave(journal: Path) -> tuple[list[dict], list[dict]]:
    binaries, numerics = [], []
    seen: set[int] = set()
    rows = [json.loads(line) for line in journal.open(encoding="utf-8") if line.strip()]
    rows.sort(key=lambda r: str(r.get("forecast_at")), reverse=True)  # latest wins
    for row in rows:
        resolve_by = str(row.get("resolve_by") or "")
        if not (MB_WINDOW[0] <= resolve_by <= MB_WINDOW[1]):
            continue
        qid = (row.get("source") or {}).get("question_id")
        if qid is None or qid in seen:
            continue
        seen.add(qid)
        if row.get("question_type") == "binary" and row.get("probability") is not None:
            binaries.append(row)
        elif row.get("percentiles"):
            pcts = row["percentiles"]
            if all(str(q) in pcts for q in QUANTILES):
                numerics.append(row)
    return binaries, numerics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--resolutions", type=Path, default=None,
                        help="JSON {qid: outcome}; omit to just list the frozen wave")
    args = parser.parse_args(argv)

    binaries, numerics = load_wave(args.journal)
    print(f"frozen wave: {len(binaries)} binaries, {len(numerics)} numerics "
          f"(resolve_by in {MB_WINDOW[0]}..{MB_WINDOW[1]})")

    if args.resolutions is None:
        for row in binaries:
            qid = row["source"]["question_id"]
            print(f"  bin qid {qid} p={row['probability']:.4f} {row['question'][:60]!r}")
        for row in numerics:
            qid = row["source"]["question_id"]
            print(f"  num qid {qid} p50={row['percentiles']['50']} {row['question'][:60]!r}")
        print("\nno --resolutions file: nothing scored (preregistration listing only)")
        return 0

    resolutions = {int(k): v for k, v in
                   json.loads(args.resolutions.read_text(encoding="utf-8")).items()}

    tags_path = Path(__file__).with_name("minibench-2026-07-tags.json")
    tags: dict[int, str] = {}
    if tags_path.exists():
        raw = json.loads(tags_path.read_text(encoding="utf-8"))
        tags = {int(k): str(v) for k, v in (raw.get("tags") or {}).items()}

    scored_b = [r for r in binaries if r["source"]["question_id"] in resolutions]
    print(f"\nbinaries resolved: {len(scored_b)}/{len(binaries)}")
    groups = ["all"] + sorted({tags.get(r["source"]["question_id"], "untagged")
                               for r in scored_b})
    for group in groups:
        rows_g = (scored_b if group == "all" else
                  [r for r in scored_b
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

    scored_n = [r for r in numerics if r["source"]["question_id"] in resolutions]
    print(f"\nnumerics resolved: {len(scored_n)}/{len(numerics)}")
    per_w: dict[float, list[float]] = {w: [] for w in WIDENS}
    cover: dict[float, int] = {w: 0 for w in WIDENS}
    for row in scored_n:
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
    if len(scored_n) >= 5:
        deltas = [x - y for x, y in zip(per_w[1.6], per_w[1.0], strict=True)]
        lo, hi = boot_ci(deltas)
        print(f"  PRIMARY w=1.6 vs 1.0: mean delta {st.mean(deltas):+.4f} "
              f"CI90 [{lo:+.4f},{hi:+.4f}]  (negative favors widening)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
