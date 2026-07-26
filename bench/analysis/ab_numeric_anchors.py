"""A/B the numeric elicitation contract: five percentiles vs seven (added p3/p97).

WHAT THIS TESTS. The 2026-07 MiniBench readout found our numeric interval WIDTH is
calibrated (25-75 coverage 52%, 10-90 coverage 76%) but the distributions are biased and
the losses are all beyond p90 — and that the harness gives the model no way to say where
tail mass sits, because ``percentiles_to_cdf`` fills everything past the outermost
declared percentile by linear interpolation to the edge of the question's range. This
A/B changes only the ELICITATION CONTRACT and measures whether the model, given room to
place tail anchors and told what it is scored on, produces better-scoring distributions.

DESIGN (paired, blind, contamination-free by construction):
- Questions: the resolved numerics from the 2026-07 MiniBench wave.
- Context held FIXED across arms: the question, its resolution criterion, its bounds, and
  the model's OWN journaled reasoning/reference_class/base_rate from the live run. Both
  arms therefore know exactly the same things; the only difference is what they are asked
  to produce. Anchoring on the journaled reasoning pushes BOTH arms toward the original
  numbers, which makes any measured treatment effect conservative, not inflated.
- No tools. The events post-date the model's training cutoff and the agent cannot search,
  so neither arm can look up the answer. Each run additionally declares a memory screen
  (``recall_outcome``); any row claiming knowledge of the outcome is reported and
  excluded pairwise, per the repository's pastcast validity ritual.
- Arms differ ONLY in the output contract and the paragraph of elicitation guidance:
    five  = production today: 10/25/50/75/90, "widen the tails beyond what feels right"
    seven = 3/10/25/50/75/90/97, the scoring rule stated, and a required named
            mechanism for each tail before the numbers are written
- Scored by ``minibench_numeric_tails.py``: log density of the constructed CDF at the
  outcome, plus PIT calibration and the median-bias sign test.

Usage:
    python bench/analysis/ab_numeric_anchors.py --resolutions FILE.json --out RESULTS.jsonl
    python bench/analysis/ab_numeric_anchors.py --out RESULTS.jsonl --score-only
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bench.analysis.minibench_counterfactuals import JOURNAL, load_wave  # noqa: E402
from bot.run_bot import extract_json, run_agent  # noqa: E402

DEFAULT_AGENT = (
    'claude -p --model claude-opus-5 --output-format json '
    '--disallowed-tools WebSearch,WebFetch,Bash,Task'
)

SYSTEM = """You are a calibrated forecaster producing a probability distribution over a
single numeric quantity. You are working from a dossier that was assembled BEFORE the
question resolved; you have no tools and must not pretend to have consulted anything new.
Reply with ONE fenced ```json block and nothing else."""

# ---- arm FIVE: the production contract as it stands today -----------------------------
FIVE_GUIDANCE = """## How to state your belief

- Elicit your belief as **five percentiles — 10 / 25 / 50 / 75 / 90** — strictly
  increasing, strictly inside the question's range. Start from the smallest value and
  work up.
- **Widen the tails beyond what feels right.** Distributions that are too narrow are the
  dominant numeric failure; your 10th-90th should feel uncomfortably wide. Ask: "what
  value would genuinely shock me?" - then make sure it carries some mass.
- Anchor the median on the zeroth/first-order forecast (persistence, trend); set the
  spread from the reference class's historical dispersion, not from confidence vibes."""

FIVE_CONTRACT = """```json
{"percentiles": {"10": 1.0, "25": 2.0, "50": 3.0, "75": 4.0, "90": 5.0},
 "reasoning": "...",
 "recall_outcome": {"knows": false, "what": ""}}
