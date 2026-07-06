---
name: forecast
description: Produce a calibrated probability forecast for any question about the future or any uncertain claim — "will X happen", "what are the odds", "how likely is it" — and record it to a scorable journal. Also use to operationalize a vague question into a resolvable one (exact resolution criterion + date), or to break a big question into fast-resolving sub-questions. Effort auto-scales (auto/low/medium/high). Pairs with the calibrate skill, which resolves and Brier-scores what this records.
---

# forecast — calibrated, tracked probability forecasts

You are producing a forecast that will later be **scored against reality**. Truth-seeking, not
belief-mirroring: forecast what is actually likely, not what anyone hopes. Every forecast leaves a
falsifiable record — a probability, an exact resolution criterion, and a date — so the `calibrate`
skill can grade it later. An unrecorded forecast is a wasted forecast.

## Tooling (check once per session)

- `fsj.py` lives in this skill's `scripts/` directory (in a plugin install:
  `${CLAUDE_PLUGIN_ROOT}/skills/forecast/scripts/fsj.py`). It is pure stdlib, Python >= 3.11,
  run directly as a file. It owns all arithmetic and persistence — never recompute Brier scores,
  aggregation, or clamps by hand when you can run it.
- Journal: `$FORECAST_JOURNAL` if set, else `./forecasts.jsonl` in the working directory.
- Config: `$FORECAST_CONFIG` or `./forecast.toml`, optional — defaults are built in. To see the
  effective tier parameters (draw counts, clamp band, blend weight): `python fsj.py config`.
- **No Python available** (some web sandboxes): produce the record as a fenced JSON block with at
  least `question`, `question_type`, `resolution_criterion`, `resolve_by` (YYYY-MM-DD),
  `probability` (binary — or `options`+`probabilities`, or `percentiles`), `reference_class`,
  `base_rate`, `reasoning`, `what_would_change_my_mind`, and `status: "open"`; apply the clamp
  band and the checks from `references/aggregate.md` manually, and tell the user to append that
  line to their journal file. The loop still works; only the automation degrades.

## Step 0 — Triage: pick the effort tier

Default is **auto**: run this rubric in one short pass, say which tier you picked and why, then
proceed. The user can override with an explicit tier ("effort: high") at any time.

| Signal | Effect |
|---|---|
| Does the answer move a real decision or a belief the user tracks? | If clearly nothing: say so. Forecast anyway only if asked or if it is cheap and resolves soon (free calibration data). |
| Stakes of what it moves | trivial/low → **low** · moderate → **medium** · high → **high** |
| Genuine uncertainty | contested, no decent anchor → bump one tier up · near-certain status quo, or a liquid crowd/market number already answers it → drop one tier down |
| Question shape | multiple-choice, numeric, or conditional → at least **medium** |

Tier parameters (number of draws, searches) come from config; the stages each tier runs:

| Stage | low | medium | high |
|---|---|---|---|
| Operationalize (Step 1) | inline checklist | + `references/question-hygiene.md` | same, + adversarial re-read |
| Research (Step 2) | 1 search (the already-resolved check), reference class from knowledge | `references/research.md` | same, + historical/current two-pass |
| Reason (Step 3) | short scratchpad | full `references/reasoning.md` spine | same, + premortem + second private estimate |
| Draws & aggregate (Step 4) | 1 draw, clamp | subagent fan-out on a shared dossier (`runs`), crowd blend | same with more runs (cross-model if available), + consistency checks |
| Record (Step 5) | always | always | always |

## Step 1 — Operationalize the question

A forecast on an ambiguous question is unscorable. Establish, and carry **verbatim** through every
later step: the exact **resolution criterion** (what counts, per which source/arbiter), the
**resolve-by date**, and the question type (binary / multiple-choice / numeric). If the user's
question is vague, propose the operationalized version and confirm it captures what they meant.

Minimum checklist (every tier): Could two honest people disagree on how this resolves? Is there a
technicality that flips it? **Is it already effectively resolved?** — checking this is the single
most catastrophic failure mode in the field; look for the answer before estimating it. At medium+
tier, work through `references/question-hygiene.md`.

## Step 2 — Research

Low tier: name a reference class and its base rate from what you know; one search only to confirm
the question is not already resolved. Medium+: follow `references/research.md` — hunt the evidence
that would most change the estimate, use at least two independent sources, primary sources first,
red-team your own draft answer. Never invent a base rate; search for published data, and if none
exists, construct one from counted instances and say so. Capture `{n_searches, sources}` for the
record.

