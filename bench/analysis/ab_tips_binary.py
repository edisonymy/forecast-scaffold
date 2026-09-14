"""A/B the six forecasting tips on binary re-forecasts: production contract vs contract + tips.

WHAT THIS TESTS. ``docs/forecasting-tips-draft-2026-08-09.md`` proposes six direction-neutral
procedural tips for the reasoning step. They are NOT in the live skill and must not enter it
until they beat a preregistered test, because the LLM literature says imperative caution text
flattens forecasts globally toward 50 (arXiv 2506.01578) — a change that would look harmless
in prose and cost points on exactly the sharp low calls that carry our record. This script is
that test: a paired re-elicitation on every resolved binary from both MiniBench waves, control
= the production binary reasoning contract, treatment = the same contract plus the six tips
verbatim. It measures the tips themselves, not any downstream elicitation format.

PREREGISTERED DESIGN (frozen 2026-08-09, before any cell was run)

- QUESTIONS: every resolved binary from both waves — journal rows via
  ``minibench_counterfactuals.load_wave`` with ``DEFAULT_WINDOWS`` (2026-07-17..2026-08-05 and
  2026-08-06..2026-08-09), intersected with the merged resolutions from
  ``minibench-2026-07-resolutions.json`` + ``minibench-2026-07-27-resolutions.json``, keeping
  only 0/1 outcomes (multiple-choice labels and numeric values are dropped by the loader and
  by the 0/1 filter). Expected n = 69. Question order is sorted by qid so ``--limit`` picks a
  deterministic prefix.

- PAIRING: two arms per question, same model, same context, run as a pair. Scoring is paired
  throughout; a question missing either arm is dropped from every statistic.

- CONTEXT HELD FIXED ACROSS ARMS: question text, resolution_criterion, resolve_by, and the
  live run's own research digest (the journal ``research`` field: n_searches, the source list,
  and any recorded missing_evidence), plus ``reference_class`` and ``base_rate``.

- DELIBERATE DEVIATION FROM ``ab_numeric_anchors.py``: that experiment fed each arm the live
  run's journaled ``reasoning``, because it was A/B-ing a downstream ELICITATION CONTRACT
  (five percentiles vs seven) and wanted both arms anchored identically on the original
  analysis; anchoring there only made the measured effect conservative. Here the treatment IS
  the reasoning step. The journaled ``reasoning`` embeds the live run's conclusion and the
  live ``probability`` is the answer itself; injecting either would anchor both arms onto the
  same number and guarantee a null regardless of whether the tips work. So BOTH are excluded:
  neither arm sees the live reasoning text and neither sees the live probability. The cost of
  this choice is honest and stated: the arms re-reason from a thinner dossier than the live
  run had (sources by URL, not their contents), so absolute Brier levels here are NOT
  comparable to the live wave's — only the paired treatment-minus-control delta is.

- ARMS (differ in exactly one block of text):
    control   = the production binary reasoning contract, adapted from ``bot/run_bot.py``:
                reason from the research, then emit one fenced json block with "probability"
                (0-1), "recall_outcome", "reasoning".
    treatment = byte-identical prompt with the six tips inserted verbatim under a
                "Forecasting tips:" header, immediately before the output contract.

- MODEL / CONTAMINATION: both arms run
  ``claude -p --model claude-sonnet-5 --output-format json
  --disallowed-tools WebSearch,WebFetch,Bash,Task``. The events post-date the training cutoff
  and no arm can search. Each cell additionally declares ``recall_outcome``; any cell claiming
  knowledge of the outcome is reported and its QUESTION is excluded PAIRWISE (both arms), per
  the repository's pastcast validity ritual.

- MECHANICS: results append to a JSONL keyed (qid, arm); a rerun skips cells already present,
  so the run is resumable. Concurrency 1 (sequential). Cost comes from the CLI's own
  ``total_cost_usd`` envelope via ``run_agent``; ``--budget`` (default $10) is a hard cap
  checked before each subprocess and stops the run cleanly.

PREREGISTERED READOUT (``--score-only``)
 1. PRIMARY: paired Brier mean delta, treatment - control (negative = tips better), with a
    paired bootstrap CI90 (10,000 resamples, seed 7, via ``minibench_counterfactuals.boot_ci``).
 2. GUARD subset — live journal p <= 0.15 AND outcome 0: paired mean delta + CI90. This is the
    flattening tripwire: our correct sharp lows are where caution text would cost the most.
 3. TARGETED subset — (live p <= 0.35 AND outcome 1) OR (live p >= 0.70 AND outcome 0): paired
    mean delta + CI90. The known loss shapes the tips are supposed to help.
 4. Diagnostics: mean |p_treatment - p_control|; count of questions where the treatment moved
    TOWARD 0.5 vs AWAY from it (the flattening diagnostic); memory-screen exclusions; total
    cost. Subsets are defined on the LIVE journal probability, not on either arm's output, so
    membership is fixed before any cell runs.

DECISION RULE (preregistered; echoed by --score-only)
    SHIP only if
      (a) the primary paired CI90 excludes 0 in the treatment's favor (upper bound < 0),
      OR
      (b) |primary mean delta| < 0.005 AND guard mean delta < +0.005 AND
          targeted mean delta < -0.02.
    Otherwise DO NOT SHIP.

    Sign convention throughout: delta = Brier(treatment) - Brier(control); negative favors the
    tips. NOTE ON CLAUSE (b): the commissioning brief wrote the guard clause as "guard mean
    delta > -0.005", which under this convention is satisfied by ANY guard loss however large
    and therefore inverts the tripwire it exists to be. It is preregistered here in the only
    coherent direction — the guard must not LOSE materially, i.e. guard mean delta < +0.005 —
    and the readout prints the guard mean and both readings' verdicts so a reader can apply
    either without rerunning anything.

Usage:
    python bench/analysis/ab_tips_binary.py --out RESULTS.jsonl
    python bench/analysis/ab_tips_binary.py --out RESULTS.jsonl --limit 2 --budget 1
    python bench/analysis/ab_tips_binary.py --out RESULTS.jsonl --score-only
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics as st
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bench.analysis.minibench_counterfactuals import (  # noqa: E402
    JOURNAL,
    boot_ci,
    load_resolutions,
    load_wave,
)
from bot.run_bot import extract_json, run_agent  # noqa: E402

DEFAULT_AGENT = (
    'claude -p --model claude-sonnet-5 --output-format json '
    '--disallowed-tools WebSearch,WebFetch,Bash,Task'
)

DEFAULT_RESOLUTIONS = (
    ROOT / "bench/analysis/minibench-2026-07-resolutions.json",
    ROOT / "bench/analysis/minibench-2026-07-27-resolutions.json",
)

SYSTEM = """You are a calibrated forecaster producing a single probability for a binary
question. You are working from a dossier that was assembled BEFORE the question resolved;
you have no tools and must not pretend to have consulted anything new.
Reply with ONE fenced ```json block and nothing else."""

# ---- the treatment text: the six tips, verbatim from the "## Tips (reasoning step)" section
# of docs/forecasting-tips-draft-2026-08-09.md. Copied byte-for-byte (including the parenthetical
# literature/provenance notes) so that what is tested here is exactly what would ship.
TIPS = """**1. Name the blocker or the driver — don't infer from silence.**
A found schedule, docket entry, recess calendar, or registry count is the strongest
fact this question class admits; weigh it heavily. The *absence* of a date you searched
for is weak evidence in both directions — it neither licenses an extreme call nor
forbids one. Ask what the actor has already done, not what you failed to find.
*(Lit: observers forecasting others' timelines run pessimistic — Buehler & Griffin;
announced target dates slip — Flyvbjerg. Both reduce to: anchor on concrete acts.)*

**2. Reconcile the notes and the number.**
Before submitting, reread your own brief. If the concrete evidence you recorded pulls
against your number, resolve the conflict explicitly: move the number, or write down
why the evidence does not bind. A decisive unknown you named yourself must be priced —
state which way it cuts, don't leave it decorative.
*(Two scored losses where the journal contained the winning consideration and the
number ignored it. LLM lit: verbalized numbers lag the model's own stated reasoning —
arXiv 2607.08046.)*

**3. Streaks and trends are regimes: name the machine.**
Before extrapolating a run rate — or betting against one — name the mechanism
generating it and what would turn it off, then check both sides: what has the
off-switch already done; what does simple continuation imply. If continuation exits
the question's range before the deadline, say so in the brief and price the escape
explicitly (`p_below_lower` / `p_above_upper`, v0.4.23) instead of letting the CDF
builder's bound anchor decide. This cuts both ways and is not by itself a reason to
move.
*(Independently replicated: capable models track growth with the distribution's center
while under-pricing regime breaks in both tails — FRI, arXiv 2605.22672.)*

**4. Premortem the confident call.**
For any forecast you would call confident, write the strongest single path to the
opposite resolution — the concrete world where you are wrong — and price that path
explicitly. If you genuinely cannot articulate one, the confidence stands; if you can,
check the number still clears it.
*(Klein 2007 premortem; Mitchell/Russo/Pennington consider-the-opposite. Direction-
neutral by construction: it tests confident YES and confident NO identically.)*

**5. Boldness is bought with facts.**
Stand far from a crowd or market only on a specific, checkable fact they plausibly
lack, and size the deviation to the concreteness of the fact. A claim about other
minds ("the market is overreacting", "thin books herd") is not a fact. Note that LLM
forecaster errors correlate strongly across models (r≈0.77 — arXiv 2605.00844): against
a bot crowd, your conviction is less independent than it feels.
*(Our three biggest wins were fact-backed extreme calls; our one market-deviation loss
was psychology-backed.)*

**6. Score the question, not your story.**
Re-read the resolution criterion immediately before submitting. Quarantine
P(condition) from P(outcome | condition). Continuous questions pay log density at the
outcome, not interval tidiness. A deadline pays observation-in-the-named-source, not
event-in-the-world — price the reporting step in whichever direction it cuts.
*(Scoring-rule mechanics — Gneiting & Raftery 2007 and the platform's own rules;
stated as mechanics, not as a scored-mistake trace.)*"""

TREATMENT_BLOCK = f"""## Forecasting tips:

{TIPS}
"""

CONTRACT = """```json
{"probability": 0.63,
 "recall_outcome": false,
 "reasoning": "<3-6 lines>"}