```"""

# ---- arm SEVEN: tail anchors + the scoring rule + required tail mechanisms -------------
SEVEN_GUIDANCE = """## How to state your belief

- Elicit your belief as **seven percentiles — 3 / 10 / 25 / 50 / 75 / 90 / 97** —
  strictly increasing, strictly inside the question's range. Start from the smallest
  value and work up.
- Anchor the median on the zeroth/first-order forecast (persistence, trend); set the
  25-75 spread from the reference class's historical dispersion, not from confidence
  vibes.
- **You are scored on the logarithm of the probability your distribution puts on the
  narrow slice that contains the outcome** — the question's range is cut into equal
  buckets and only the mass in the outcome's bucket counts. Three consequences, none of
  them intuitive:
  - **Distance from your median is not scored at all.** A narrow distribution that is
    slightly off can score far worse than a wide one that is badly off. Being "close"
    earns nothing; putting mass exactly where the outcome lands earns everything.
  - **The penalty for a thin region is unbounded** (until a clamp near 1% of uniform).
    An outcome landing inside your range but in a stretch you left nearly empty is a
    catastrophe, not a near miss — and that stretch is usually just outside your
    outermost percentile, because that is where your declared mass runs out.
  - **Mass you put where the outcome does not land is cheap.** Spreading is not free,
    but it is far cheaper than a tail that ends too early.
- **Before you write any number, name the mechanism at each end.** Write one concrete
  sentence for each:
  - `tail_high`: what specific, live mechanism would drive this ABOVE your 97th? (a
    process that keeps accumulating over the remaining window, an escalation, a shock,
    a decision that lands early)
  - `tail_low`: what specific, live mechanism would drive this BELOW your 3rd?
  Then place p3 and p97 so that each named mechanism, if it happened, would sit INSIDE
  your distribution rather than in an empty stretch beyond it.
- **Spend your tail budget inside the question's range.** Probability you push past the
  stated bounds is nearly worthless to you: every forecaster's out-of-bound mass is
  pinned near the same floor, so an outcome outside the range scores about the same for
  everyone. The tail that pays is the one covering values that are extreme but still
  inside the range.
- **Do not default to symmetry.** If you can name a live mechanism on one side and only
  a contrived one on the other, the distribution should be skewed that way, and your p97
  should sit further from your median than your p3 does (or the reverse). Quantities that
  accumulate over a window, counts of events during an ongoing crisis, and prices exposed
  to a supply shock are typically skewed upward; capacity-limited throughput (scheduled
  launches, production runs) is typically capped above and skewed downward."""

SEVEN_CONTRACT = """```json
{"percentiles": {"3": 0.5, "10": 1.0, "25": 2.0, "50": 3.0, "75": 4.0, "90": 5.0, "97": 8.0},
 "tail_high": "<the specific mechanism that would push this above your 97th>",
 "tail_low": "<the specific mechanism that would push this below your 3rd>",
 "reasoning": "...",
 "recall_outcome": {"knows": false, "what": ""}}
