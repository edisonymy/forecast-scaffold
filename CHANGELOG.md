# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/)
and mirror `.claude-plugin/plugin.json`.

## [0.4.2] - 2026-07-06

### Fixed
- **The bot-crowd anchor is removed from production briefs.** The Metaculus API
  firewalls the human community prediction from bot accounts everywhere — every value
  `run_bot` can fetch is an aggregate of *other competing bots*, and sighted mode was
  injecting it into the brief as "## Community prediction". Measured harm in the e2e
  runs: the sandbox bot-crowd said 0.63 on Dems-House-plurality while real markets sat
  ~0.82, and the injected anchor pulled a sighted run from 0.79 (blind, ≈ the market)
  to 0.72 — toward the bots, away from the money. The Halawi crowd-anchor evidence is
  about human crowds and does not validate anchoring on competitors. Now: the fetched
  value is journaled as a benchmark only (`shown_to_agent: false`, source relabeled
  "metaculus bot aggregate"), and sighted briefs instead tell the agent that finding
  real human markets (Polymarket, Kalshi, Manifold, public Metaculus) is part of
  research. Blind mode is unchanged.

## [0.4.1] - 2026-07-06

Fixes from the first live end-to-end runs of v0.4.0 (4 real questions: bot-testing-area
sighted medium + the live FutureEval question blind at high and medium, all dry-run).
The pipeline itself ran clean — dossier → verification → `named_scenarios`-compliant
reasoning runs → untrimmed pool → v0.4-stamped journal records, zero failures.