## Step 3 — Reason

Follow the scratchpad spine in `references/reasoning.md` (low tier: its short form). For
multiple-choice, numeric, or conditional shapes read `references/question-types.md`; when
decomposition would help — or the question is slow and needs fast-resolving proxy sub-questions —
read `references/decompose.md`. The
non-negotiables at every tier: state the **status-quo outcome** (the world changes slowly most of
the time), anchor on a named **reference class + base rate before** touching case specifics, argue
**both directions**, and land on a probability at **1% granularity** — the last digit needs a
stated reason; 50% and round numbers are claims, not defaults. Do not perform odds-ratio arithmetic
theater; keep the Bayesian discipline structural (base rate first, independent evidence clusters,
explicit update).

## Step 4 — Draws and aggregation

**If your surface has subagents (Claude Code, Cowork, any Task-capable host), fan-out is the
default at medium+ tier, not an upgrade**: write an estimate-free research dossier from Step 2,
spawn the tier's `runs` as parallel subagents — each gets the dossier plus one suggested lens (a
diversity device it may swap for a better angle), never each other's numbers — and pool. Research
happens once; reasoning happens independently k times. The full protocol, the lens list, and the no-numbers-in-the-dossier rule (it is
load-bearing) are in `references/aggregate.md`. Then pool with the tool, which applies the
configured clamp:

```
python fsj.py aggregate --draws 0.52,0.58,0.56,0.61,0.66 --method geo_mean_odds --crowd 0.60
```

Method rules (details and rationale in `references/aggregate.md`): separate contexts (subagents,
harness runs) → `--method geo_mean_odds`; in-context draws → `trimmed_mean`; a crowd or market
number exists → capture it and pass `--crowd` (and if your aggregate is far from the crowd, stop
and find out what they know that you don't — or what you know that they don't — before
proceeding). Never extremize.

**No subagents** (a plain chat): run the lens set as in-context draws — each draw still estimates
the same unconditional probability from a different starting frame — pool with `trimmed_mean`,
and **tell the user**: "draws were in-context (correlated) — treat this tier's error bars as
wider than usual." Degraded honestly beats differentiated in name only.

## Step 5 — Record

```
python fsj.py record \
  --question "..." --probability 0.5917 --resolve-by 2026-12-31 \
  --criterion "<verbatim resolution criterion>" \
  --reference-class "<the class>" --base-rate 0.35 \
  --why "<which decision/belief this moves>" \
  --draws 0.52,0.58,0.56,0.61,0.66 \
  --aggregation "geo_mean_odds(n=5) blended with crowd=0.6 (weight 0.8)" \
  --effort "medium (auto)" --model "<model(s) used>" \
  --crowd-value 0.60 --crowd-source "metaculus" \
  --reasoning "<3-6 line summary: base rate, key update, main counterargument>" \
  --changes-mind "<observation that would move this>"
```

(The recorded probability and aggregation string are the tool's Step 4 output, verbatim — the
tool wins. Multiple-choice records use `--options "A,B,C" --probabilities "0.5,0.3,0.2"`; numeric
records use `--percentiles "10:5,25:8,50:12,75:20,90:35"`; a full record can also be passed as
one object via `--record-json`.)

Fix any errors it reports; take its warnings seriously (they encode measured failure modes). For a
big slow question, also record 2–5 **fast-proxy sub-questions** that resolve in weeks
(`--parent-id <id> --fast-proxy`) — they generate the calibration signal long before the parent
resolves.

## Step 6 — Report

Give the user: the probability, the two-sentence version of why (base rate → key update), what
would change your mind, how it compares to the crowd if one exists, and the record id. State the
resolve-by date so they know when `calibrate` will score it.

## Guardrails

- **Falsifiable or it's worthless** — no record without a criterion and a date.
- **Truth-seeking, not belief-mirroring** — the score is against reality, not against approval.
- **The already-resolved trap** — always check before estimating.
- **Don't restate math** — `fsj.py` owns clamps, pooling, and scoring; if a number differs from
  what the tool computes, the tool wins.
- **Honesty about tier** — say which tier ran and why; a low-tier answer to a high-stakes question
  must be flagged as such.