```"""

ARMS = {
    "five": (FIVE_GUIDANCE, FIVE_CONTRACT, ("10", "25", "50", "75", "90")),
    "seven": (SEVEN_GUIDANCE, SEVEN_CONTRACT, ("3", "10", "25", "50", "75", "90", "97")),
}


def build_prompt(row: dict[str, Any], arm: str) -> str:
    guidance, contract, _ = ARMS[arm]
    sc = row.get("scaling") or {}
    bounds = (f"Range: {sc.get('range_min')} to {sc.get('range_max')}"
              f"{' (lower bound OPEN)' if sc.get('lower_open') else ''}"
              f"{' (upper bound OPEN)' if sc.get('upper_open') else ''}")
    if sc.get("zero_point") is not None:
        bounds += f"; log-scaled with zero_point {sc['zero_point']}"
    research = row.get("research") or {}
    sources = research.get("sources") or []
    dossier = "\n".join(f"- {s}" for s in sources[:20]) or "- (none recorded)"
    return f"""# Question

{row.get('question')}

## Resolution criterion

{row.get('resolution_criterion') or '(not recorded)'}

## Bounds

{bounds}
Resolves: {row.get('resolve_by')}

# Dossier assembled before resolution

## Reference class and prior
reference_class: {row.get('reference_class') or '(none recorded)'}
base_rate: {row.get('base_rate') if row.get('base_rate') is not None else '(none recorded)'}

## Research notes written at forecast time
{row.get('reasoning') or '(none recorded)'}

## Sources consulted at the time (for provenance; you cannot open them)
{dossier}

# Your task

You have NO tools and cannot research further. Work from the dossier above.

{guidance}

Also fill `recall_outcome`: set `knows` true and describe it if you actually remember how
this specific question resolved from training data. Honesty here costs you nothing and a
false negative corrupts the experiment.

Reply with exactly one fenced json block:

{contract}
"""


def load_rows(resolutions: dict[int, float]) -> list[dict[str, Any]]:
    _, numerics = load_wave(JOURNAL)
    return [r for r in numerics
            if r["source"]["question_id"] in resolutions and r.get("scaling")]


def done_cells(out: Path) -> set[tuple[int, str]]:
    if not out.exists():
        return set()
    cells = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("percentiles"):
            cells.add((int(row["qid"]), str(row["arm"])))
    return cells


def score(out: Path) -> int:
    """Score a completed A/B in leaderboard points and print the paired readout."""
    import statistics as st

    from bench.analysis.minibench_counterfactuals import boot_ci
    from bench.analysis.minibench_numeric_tails import (
        cdf_at, location_of, rebuild, score_row,
    )

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    by_q: dict[int, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r.get("percentiles"):
            by_q.setdefault(int(r["qid"]), {})[str(r["arm"])] = r

    leaked = [(q, a) for q, arms in by_q.items() for a, r in arms.items()
              if (r.get("recall_outcome") or {}).get("knows")]
    if leaked:
        print(f"MEMORY SCREEN: {len(leaked)} cell(s) claim outcome knowledge -> "
              f"excluded pairwise: {leaked}")
    for qid, _arm in leaked:
        by_q.pop(qid, None)

    paired = {q: a for q, a in by_q.items() if "five" in a and "seven" in a}
    print(f"paired questions: {len(paired)} (of {len(by_q)} with any cell)")

    live_journal = {r["source"]["question_id"]: r for r in load_wave(JOURNAL)[1]}
    stats: dict[str, dict[str, list[float]]] = {}
    per_q: list[dict[str, Any]] = []
    for qid, arms in sorted(paired.items()):
        base = arms["five"]
        outcome, scaling = float(base["outcome"]), base["scaling"]
        entry: dict[str, Any] = {"qid": qid, "question": base["question"],
                                 "outcome": outcome,
                                 "n_buckets": int(scaling.get("cdf_size") or 201) - 1}
        for arm in ("five", "seven"):
            cdf = rebuild({k: float(v) for k, v in arms[arm]["percentiles"].items()}, scaling)
            if cdf is None:
                entry[arm] = None
                continue
            s = score_row(cdf, outcome, scaling)
            pit = cdf_at(cdf, location_of(outcome, scaling))
            stats.setdefault(arm, {"score": [], "pit": []})["score"].append(s)
            stats[arm]["pit"].append(pit)
            entry[arm] = {"score": s, "pit": pit,
                          "percentiles": arms[arm]["percentiles"],
                          "tail_high": arms[arm].get("tail_high"),
                          "tail_low": arms[arm].get("tail_low")}
        live = live_journal.get(qid)
        if live and live.get("submitted_cdf"):
            ls = score_row(live["submitted_cdf"], outcome, live["scaling"])
            stats.setdefault("live", {"score": [], "pit": []})["score"].append(ls)
            stats["live"]["pit"].append(cdf_at(live["submitted_cdf"],
                                              location_of(outcome, live["scaling"])))
            entry["live"] = {"score": ls, "percentiles": live.get("percentiles")}
        per_q.append(entry)

    print(f"\n{'arm':<8}{'total':>9}{'mean/q':>9}{'median':>9}{'worst':>9}"
          f"{'in 25-75':>10}{'in 10-90':>10}{'above med':>11}")
    for arm in ("live", "five", "seven"):
        if arm not in stats:
            continue
        sc, pit = stats[arm]["score"], stats[arm]["pit"]
        n = len(sc)
        print(f"{arm:<8}{sum(sc):>9.0f}{st.mean(sc):>9.1f}{st.median(sc):>9.1f}"
              f"{min(sc):>9.1f}"
              f"{sum(1 for p in pit if 0.25 <= p <= 0.75):>7}/{n:<2}"
              f"{sum(1 for p in pit if 0.10 <= p <= 0.90):>7}/{n:<2}"
              f"{sum(1 for p in pit if p > 0.5):>8}/{n:<2}")

    if "five" in stats and "seven" in stats:
        deltas = [b - a for a, b in zip(stats["five"]["score"], stats["seven"]["score"],
                                        strict=True)]
        lo, hi = boot_ci(deltas)
        wins = sum(1 for d in deltas if d > 0)
        print(f"\nPAIRED seven - five: mean {st.mean(deltas):+.1f} points/question  "
              f"CI90 [{lo:+.1f},{hi:+.1f}]  seven wins {wins}/{len(deltas)}")
        big = sorted(zip([e['qid'] for e in per_q], deltas, strict=True),
                     key=lambda t: t[1])
        print(f"  worst for seven: qid {big[0][0]} {big[0][1]:+.1f}   "
              f"best for seven: qid {big[-1][0]} {big[-1][1]:+.1f}")

    named = [e for e in per_q if e.get("seven") and e["seven"].get("tail_high")]
    print(f"\ntail mechanisms named: {len(named)}/{len(per_q)}")
    skews = []
    for e in per_q:
        s = e.get("seven")
        if not s:
            continue
        p = {k: float(v) for k, v in s["percentiles"].items()}
        if {"3", "50", "97"} <= set(p):
            up, down = p["97"] - p["50"], p["50"] - p["3"]
            if down > 0:
                skews.append(up / down)
    if skews:
        print(f"upper/lower tail-length ratio (seven): median {st.median(skews):.2f}, "
              f"skewed up in {sum(1 for s in skews if s > 1.05)}/{len(skews)}, "
              f"down in {sum(1 for s in skews if s < 0.95)}/{len(skews)}")

    (out.parent / (out.stem + "-scored.json")).write_text(
        json.dumps(per_q, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nper-question detail -> {out.parent / (out.stem + '-scored.json')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolutions", type=Path,
                        default=ROOT / "bench/analysis/minibench-2026-07-resolutions.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--agent-cmd", default=DEFAULT_AGENT)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--budget", type=float, default=25.0,
                        help="hard USD cap across the whole run; stops before exceeding")
    parser.add_argument("--limit", type=int, default=0, help="first N questions (0 = all)")
    parser.add_argument("--arms", default="five,seven")
    parser.add_argument("--score-only", action="store_true",
                        help="score an existing results file; run no agents")
    args = parser.parse_args(argv)

    if args.score_only:
        return score(args.out)

    resolutions = {int(k): float(v) for k, v in
                   json.loads(args.resolutions.read_text(encoding="utf-8")).items()}
    rows = load_rows(resolutions)
    if args.limit:
        rows = rows[:args.limit]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    already = done_cells(args.out)
    print(f"{len(rows)} questions x {len(arms)} arms; {len(already)} cells already done")

    spent = 0.0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as fh:
        for row in rows:
            qid = int(row["source"]["question_id"])
            for arm in arms:
                if (qid, arm) in already:
                    continue
                if spent >= args.budget:
                    print(f"budget cap ${args.budget:.2f} reached (${spent:.2f} spent) — stopping")
                    return 0
                try:
                    text, cost, model = run_agent(
                        args.agent_cmd, build_prompt(row, arm), SYSTEM, args.timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  qid {qid} [{arm}] FAILED: {str(exc)[:160]}")
                    continue
                spent += cost
                payload: dict[str, Any] = {}
                with contextlib.suppress(Exception):
                    payload = extract_json(text)
                pct = payload.get("percentiles") or {}
                record = {
                    "qid": qid, "arm": arm, "model": model, "cost_usd": cost,
                    "question": row.get("question"),
                    "scaling": row.get("scaling"),
                    "outcome": resolutions[qid],
                    "percentiles": {str(k): float(v) for k, v in pct.items()} if pct else None,
                    "tail_high": payload.get("tail_high"),
                    "tail_low": payload.get("tail_low"),
                    "recall_outcome": payload.get("recall_outcome"),
                    "reasoning": payload.get("reasoning"),
                    "submitted_percentiles": row.get("percentiles"),
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                keys = ",".join(sorted(pct, key=float)) if pct else "NONE"
                print(f"  qid {qid} [{arm:>5}] ${cost:5.2f} cum ${spent:6.2f}  pcts={keys}")
    print(f"\ndone; ${spent:.2f} spent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