### Fixed
- **`--agent-cmd` default was a footgun**: bare `claude -p` returns no JSON envelope (cost
  and model silently record as nothing) and applies no `--allowed-tools` hardening — one
  bare run did ZERO web searches where the production command did seven on the same
  question, and the blind answer moved 0.34 → 0.66 on evidence access alone (live
  corroboration of issue #9's evidence-threshold hypothesis). The local default now
  mirrors bot.yml's hardened production command exactly.
- **Scenario-coherence flag gets 0.05 slack**: the live runs flagged a 0.25-vs-0.24
  "violation" — rounding noise, not the named-then-unpriced failure the check hunts
  (audited real cases look like 0.14 named vs 0.03 priced). Contract wording also now
  asks for roughly mutually exclusive, opposite-direction pathways only.

## [0.4.0] - 2026-07-06

The lean-aggregation release. Rolled out on explicit owner decision on plausibility plus the
PR #11 tail audit (see the issue #10 comment trail), ahead of the preregistered #8/#9
batteries: the audit found **no** gross outer-bucket overconfidence on resolved outcomes (the
arbiter extension's motivating premise), found the extreme-drop trim pushing toward the one
weak real signal (the 0.75–0.90 shoulder), and found the zero-shot ablation showing the same
tail profile as the full harness. Principle adopted in `docs/design.md`: **the harness owns
what each context sees; the agent owns what to think.**

### Removed
- **Crux arbiter** (v0.3.0's disagreement-triggered override). It never fired in the 4-case
  regression; its probability-space trigger (spread > 0.15) is structurally blind at the tails
  (0.02 vs 0.10 is a 5× odds disagreement but a 0.08 "spread"); and on firing it replaced the
  pool with one context's number at exactly the highest-stakes moments. The pool is the
  aggregator; disagreement stays visible in `raw_draws`.
- **Extreme-drop trim in `geo_mean_odds`** — now opt-in (`drop_extremes=False` default). A
  rank-symmetric trim is logit-asymmetric near the boundary: measured on the repo's own cases
  it moved one-sided pools *toward* the extreme ([0.03, 0.03, 0.05, 0.12]: 0.049 → 0.039;
  the both-chambers pool: 0.239 → 0.229, deleting the market-closest draw), and at n=4 it
  kept only the middle two draws. `median` remains the contamination fallback.

### Added
- **`named_scenarios` in the reasoning-run contract + an arithmetic-only coherence flag.**
  Each reasoning run must disclose the pathways it considered to the opposite resolution from
  its lean, with the mass it actually assigns ([] is honest); the harness flags — never
  overrides — a forecast that leaves less room than the mass its own run named. The audited
  tail failure was precisely "named the scenario, didn't price it"; support theory (unpacking
  an implicit residual raises its judged probability) predicts the disclosure alone moves
  tails the right way. Zero extra agent calls. Flags land in the journal reasoning note.

### Changed
- **Lenses are suggestions, not assignments** — a reasoning run may swap its angle for a
  better one (harness = convenience, not railroading). The counter-biasing opposite pair
  moved to the front of the rotation so lean run counts stay directionally neutral at k ≥ 2.
- **Leaner tiers:** medium 4 → 3 runs (research + 2 reasoning), high 6 → 4 (research + 3).
  The measured BTF-2 null (harness − zero-shot = +0.0002 ± 0.0148, n=85) says reasoning
  multiplicity wasn't paying for itself; the diversity lever that remains is cross-model
  `run_models`.
- Prose reason-gates in the skill references reframed as decision aids and paired with the
  price-what-you-name discipline (the binding checks are now schema-level, where every
  previously-proven win in this project lives).

### Validation debt (deliberate)
This version shipped on plausibility by owner decision (2026-07-06), not on the
resolved-Brier gate the repo normally requires. Issues #8/#9 remain the validation vehicle —
run their arms on v0.4.0, plus the 4-case regression, before re-enabling the tournament cron.

## [0.3.0] - 2026-07-05

The architecture-review release (see the loop-architecture review artifact + issues #7-#9):
reallocates effort from reasoning multiplicity toward evidence quality, contract discipline,
and the scoring loop. The tournament cron remains disabled pending the issue #9 experiment.

### Changed
- **`crowd_weight` 0.5 → 0.8.** The 0.5 default misquoted its own source: Halawi et al.'s
  validated optimum is "4x weight for the crowd" (Brier .149 → .146). Known-value tests and
  docs updated; staleness rule added to the crowd section (a crowd number is evidence as of
  its timestamp).
- **Reasoning runs may now fill evidence gaps** (owner decision): up to 2 targeted searches,
  dossier-first, blind domain blocks still apply — instead of the v0.2.x hard web strip.
  Matches production practice of interleaving acquisition with reasoning.

### Added
- **Premise verification (CoVe-shaped)**: after the dossier is written, its 1-3 load-bearing
  premises are re-checked as isolated questions (blind to any draft, one search each, ≤4
  items — the measured optimum) and the verdicts are appended so every reasoning run sees
  them. Non-fatal, budget-guarded. External receipts: CoVe 23-28% relative error reduction;
  FEVER: retrieval-coupled checks beat introspection ~4:1.
- **Disagreement-triggered crux arbitration**: when the pooled draws spread more than 0.15,
  one arbiter run sees the draws + rationales (it is the aggregator — that is its job),
  identifies the crux, resolves it with ≤3 searches, and overrides the pool; the journal
  records both (`aggregation: "crux_arbiter(spread=…) over geo_mean_odds(runs=…)"`) and
  keeps raw_draws. The shape FutureSearch's supervisor and No-Stream's conditional stacking
  converged on: extra research only where the ensemble located genuine uncertainty.
- **Fast proxies for slow questions**: binary questions resolving >180 days out ask the
  research run for up to 2 journal-only sub-questions that resolve within ~8 weeks
  (`parent_id`/`fast_proxy` linkage) — calibration bandwidth for the scoring loop.
- **`bench/evidence_ablation.py`** (issue #9's experiment, ready to run): inverted BTF-2 —
  same questions, same zero-shot reasoning, dossier served at four quality levels
  (full/half/stub/none) on a cheap parametrically-clean model. Decides whether evidence
  quality is a cliff, a slope, or flat on this corpus.

## [0.2.3] - 2026-07-05

Hardening release from a four-perspective adversarial review (code correctness, methodology,
operations, spec coherence) before any live tournament use. The FutureEval cron stays disabled
until a resolved-Brier lens battery (issue #8) passes.

### Fixed
- **Lens/model assignment no longer repeats after a failed reasoning run**: the index came
  from the success count, so any transient failure handed the same lens (and model) to the
  next slot, silently collapsing ensemble diversity. Now a per-attempt slot counter.
- **Pooled records no longer narrate the wrong number**: when pooling changes the submitted
  probability, the journal/comment reasoning gains an explicit pooling note (previously the
  text argued for the research run's own draw, not what was submitted).
- **`missing_evidence` from reasoning runs reaches the journal** (was requested from agents
  and silently dropped); dossiers are capped at 8,000 chars before re-embedding.
- **Comment-posting failures no longer fail the question** (they used to exit nonzero and
  re-run the whole remaining batch on the paid fallback provider); Metaculus reads and
  forecast submission retry on 429/5xx/network blips (comments deliberately don't).
- **Reasoning-only system prompts no longer contain contradictory draw instructions**: the
  tier line asked for an in-context draw ensemble the harness discards; in multi-run mode it
  now asks for one probability.
- Journal is preserved as a private CI artifact when the leak-guard blocks a push (a blocked
  push used to discard it — a hole in the public preregistration trail).

### Changed
- **Wall-clock deadline** (`--deadline-minutes`, set to 85 in the hourly workflow): the
  dollar budget is blind to hung calls (a timeout costs $0), so time itself is now capped
  between questions and between run slots; triage and reasoning-only runs get short
  timeouts (300s/600s) instead of the research run's full leash. The per-invocation budget
  is also now checked between run slots, not only between questions.
- **Tier run counts: medium 3→4, high 5→6**, so pooled n ≥ 4 wherever pooling happens —
  `geo_mean_odds` only drops extremes at n ≥ 4 (it was silently untrimmed at the old
  medium default) — and every tier's lens prefix now contains a counter-biasing pair.
- **Lenses re-worded neutrally and re-ordered** (reference-class check, opposite-down,
  opposite-up, decomposition, premortem): each names both failure directions and pre-judges
  nothing about the dossier. **Correction to 0.2.2's framing**: that change was a lens
  *selection* change, not a reorder (pool order is commutative; at the old medium tier only
  the first two lenses ever ran), its evidence was one question with arguably leading
  diagnostic wordings, and "landing nearer other LLMs" is not validation (it measures shared
  prior). Whether method lenses beat attitude lenses is preregistered as an open question
  (issue #8) to be decided on resolved Brier only.
- Reasoning runs' system prompt names the dossier as untrusted third-party-derived data.

## [0.2.2] - 2026-07-05

### Changed
- **Method lenses replace attitude lenses at the head of the ensemble** (`LENSES`,
  `aggregate.md`): a live paired test on one question (issue #7 comment) found the v0.2.1
  attitude lenses (outside-view / inside-view / steelman) all inherited the shared dossier's
  prominently-placed unconditional base rate and clustered within 5 points of it, while a
  reference-class-check lens and a decomposition lens moved 2-3x further (0.19/0.27 vs
  0.06-0.11) on the same dossier. Anchors propagate through evidence, not just estimates.
- **Dossiers must class their base rates** (`DOSSIER_SECTION`, `aggregate.md`): every base
  rate carries the class it is computed over; when a conditioning variable is already known,
  the conditional or component rates are mandatory — a single broad unconditional rate is an
  anchor wearing a source citation.

## [0.2.1] - 2026-07-05

Ensemble mechanics rebuilt around the shared-dossier / independent-reasoning structure used by
the best published pipelines (Halawi et al. 2024 share one retrieval across all reasoning calls;
IDEA protocol and Samotsvety share evidence, then estimate privately; Davis-Stober et al. 2014:
the harmful correlation is seeing each other's *estimates*, not sharing *evidence*).

### Changed
- **Bot pooled runs no longer duplicate research.** The first run researches and emits an
  estimate-free `dossier` (no probability, no lean — anchoring guard, enforced by a repair
  retry); the remaining runs are reasoning-only on that dossier in separate contexts with web
  tools stripped at the CLI level (`reasoning_only_cmd`), each under one of five assigned
  analytical lenses (outside-view / inside-view / consider-the-opposite ×2 / premortem, in
  counter-biasing pairs). Pooled with unextremized `geo_mean_odds` (Satopää: information
  overlap ≈ 1 ⇒ extremizing factor ≈ none). Cuts multi-run cost roughly in half.
- **Draws are lens-diverse, not scenario-conditioned** (`aggregate.md`, SKILL.md Step 4): every
  draw estimates the same unconditional P(X) from a different starting frame. The v0.2.0
  wording ("assume your premortem story actually happens") produced P(X|scenario) draws, and
  pooling conditionals as estimates of P(X) is a category error — fixed. Subagent fan-out on a
  shared dossier is now the *default* Step 4 mechanism on Task-capable surfaces (Claude Code,
  Cowork), with in-context draws demoted to the degraded mode.
- **Extremes gate is a reason gate, not a floor** (`reasoning.md`): sub-5%/above-95% still must
  name the blocking mechanism, but the ~10/90 floor language for political questions is gone —
  Q4 AIB data shows bots lost more to timid tails (7% where Pros said 2%) than reckless ones,
  and rounding skilled forecasters' tails measurably hurts (Friedman et al., 888k forecasts).
- **Resolver risk is first-class in question hygiene**: undefined subjective predicates
  ("a suit", "an invasion") are resolver risk, not event risk — forecast the text under the
  resolver's likely reading, think P(event) × P(faithful resolution | event).

### Added
- `tiers.*.run_models` (config): optional model ids the harness cycles through for runs after
  the first — cross-model diversity is the strongest documented ensemble lever (tournament
  winners average ~1.8 model families). Default empty.

## [0.2.0] - 2026-07-04

Also in this release (missed at the 0.2.0 cut): the BTF-2 pastcasting bench
(`bench/fetch_btf2.py`), `--budget` caps on bench/bot, resolution scoring in the bench report,
`score --by` grouped Brier anchoring, six audit-driven skill changes (issue #6), harness-side
pooled independent runs, and the hourly FutureEval tournament cron.

### Added
- **OpenRouter provider** (`--provider openrouter` in `bot/run_bot.py` and `bench/run_bench.py`):
  routes the same `claude` CLI through OpenRouter's Anthropic-compatible endpoint (billed to
  OpenRouter credits), with automatic `anthropic/<id>` model-slug rewriting; `bot.yml` uses it
  as an automatic fallback when the subscription step fails. New optional `provider` field on
  `ForecastRecord` (additive, no schema bump).
- **Internal tier-distillation benchmark** (`bench/`): frozen question sets built from
  ForecastBench's public market questions (crowd probability included; Manifold/Polymarket
  refreshed live), paired blind runs of `low`/`medium`/`high`/`auto`, and a report scoring each
  tier's distance to the crowd and to the `high` tier (|Δp|, RMS, KL, |Δlogit|) per dollar.
- **`bot/crowd.py`**: reads the human community prediction with a personal-account token
  (`METACULUS_CP_TOKEN`) for offline measurement — Metaculus firewalls bot accounts from the
  human crowd on public questions, and this stays deliberately outside the forecasting loop.

### Changed
- **Effort tiers are now harness-enforced**: `[tiers.*]` config gains `runs` (independent
  agent runs pooled with geo-mean-of-odds by bench/report; low 1 / medium 3 / high 5) and
  the tier's `draws`/`searches` are inlined into the bot-mode system prompt. The baseline
  showed in-context draw instructions are under-executed headlessly (all tiers ≈3
  correlated draws; tier gaps = rerun noise). Surfaces without independent runs degrade
  to in-context draws and the skill now says so out loud.
- **Benchmark contracts are verbatim-or-excluded**: set briefs carry the exact resolution
  terms fetched from the source platform (Polymarket Gamma description, Manifold creator
  description, Metaculus criteria + fine print via API); INFER is excluded by default
  (login-walled terms). Live crowd values gain liquidity floors (Polymarket ≥ $10k volume,
  Manifold ≥ 20 bettors).

### Fixed
- The subscription provider path now drops inherited `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`
  endpoint overrides and empty `ANTHROPIC_API_KEY` artifacts, so ambient shell config cannot
  silently redirect or break the agent; agent failures now surface the stdout error envelope,
  not just (often-empty) stderr. `run_bot` exits nonzero when any question fails, enabling
  workflow-level fallback.
- OpenRouter provider on a machine with a cached `claude` login: the CLI ignores env auth
  when a cached OAuth account exists (requests reached OpenRouter with no auth header).
  The openrouter path now runs the agent under a dedicated empty `CLAUDE_CONFIG_DIR`.
- Benchmark lessons from the 2026-07-04 baseline run, all in `bench/`:
  Polymarket/INFER questions carried the literal criteria string "N/A" (their contract
  lives in `background`) — `build_criteria` now says so explicitly and background is no
  longer truncated at 4k; stale freeze-time crowd values indicted a correct forecast (the
  market had since been decided by SCOTUS), so `--refresh-crowd` now drops questions that
  can't be confirmed live or that trade at extremes; the auto tier defaults to
  router-only (+report-side imputation from the routed tier); rows now record
  `raw_draws`/`n_draws`, `reasoning`, and `duration_s` so tier compliance is auditable;
  the report adds median/bias columns, a per-source table with crowd freshness, and a
  run-to-run repeatability section; `bench/README.md` gains a preregistration/dev-holdout
  iteration protocol.

## [0.1.0] - 2026-07-03

### Added
- **`forecast` skill**: effort-tiered forecasting pipeline (`auto`/`low`/`medium`/`high` with an
  auto-triage rubric) with progressive-disclosure references: question hygiene, research
  protocol, the reasoning spine (reference-classes-first, structured debiasing), decomposition
  and fast proxies, aggregation rules, and multiple-choice/numeric/conditional handling.
- **`calibrate` skill**: the learning loop — resolve due forecasts, Brier score with direction of
  miscalibration, post-mortems tagged by pipeline step.
- **`forecast_scaffold` Python core** (zero dependencies, single file, vendored into each skill):
  ForecastRecord schema + append-only JSONL journal with idempotent resolve/annul; Brier +
  folded-confidence calibration report; trimmed-mean / geometric-mean-of-odds / median pooling
  with crowd blending and clamping; validators for probabilities, percentiles, and
  multiple-choice sets; percentile→CDF construction with platform-rule repair (ported from
  MIT-licensed forecasting-tools, attributed); CLI
  (`record | resolve | due | score | aggregate | validate | cdf | export | config`), including
  `export --format decision-record` interop.
- **Metaculus tournament bot** (`bot/`): stdlib API client, headless harness driving the same
  `forecast` skill with auto-effort triage and a validate-and-repair output contract, public
  committed journal; GitHub Actions workflows for dry runs and the 20-minute tournament cron.
- **Evals**: behavioral scenarios + pure graders (`scripts/behavioral_evals.py`,
  `evals/scenarios.json`) testing conduct (journal side effects), not wording.
- Plugin + marketplace manifests (installable via
  `/plugin marketplace add edisonymy/forecast-scaffold`), claude.ai skill bundles
  (`scripts/build_skill_bundles.sh` → `dist/*.zip`), CI (tests on Python 3.11/3.12, lint, strict
  types, vendored-copy sync check, personal-data leak guard, plugin validation), and docs
  (schema spec + DecisionRecord mapping, sourced design rationale, evaluation protocol).
