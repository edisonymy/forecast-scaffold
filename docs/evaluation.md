# Evaluation — how we'll know if it's any good

A forecasting scaffold's only honest credential is a scored track record against baselines. This
file is the protocol.

## No naive backtesting

Backtesting on resolved questions is contaminated in ways that flatter the system (Paleka et al.,
*Pitfalls in Evaluating Language Model Forecasters*, [arXiv:2506.00723](https://arxiv.org/abs/2506.00723)):
question selection leaks outcomes, date-restricted retrieval leaks the future through today's
search rankings, and stated training cutoffs under-report what models know by ~3–4 months. So:
**live forecasting is the evaluation**; frozen-corpus "pastcasting" harnesses are acceptable for
offline iteration, ad-hoc backtests are not evidence.

## Baselines (recorded for every evaluated forecast)

1. **Always-0.5** — Brier 0.25. Below this, something is broken.
2. **Community median / market price at forecast time** — captured in the record's `crowd` field
   the moment the forecast is made. The interesting number is the *edge over the crowd*, because
   "beat the crowd" claims without the timestamp are circular.
3. **Zero-shot frontier model** — same question, no scaffold, one call. Isolates what the
   scaffold adds beyond the model.

## Paired tier comparison

To compare effort tiers (or any two scaffold variants), run them on **identical questions** and
score the per-question difference. Pairing cuts the sample needed to detect a 0.02 Brier gap from
~3,500 questions per arm to ~150–700. Two mechanics matter:

- **Cluster by underlying event** — questions sharing a driver are not independent samples;
  a lucky call on one theme can masquerade as skill across ten questions.
- **Evaluate across disjoint calendar windows** — one window's regime can flatter one variant.

## The milestone ladder

In order, each measurable at near-zero marginal cost:

1. **Beat always-0.5** (any positive skill at all).
2. **Beat the zero-shot frontier model, paired** (the scaffold earns its complexity).
3. **Approach the community prediction** on a meaningful sample (crowd-level skill).
4. **Place respectably in a live bot tournament season**
   ([Metaculus FutureEval](https://www.metaculus.com/futureeval/)) — the external, tamper-proof
   version of all of the above. Its biweekly MiniBench tournaments are the fast, leak-free
   iteration loop.

For context on the bar: Metaculus Pro human forecasters beat every bot team in every season
through mid-2026, and the residual human edge is *discrimination* (justified extremity), not
calibration. Crowd-level is already a high bar.

## Tier distillation (the internal benchmark)

The cheap tiers exist to approximate the expensive one. `bench/` operationalizes that as
distillation: teachers = the `high` tier and the crowd (ForecastBench market freeze values +
live Manifold/Polymarket prices — no Metaculus access needed); students = `low`, `medium`,
`auto`. Every tier runs blind on the identical open-question set, and the report scores
mean |Δp|, RMS Δp, KL(teacher‖student), and |Δlogit| — per dollar. The tuning loop is:
run the benchmark → move whatever the gap implicates (triage rubric, draw counts, research
passes — all in config/skill text) → bump `scaffold_version` → rerun the same set. Crowd
distance is the fast proxy; the slow honest check stays resolution-based (the same
ForecastBench IDs resolve in their published resolution sets, and the journal resolves via
`calibrate`). Caveat kept in view: distilling toward the crowd can never *beat* the crowd —
once tiers converge, the target shifts to resolution Brier (milestone 3+).

## Pre-registration / tamper evidence

- Forecasts submitted to a platform are timestamped by the platform — the strongest form.
- The journal file is committed to git and **pushed promptly** to a public remote (local history
  is rewritable; public push history isn't, practically).
- `forecast_at` records the commitment timestamp inside each record; `resolve` refuses silent
  overwrites; ambiguous questions are annulled, not conveniently graded.

## Ongoing self-testing

The `calibrate` skill run on a schedule (daily/weekly) is the standing harness: resolve what's
due, report Brier + direction, honor the small-N flag (below N=5, direction is noise), and
post-mortem misses by pipeline step. Recalibration adjusts *future shading* (e.g. "shade
high-confidence calls down"), never past records or the scorer.
