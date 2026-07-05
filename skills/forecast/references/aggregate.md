# aggregate.md — draws, pooling, and the crowd

Aggregation is the most reliable accuracy lever in the field: even running the same forecaster
twice measurably improves the Brier score, and a pool of a dozen diverse models has matched a
900-person human crowd. The math lives in `fsj.py aggregate` — this file is about producing draws
worth pooling and choosing the right pool.

## Producing draws

A draw = one full pass of the reasoning spine ending in one probability. Draw count per tier comes
from config. The value of an ensemble is the **diversity of its errors**, so vary the framing
between draws — emphasize a different reference class, reverse the order you argue (NO-case first
vs YES-case first), shuffle option order on multiple choice, start one draw from the trend anchor
and another from the status quo. Copying your first number N times is not an ensemble.

**Each draw must condition on a different named scenario, not resample the same judgment.**
Audited runs show varied-framing draws collapse into one estimate ± noise (spreads of 2–5 points
around a single view). So assign scenarios before drawing: at least one draw assumes your
premortem story actually happens, one assumes the status quo holds to the deadline, one takes the
strongest YES-case at face value, one the strongest NO-case. If your draws still span less than
~5 points, report fewer draws honestly instead of padding — a tight cluster is one draw wearing
twelve hats, and pooling it adds false precision, not information.

If the host agent supports subagents, high tier should run draws as **independent subagents that
do not share context** (each gets the question, criterion, and research digest — not each other's
numbers). Independent contexts across different models are the closest available thing to
independent forecasters.

## Choosing the pool

| Situation | Method | Why |
|---|---|---|
| Your own draws (one forecaster, varied framings) | `trimmed_mean` (default) | Correlated draws share their information; trimming is the right robustification. **Never extremize your own draws** — extremizing assumes independent private information, which self-ensembles don't have; it just double-counts. |
| Genuinely independent forecasters (different models/agents, separate contexts) | `--method geo_mean_odds` | Geometric mean of odds, dropping the single most extreme forecast on each end — the aggregation rule used by the best-track-record human forecasting teams. |
| Skewed or contaminated draw set | `--method median` | Robust fallback. |

## The crowd

If a community prediction or market price exists, capture it with its timestamp and pass
`--crowd`. A simple 50/50 blend of a good system with the crowd has beaten both alone — the crowd
is an anchor, not an opponent. Two rules:

- **Disagreement is a stop sign, not a triumph.** If your aggregate is far from a liquid crowd
  number, either they know something (find it) or you do (name it, and be able to defend it). Only
  then proceed.
- The pre-blend aggregate, the crowd value, and the blended result all belong in the record
  (`--draws`, `--crowd-value`, final `--probability`) so the track record can later show whether
  your edge over the crowd was real.

## Clamp and overrides

`aggregate` clamps the final probability into the configured band and says so; the record command
warns when a probability sits outside it. The clamp encodes measured tail overconfidence — but
tails that are too *thin* are also a documented failure. Overriding the band is legitimate exactly
when the reasoning summary cites decisive evidence for the extreme; do it consciously, never
silently.

## Consistency checks (high tier)

Before recording, sanity-check the numbers against themselves: complementary framings should sum
to ~1 (if you'd forecast "will X NOT happen" at anything other than 1−p, find out why); a
probability over a longer horizon must be ≥ the same event over a shorter one; multiple-choice
probabilities must sum to 1 (the tool enforces this). Cheap, model-independent, and they catch
real errors every model generation.
