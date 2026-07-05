# Design rationale — why the scaffold looks like this

Every structural choice below is tied to published evidence. Where the evidence will rot (model
rankings, cost figures), the design responds by putting the perishable part in config.

## The pipeline skeleton

Operationalize → research → outside-view-first reasoning (the spine anchors on a reference
class before case specifics — a property of every draw, not a lens assignment) → independent
draws → pool → blend with
crowd → validate/clamp → record → resolve → score. This shape is stable across every serious
system from the first near-human LLM forecaster (Halawi, Zhang, Yueh-Han & Steinhardt,
*Approaching Human-Level Forecasting with Language Models*, [arXiv:2402.18563](https://arxiv.org/abs/2402.18563))
through the 2025–26 Metaculus tournament winners.

Key results the skills encode:

- **Retrieval is the largest single gain** (~0.02 Brier in Halawi's ablations), and multi-source
  research was the strongest correlate of tournament performance in the Metaculus Fall-2025 bot
  survey (r ≈ 0.42). Hence `references/research.md`'s two-independent-sources rule.
  The same survey series found *native* agent web search now beats bespoke retrieval pipelines —
  hence the scaffold has **no retrieval provider**: it uses whatever the host agent has.
- **Reference classes first.** In the Good Judgment Project's randomized training experiments
  (Chang, Chen, Mellers & Tetlock 2016), a <1-hour module improved Brier 6–11% every year, and
  the comparison-classes component drove most of the gain. Hence the outside view is mandatory at
  every tier.
- **Status-quo weighting** and zeroth/first-order anchors: short-horizon world states are sticky;
  event questions resolve NO far more often than models say YES (platform YES base rate ≈ 35%).
- **Granularity is signal.** Friedman et al. (888k GJP forecasts): rounding to the nearest 10% (or
  even 5% for the best forecasters) measurably worsened accuracy. Hence the 1%-granularity rule
  and the round-number/50% warnings in `fsj.py validate`.
- **Prompt wording barely matters — and Bayes-ritual prompts hurt.** A 38-prompt study
  ([arXiv:2506.01578](https://arxiv.org/abs/2506.01578)) found most prompt tweaks negligible,
  base-rate references slightly positive, and "reason like a Bayesian" instructions strongly
  *negative*. Hence `references/reasoning.md` encodes Bayesian discipline structurally (base rate
  first, evidence clustered by independent source, expected-evidence self-test) and bans the
  ritual arithmetic.

## Aggregation

- **Trimmed mean over ~5 varied draws** for one forecaster's own ensemble: beat mean, median, and
  geometric mean in Halawi et al., and was naturally well calibrated. Even running an agent twice
  measurably improves Brier (FutureSearch's run-agents-twice result).
- **Geometric mean of odds, dropping the single most extreme forecast on each end**, for genuinely
  independent forecasters: the documented aggregation rule of the best-track-record human team
  (Samotsvety — 1st, CSET-Foretell/INFER 2020–22), theoretically preferred (external Bayesianity;
  Sevilla, *When pooling forecasts, use the geometric mean of odds*) and empirically supported
  (Satopää et al. 2014).
- **Never extremize a self-ensemble**: extremizing corrects for forecasters sharing only part of
  their private information — one model's repeated draws share ~all of it. (Extremizing also
  underperformed plain pooling on later Metaculus data.)
- **Blend with the crowd**: a simple average of a good system with the market/community number
  beat both alone (Halawi et al.: 0.146 vs crowd 0.149; Schoenegger et al., *Wisdom of the Silicon
  Crowd*, Sci. Adv. 2024). 41% of Fall-2025 tournament winners sanity-checked against the
  community median before submitting; 0% of non-winners did.
- **Cross-model ensembles beat same-model resampling** because frontier models' errors are heavily
  correlated (Mantic × Thinking Machines, *Training LLMs to Predict World Events*, 2026) — hence
  the high tier prefers independent subagents/models when the host provides them.

## Effort tiers, and why `auto` is the default

The cost–accuracy Pareto frontier is shallow: in FutureSearch's Deep Research Bench, roughly 2×
cost bought ~2 points of accuracy, and most agent runs cost well under $1. So tiers are justified
by *stakes and value-of-information*, not by hoping high effort buys big accuracy — which is a
triage judgment, made once, cheaply, up front. The auto rubric generalizes an
effort-scaled-to-stakes design (stakes × reversibility × uncertainty → tier) from the
decision-scaffolding system this project was spun out of. Tier *numbers* (draws, searches) live
in `config/forecast.toml`.

## Model-agnosticism as the survival strategy

Two findings, held together, dictate the architecture:

1. Good scaffolding is worth roughly **9 months of base-model progress** (Metaculus FutureEval
   analyses) — a scaffold is genuinely valuable.
2. **Model choice dominates scaffolding**: Metaculus's own fixed-prompt template bot with the
   newest model placed top-6 four quarters running while pinned-model bots collapsed (a top-4 bot
   fell to rank ~44 as its model aged).

So the scaffold never names a model in a skill, never ships a retrieval provider, and keeps every
tunable in config. The components that stay valuable as base models improve are exactly what this
repo is: the evaluation harness, the question-hygiene and aggregation discipline, output
validation/repair, and the record→resolve→score loop. The components being absorbed into base
models (elaborate reasoning prompts, tool orchestration) are exactly what this repo doesn't have.

## The learning loop

Fast-proxy decomposition (record short-horizon sub-questions of slow questions) manufactures
calibration bandwidth — many clean, quick signals instead of one slow ambiguous one. GJP's
"perpetual beta" finding (commitment to updating predicted accuracy ~3× more strongly than
intelligence) is why `calibrate` ends with a post-mortem that tags **which pipeline step failed**,
not just a score.

## Numeric CDF handling

Ported (with attribution) from the MIT-licensed
[Metaculus/forecasting-tools](https://github.com/Metaculus/forecasting-tools), because format
violations reject ~a third of naive numeric submissions and the constraints are exacting
(201-point monotone CDF, 5e-05 minimum step, 0.2 per-bin mass cap, open-bound tail masses).
Battle-tested code was ported rather than reinvented; `fsj.py cdf` + `validate_cdf` implement the
build-validate-repair loop.