```"""

ARMS = ("control", "treatment")


def digest(row: dict[str, Any]) -> str:
    """The live run's own research digest, rendered identically for both arms."""
    research = row.get("research") or {}
    lines = []
    n_searches = research.get("n_searches")
    if n_searches is not None:
        lines.append(f"searches run at the time: {n_searches}")
    sources = [s for s in (research.get("sources") or []) if str(s).strip()]
    lines.append("sources consulted at the time (for provenance; you cannot open them):")
    lines.extend(f"  - {s}" for s in sources[:20] or ["(none recorded)"])
    missing = [m for m in (research.get("missing_evidence") or []) if str(m).strip()]
    if missing:
        lines.append("gaps the researcher flagged at the time:")
        lines.extend(f"  - {m}" for m in missing[:10])
    return "\n".join(lines)


def build_prompt(row: dict[str, Any], arm: str) -> str:
    """The two arms differ by exactly one inserted block; everything else is byte-identical."""
    tips = TREATMENT_BLOCK if arm == "treatment" else ""
    base_rate = row.get("base_rate")
    return f"""# Question

{row.get('question')}

## Resolution criterion

{row.get('resolution_criterion') or '(not recorded)'}

Resolves: {row.get('resolve_by')}

# Dossier assembled before resolution

## Reference class and prior
reference_class: {row.get('reference_class') or '(none recorded)'}
base_rate: {base_rate if base_rate is not None else '(none recorded)'}

## Research digest from the run that forecast this question
{digest(row)}

# Your task

You have NO tools and cannot research further. Work from the dossier above.

Work through the reasoning: derive your prior from the reference class and base rate above,
then adjust on the evidence in the dossier, carrying considerations in BOTH directions. Then
state your belief as a single probability in (0, 1) that the question resolves YES.

{tips}Also fill `recall_outcome`: set it true ONLY if you actually believe you know, from
training data, how this specific question resolved. Honesty here costs you nothing and a
false negative corrupts the experiment.

Reply with exactly one fenced json block, no text after it:

{CONTRACT}
"""


def knows_outcome(value: Any) -> bool:
    """Memory screen, tolerant of both the bare-bool contract and a {'knows': ...} dict."""
    if isinstance(value, dict):
        return bool(value.get("knows"))
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def load_rows(resolutions: dict[int, float], journal: Path) -> list[dict[str, Any]]:
    """Resolved binaries from both waves, 0/1 outcomes only, sorted by qid (deterministic)."""
    binaries, _ = load_wave(journal)
    rows = [r for r in binaries
            if resolutions.get(r["source"]["question_id"]) in (0.0, 1.0)]
    return sorted(rows, key=lambda r: int(r["source"]["question_id"]))


def done_cells(out: Path) -> set[tuple[int, str]]:
    if not out.exists():
        return set()
    cells = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("probability") is not None:
            cells.add((int(row["qid"]), str(row["arm"])))
    return cells


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def paired_block(label: str, deltas: list[float], note: str = "") -> float | None:
    """Print one paired mean + CI90 block; returns the mean (None when the subset is empty)."""
    if not deltas:
        print(f"  {label:<34} n=0   (empty subset)")
        return None
    mean = st.mean(deltas)
    if len(deltas) >= 2:
        lo, hi = boot_ci(deltas)
        ci = f"CI90 [{lo:+.4f},{hi:+.4f}]"
    else:
        ci = "CI90 (n<2, not computed)"
    wins = sum(1 for d in deltas if d < 0)
    print(f"  {label:<34} n={len(deltas):<3} mean {mean:+.4f}  {ci}  "
          f"tips better on {wins}/{len(deltas)}{note}")
    return mean


def score(out: Path, journal: Path, resolution_paths: list[Path]) -> int:
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    by_q: dict[int, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r.get("probability") is not None:
            by_q.setdefault(int(r["qid"]), {})[str(r["arm"])] = r

    leaked = [(q, a) for q, arms in by_q.items() for a, r in arms.items()
              if knows_outcome(r.get("recall_outcome"))]
    print(f"MEMORY SCREEN: {len(leaked)} cell(s) claim outcome knowledge"
          + (f" -> question(s) excluded PAIRWISE: {leaked}" if leaked else " (none)"))
    for qid, _arm in leaked:
        by_q.pop(qid, None)

    paired = {q: a for q, a in by_q.items() if all(arm in a for arm in ARMS)}
    print(f"paired questions: {len(paired)} (of {len(by_q)} surviving with any cell)")
    if not paired:
        return 0

    resolutions = load_resolutions(resolution_paths)
    live = {r["source"]["question_id"]: r for r in load_rows(resolutions, journal)}

    per_q: list[dict[str, Any]] = []
    for qid in sorted(paired):
        arms = paired[qid]
        y = float(arms["control"]["outcome"])
        p_c = float(arms["control"]["probability"])
        p_t = float(arms["treatment"]["probability"])
        live_p = live.get(qid, {}).get("probability")
        per_q.append({
            "qid": qid,
            "question": arms["control"].get("question"),
            "outcome": y,
            "live_p": float(live_p) if live_p is not None else None,
            "p_control": p_c,
            "p_treatment": p_t,
            "brier_control": brier(p_c, y),
            "brier_treatment": brier(p_t, y),
            "delta": brier(p_t, y) - brier(p_c, y),
            "reasoning_control": arms["control"].get("reasoning"),
            "reasoning_treatment": arms["treatment"].get("reasoning"),
        })

    deltas = [e["delta"] for e in per_q]
    b_c = st.mean(e["brier_control"] for e in per_q)
    b_t = st.mean(e["brier_treatment"] for e in per_q)
    print(f"\nmean Brier   control {b_c:.4f}   treatment {b_t:.4f}")
    print("\ndelta = Brier(treatment) - Brier(control); NEGATIVE favors the tips")
    primary = paired_block("1. PRIMARY (all paired)", deltas)

    guard_rows = [e for e in per_q
                  if e["live_p"] is not None and e["live_p"] <= 0.15 and e["outcome"] == 0.0]
    guard = paired_block("2. GUARD live p<=0.15 & outcome 0",
                         [e["delta"] for e in guard_rows],
                         "   <- flattening tripwire")

    targeted_rows = [e for e in per_q if e["live_p"] is not None and (
        (e["live_p"] <= 0.35 and e["outcome"] == 1.0)
        or (e["live_p"] >= 0.70 and e["outcome"] == 0.0))]
    targeted = paired_block("3. TARGETED known loss shapes",
                            [e["delta"] for e in targeted_rows])

    moves = [abs(e["p_treatment"] - e["p_control"]) for e in per_q]
    toward = sum(1 for e in per_q
                 if abs(e["p_treatment"] - 0.5) < abs(e["p_control"] - 0.5) - 1e-12)
    away = sum(1 for e in per_q
               if abs(e["p_treatment"] - 0.5) > abs(e["p_control"] - 0.5) + 1e-12)
    print(f"\n4. movement: mean |p_treatment - p_control| = {st.mean(moves):.4f} "
          f"(max {max(moves):.4f})")
    print(f"   flattening diagnostic: moved TOWARD 0.5 on {toward}/{len(per_q)}, "
          f"AWAY on {away}/{len(per_q)}, unchanged {len(per_q) - toward - away}")
    cost = sum(float(r.get("cost_usd") or 0.0) for r in rows)
    print(f"   memory-screen exclusions: {len(leaked)} cell(s); total cost ${cost:.2f} "
          f"over {len(rows)} recorded cell(s)")

    print("\nDECISION RULE (preregistered): SHIP only if (a) primary CI90 excludes 0 in the "
          "treatment's\n  favor, OR (b) |primary mean| < 0.005 AND guard mean < +0.005 AND "
          "targeted mean < -0.02.")
    lo, hi = boot_ci(deltas) if len(deltas) >= 2 else (float("nan"), float("nan"))
    clause_a = len(deltas) >= 2 and hi < 0
    clause_b = (primary is not None and abs(primary) < 0.005
                and guard is not None and guard < 0.005
                and targeted is not None and targeted < -0.02)
    print(f"  (a) primary CI90 upper bound {hi:+.4f} < 0 ? {'YES' if clause_a else 'no'}")
    print(f"  (b) |primary| {abs(primary):.4f} < 0.005 ? "
          f"{'YES' if abs(primary) < 0.005 else 'no'};  "
          f"guard {guard if guard is None else f'{guard:+.4f}'} < +0.005 ? "
          f"{'YES' if guard is not None and guard < 0.005 else 'no'};  "
          f"targeted {targeted if targeted is None else f'{targeted:+.4f}'} < -0.02 ? "
          f"{'YES' if targeted is not None and targeted < -0.02 else 'no'}")
    print(f"  VERDICT: {'SHIP' if (clause_a or clause_b) else 'DO NOT SHIP'}")
    print("  note: the brief's literal guard clause was 'guard mean > -0.005', which any guard "
          "loss\n  satisfies and so inverts the tripwire; preregistered above as "
          "'guard mean < +0.005'. Literal\n  reading would score guard "
          f"{'PASS' if guard is not None and guard > -0.005 else 'fail'}.")

    scored_path = out.parent / (out.stem + "-scored.json")
    scored_path.write_text(json.dumps(per_q, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nper-question detail -> {scored_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--resolutions", type=Path, action="append", default=None,
                        help="JSON {qid: outcome}; repeatable, later files win on collision. "
                             f"Default: {', '.join(p.name for p in DEFAULT_RESOLUTIONS)}")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--agent-cmd", default=DEFAULT_AGENT)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--budget", type=float, default=10.0,
                        help="hard USD cap across the whole run; stops before exceeding")
    parser.add_argument("--limit", type=int, default=0, help="first N questions (0 = all)")
    parser.add_argument("--score-only", action="store_true",
                        help="score an existing results file; run no agents")
    args = parser.parse_args(argv)

    resolution_paths = args.resolutions or list(DEFAULT_RESOLUTIONS)
    if args.score_only:
        return score(args.out, args.journal, resolution_paths)

    resolutions = load_resolutions(resolution_paths)
    rows = load_rows(resolutions, args.journal)
    if args.limit:
        rows = rows[:args.limit]
    already = done_cells(args.out)
    print(f"{len(rows)} questions x {len(ARMS)} arms; {len(already)} cells already done; "
          f"budget ${args.budget:.2f}")

    spent = 0.0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as fh:
        for row in rows:
            qid = int(row["source"]["question_id"])
            for arm in ARMS:
                if (qid, arm) in already:
                    continue
                if spent >= args.budget:
                    print(f"budget cap ${args.budget:.2f} reached (${spent:.2f} spent) "
                          "— stopping")
                    return 0
                try:
                    text, cost, model = run_agent(
                        args.agent_cmd, build_prompt(row, arm), SYSTEM, args.timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  qid {qid} [{arm}] FAILED: {str(exc)[:200]}")
                    continue
                spent += cost
                payload: dict[str, Any] = {}
                with contextlib.suppress(Exception):
                    payload = extract_json(text)
                p = payload.get("probability")
                try:
                    p = float(p)
                except (TypeError, ValueError):
                    p = None
                if p is not None and not (0.0 < p < 1.0):
                    print(f"  qid {qid} [{arm}] probability out of (0,1): {p!r} — kept as-is")
                record = {
                    "qid": qid, "arm": arm, "model": model, "cost_usd": cost,
                    "question": row.get("question"),
                    "outcome": resolutions[qid],
                    "live_p": float(row["probability"]),
                    "probability": p,
                    "recall_outcome": payload.get("recall_outcome"),
                    "reasoning": payload.get("reasoning"),
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"  qid {qid} [{arm:>9}] ${cost:5.2f} cum ${spent:6.2f}  "
                      f"p={p if p is None else f'{p:.3f}'}  live={row['probability']:.3f}  "
                      f"y={resolutions[qid]:.0f}  recall={payload.get('recall_outcome')!r}")
    print(f"\ndone; ${spent:.2f} spent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
