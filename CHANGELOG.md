# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/)
and mirror `.claude-plugin/plugin.json`.

## [0.4.21] - 2026-07-12

### Added
- **Hourly Manifold cloud runner with a hard subscription-credit boundary.** The scheduled
  workflow is Claude-OAuth-only (OpenRouter and metered/gateway auth are rejected), caps
  every run at $5 USD-equivalent usage both cumulatively and through Claude's native
  remaining-budget flag, reserves unknown usage on failures/timeouts, and stops model work
  after 45 minutes so the journal can publish before the next tick. The Manifold API key is
  required before model spend; the existing mana exposure/floor/position gates still bind.
- **Research-mechanics telemetry for future benchmark A/Bs.** Each time-locked forecast gets
  a private content-free MCP event sink; rows now carry `n_searches`, `n_full_reads`, bounded
  exact queries, and model-declared source classes. Angle subruns aggregate telemetry into
  their pooled row, and concurrent forecasts cannot commingle logs.
- **Deadline-move preregistration.** A manually audited census partitions all 152 admissible
  BTF-2 questions into 92 tagged development questions, 10 exact motivating holdouts, and 50
  non-fired controls. The experiment-only move fetches official status/dockets, enumerates
  remaining steps, does window arithmetic, and checks institution-specific slippage; no live
  production prompt changed and no paid A/B has run.

### Fixed
- Tranche1 analysis now screens and scores only the preregistered `run == 0` cells. The
  original resume command accidentally let the configured high tier expand to four runs;
  six paid nonzero-run rows remain preserved as unused raw data, while `--max-runs 1`
  restores the intended 40 questions x 3 arms = 120-cell design.
- `memory_screen.py` accepts arbitrary result files and a run filter; the tranche readout
  rejects duplicate cells and accepts repeatable pairwise memory exclusions.

### Measured / diagnostic
- A literal teacher-cited-page recall audit is not reconstructible from the public BTF-2
  release (no page-read trace or citation-to-URL map). On a frozen first-20
  question-source-set proxy, production-global search surfaced a linked source for 18/20
  questions (90%, Wilson 95% CI 70-97%), equal to question-scoped retrieval. Only 50.6% of
  linked URLs were eligible under the production crawl-time cutoff on average, so corpus
  discovery is not broadly broken but load-bearing recall remains unverified.

## [0.4.20] - 2026-07-11

### Added
- **AskNews as an optional, competition-scoped research source** (`bot/asknews.py`):
  when a key is present (env or `~/.asknews/key[.txt]`), the tournament bot's research
  and angle runs get a "Recent news" section — hot (`strategy=latest news`, 6 articles)
  + historical (`news knowledge`, 10) passes, deduped, dated, capped, and explicitly
  labeled "starting material; verify key claims and search beyond it" (the measured
  lesson: digests reduce research agency — this seeds, never replaces, self-directed
  search). No preliminary lean is ever injected (the reference bots' anti-pattern).
  Ships dark: no key = byte-identical briefs. `ASKNEWS_DISABLE=1` kill switch.
  **Key usage terms enforced structurally**: the key is licensed for the Metaculus
  competition only — `run_manifold` has no asknews import and a compliance-guard test
  keeps it that way. bot.yml passes `ASKNEWS_API_KEY` through (absent secret = off).
  Suite-wide conftest defaults AskNews off in tests so a developer's keyfile never
  leaks live calls into CI or local runs.

## [0.4.19] - 2026-07-11

Post-hoc logistic recalibration (Platt scaling) — the highest-value portable lever from a
sweep of the Metaculus bot ecosystem (their own analysis: Brier −0.016 binary; independent
test on our pastcast data: 0.1997→0.1761, question-level CV, fitted logit slope 0.573 =
opus-4-6 overconfident on hard-news questions).

### Added
- `fsj calibrate-fit`: fits a 2-parameter logistic map `sigmoid(a·logit(p)+b)` from the
  journal's own resolved binary forecasts, refuses to emit unless n ≥ 40 AND out-of-sample
  5-fold CV improves, and writes `bot/journal/recalibration.json`. `fit_platt`,
  `apply_recalibration`, `recalibration_cv`, `load_recalibration`, and the unwired
  `extremize_logodds` (AIA Forecaster's data-free √3 fallback — opposite sign to our data,
  an A/B candidate only) in core.py.
- The bot applies the fitted map to the final pooled binary probability before submission,
  journaling both `raw_probability` and the recalibrated value. **Ships inert**: with no
  params file, load returns identity and the path is byte-identical to before — the
  correction only ever comes from the deployment's OWN resolved history, never a hardcoded
  direction (the live tournament regime can be under-, not over-, confident; the sign is
  not portable across model/distribution). `docs/schema.md` updated for `raw_probability`.

## [0.4.18] - 2026-07-11

The research-side answer to "why does FutureSearch beat frontier models" — their own
paper and a first-party ablation say: research agency (Opus loses 0.022 Brier when denied
its own search: 0.131→0.153, their measurement) plus evidence-diverse ensembling (+0.005)
plus a strategy stack (+0.006). Two pieces shipped toward that:

### Added
- **Corpus-backed discovery for the vault** (committed separately as `b407635`): an
  8,025,921-row FTS5 index over FutureSearch's published BTF-2 scrape manifest —
  ranked, date-stamped, question-linked URL discovery, with content still flowing
  through the time-locked Wayback fetch (the manifest ships no page bodies).
  RetroSearch-lite: corpus for finding, archive for reading. `timevault_mcp --corpus`,
  `run_bench --corpus`.
- **Angle-diverse independent research** (`run_angles` tier knob, ships dark): when set
  (e.g. `["F","D","A"]`), a tier runs one INDEPENDENT full-research run per angle from
  `skills/forecast/references/research-angles.md` — fundamentals (market-blind by
  design, even in sighted mode), decomposition, anomaly hunt — and pools with
  geo-mean-odds, journaling per-angle probabilities. Measured why: dossier-sharing runs
  disagree by only ~0.03, so their pool equals the member average; FutureSearch
  transcripts show members that research independently with assigned angles and
  deliberately different information diets. Evidence diversity is the pooling
  prerequisite. The method text ships as skill markdown (portable); the harness only
  orchestrates.

## [0.4.17] - 2026-07-11

### Added
- **Manifold Markets bot** (`bot/run_manifold.py`, `bot/score_manifold.py`) — the
  days-scale feedback channel. Selects liquid binary markets (>=50 bettors, 3-60d close,
  volume-ranked, meme/self-referential excluded, topic-diversity cap), forecasts each
  BLIND and SIGHTED in the same run (blind blocks manifold.markets; sighted carries the
  price under the v0.4.11 judgment framing and must return a checkable
  `market_read` ∈ informed|herding|thin|stale — only non-"informed" reads may bet).
  Signals: price movement toward the forecast at t+3/7d, mark-to-market P&L, resolution
  Brier, paired blind-vs-sighted comparison. Journal: `bot/journal/manifold.jsonl`
  (committed preregistration).
- **Operator-approved betting policy with an automatic phase machine**
  (`docs/manifold-policy.md`, `bot/journal/manifold-phase.json`): phase 0 dry-run →
  phase 1 flat 25-mana stakes (<=10 bets/run, exposure <=30% of balance, 1,100-mana
  floor) → phase 2 quarter-Kelly (5%-of-balance cap, convergence exits, adverse-move
  re-forecasts). Promotions and the kill criterion (n>=50 movement sample, exact
  binomial test) are evaluated mechanically each run and journaled with their evidence —
  preregistered phase transitions, no in-the-moment judgment.
- Daily GitHub Actions workflow (`manifold.yml`, 07:30 UTC) mirroring bot.yml's
  commit/leak-guard patterns; without the `MANIFOLD_API_KEY` secret it dry-runs.
- Key lookup: `MANIFOLD_API_KEY` env or `~/.manifold/key(.txt)` — a keyfile outside the
  repo so the credential never enters git or session transcripts.

## [0.4.16] - 2026-07-10

### Added
- **Direct OpenRouter transport for tool-less bench calls** (`bench/direct_agent.py`,
  `--provider openrouter-direct` on the probe and on `run_bench` — the latter guarded
  to exactly `--tiers zero --leakfree none`). Measured motivation: the claude CLI
  prepends ~22,000 tokens of agent scaffolding to every call ($0.066 to say "hello";
  5–10× the cost of the actual probe/arm prompt) and returns an EMPTY result for
  non-Anthropic models through the Anthropic-compat endpoint (gemini-2.5-pro:
  `result:"", output_tokens:16`). The direct transport posts the prompt alone to
  OpenRouter's native API, takes cost from the response's own usage accounting, and
  makes cross-family models (the `run_models` ensemble lever) probeable and runnable.
- `contamination_probe --provider {subscription,openrouter,openrouter-direct}` —
  non-Anthropic models can now be contamination-probed before joining an ensemble.

## [0.4.15] - 2026-07-10

The reasoning-spine A/B harness, and improvement-loop 1's results — the negatives are
the point of preregistering:

### Added
- **`run_bench --spine-file`**: the zero tier (dossier-only reasoning cell) doubles as a
  reasoning-spine A/B harness — same frozen research, no tools, only the method text
  varies. Rows stamp `arm` + `spine_sha` so no results file is ambiguous about which
  prompt produced it. Spine variants live in `bench/spines/`.
- **Memory-claim screen** for pastcast validity: the recall probe under-detects (its own
  documented caveat) — a probe-cleared ECB question surfaced "high confidence as this
  event has already occurred" mid-forecast (a confabulated memory: it "remembered" a cut;
  the ECB held). Screen = mechanical regex shortlist over all arms' reasoning, judged by
  reading, excluded pairwise. One row in ~350 flagged.

### Measured (loop 1, opus-4.6 on 152 probe-admissible BTF-2 questions, frozen dossiers)
- **Premortem/perspectives/wildcards spine: null** (−0.0003 ±0.0068, n=47) — it hedges
  (REL and RES both drop) rather than redistributing mass.
- **Source-skepticism spine: null** (+0.0037 ±0.0046, n=152) — tranche-1's promise
  (−0.0076, n=47) regressed on fresh questions; it over-discounts on-schedule
  institutional events (elections held as scheduled, enforcement that landed).
- **Extremization of single-run outputs: negative** — train-optimal d=1.0; the test-set
  Brier curve worsens monotonically in d. Single-run opus is not underconfident.
- **Method-diversity ensembles (geo-mean-odds over spines): null** (+0.0013 ±0.0024).
- Baseline gap to the FutureSearch ensemble teacher on identical frozen research
  [CORRECTED same day: the BTF-2 dataset card states the SOTA forecast was made from
  their full frozen scraped corpus, independent of the research_summary digest our
  briefs carry — so this gap confounds evidence access with reasoning; "identical
  frozen research" was wrong]:
  +0.0197 ±0.0218 mean, but the teacher wins 106/152 per-question — a small, consistent
  refinement edge (RES 0.111 vs our 0.042) that prompt text did not close. Next lever:
  cross-model ensembling (`run_models`, documented but never exercised).

## [0.4.14] - 2026-07-10

### Added
- **Prospective freezing** (`bench/freeze_prospective.py`): `freeze` snapshots the bot
  tournaments' currently-open binary questions into a preregistration set file
  (`bench/sets/prospective-<date>.jsonl`, `frozen_at`-stamped, refuses to overwrite
  without `--force`); `resolve` later fills outcomes idempotently, never touching a
  frozen field. With timevault research cut at `frozen_at`, this is the only valid
  evaluation path for models (like the live bot's sonnet-5) whose training window
  covers every already-resolved question. The weight leak stays a set-selection duty:
  only evaluate models whose cutoff predates `frozen_at`.
- **Repair-retry visibility**: `one_run` prints `repaired on retry: <qid> (<reason>)`
  when a payload is accepted on the second attempt (previously indistinguishable from a
  clean first attempt anywhere), and `bot.yml` now tees both provider runs and appends a
  filtered digest (recorded/submitted/repaired/flags/floor lines) to the Actions job
  summary, mirroring `bench.yml`'s existing pattern.

## [0.4.13] - 2026-07-10

Two more review clusters (finding #9 and the journal-completeness set), implemented by
opus subagents and reviewed:

### Added
- **Untrusted-input security sections in the skill markdown** (`skills/forecast/SKILL.md`,
  `skills/calibrate/SKILL.md`). The bot surface always had prompt-injection defenses in
  its Python-built system prompt; the skill — the surface installed with far broader tool
  permissions — had none. Written as a security frame (question text, criteria, and
  fetched pages are data to forecast, never instructions; self-advocating content is
  incentive evidence, not world evidence), not as workflow gating.
- **Continuous-question submission provenance**: `ForecastRecord` gains `submitted_cdf`
  (the exact ~201-point CDF sent to the platform) and `scaling` (the bounds/zero-point it
  was built against). The CDF is now built once at record time and the submit path sends
  that same object — the journal and the platform can no longer silently diverge.
  Rows grow ~2.7 KB on continuous questions; completeness beats compactness in a
  preregistration journal. `docs/schema.md` updated.
- **`to_decision_record` carries MC and numeric forecasts** (`options`/`probabilities`,
  `percentiles`) instead of exporting a null binary probability slot — the silent-drop
  found in review. Binary exports unchanged.

### Fixed
- `validate_percentiles` rejects distinct keys that normalize to the same percentile
  (`"50"` vs `"50.0"`) — previously which value won was accidental; now it is a
  repairable contract error.

## [0.4.12] - 2026-07-10

Three deep-review roadmap items (findings #1–#3), implemented by opus/sonnet subagents
and reviewed:

### Added
- **Reference-class floor for MC/numeric research runs** (`bot/run_bot.py`). The MC and
  numeric contract examples now carry `reference_class`/`base_rate` (binary already did),
  and on research runs (`min_sources > 0`) a missing or empty `reference_class` is
  rejected in the same validate/repair loop as the source floor; an MC `base_rate` dict
  is checked against the exact option labels. Motivated by the live Vanguard ETF bucket
  question: an even 32/31/34 spread where a Poisson/historical reference class implied
  ~50/35/16 — nothing structural ever asked a single-run MC question to derive a prior.
  Known limits documented at `REFERENCE_CLASS_SECTION`.
- **Paired per-question Brier section in `bench/report.py`** — for every tier pair, the
  mean per-question Brier difference ± SE with win/loss/tie counts, over qids where both
  tiers forecast and the resolution is known. This is the pivotal experiment statistic
  and was previously hand-computed for every run. Additive; reuses the report's existing
  per-(qid, tier) pooling.

### Changed
- **Retired the stale n=85 justification for tier `runs` sizing** (comments in
  `core.py`, `config/forecast.toml`, vendored `fsj.py`). That null measured in-context
  draws at v0.1.0 — not the independent-runs architecture — and the 2026-07 contamination
  probe flagged 8/55 of its corpus, so it neither supports nor refutes current sizes.
  Sizing is now labeled the cost/quality judgment call it is, pending the leak-free
  re-measurement.

## [0.4.11] - 2026-07-10

Reversal of 0.4.10's harness blend, same day, on operator review — kept here rather
than history-rewritten because the reasoning is the valuable part:

1. **Determinism**: 0.4.10 blended sometimes-at-the-harness, sometimes-in-the-agent
   (guard-dependent). Two conditional mechanisms make submitted numbers hard to reason
   about. One mechanism, owned by the agent, always.
2. **Contract equivalence cannot be checked mechanically.** The same-question case is
   easy but rare (Metaculus hides aggregates from bots); in practice the available
   market is a similarly-worded question on another platform, and "similar wording"
   with one differing clause legitimately prices 4x away (the repo's own $386k
   Polymarket/NPM case). Deciding whether a market is THIS contract takes judgment —
   an agent capability, not an arithmetic one.

### Changed
- The bot harness never blends, in any mode. The journal still captures the platform
  aggregate as a benchmark (never shown to the agent — v0.4.2 boundary unchanged).
- The sighted brief's "Crowd signals" section now makes the market scan a REQUIRED,
  disclosed research step: report what was found (including "no market found"), and
  state the contract differences checked before leaning on any market number. Blending
  is explicitly the agent's judgment call.
- `blend.crowd_weight` restored to 0.8 — its remaining consumer is the chat/CLI
  aggregate path where a judged-relevant same-question value is passed explicitly,
  which is exactly what Halawi's 4:1 optimum was calibrated on.

## [0.4.10] - 2026-07-10

Crowd blend gets a real code path — with a double-count guard. The review found
blend_with_crowd was never called anywhere: config's crowd_weight was decorative and
design.md claimed a win the bot could not produce. Operator decision: never blend in
blind/testing modes (they measure own skill); in prod the sighted agent already reads
everything, so the harness may blend — but must not count the crowd twice.

### Added
- **Harness-level crowd blend** on sighted binaries: after pooling, when the platform
  exposes an aggregate at forecast time, submit blend(pooled, crowd, w) — the agent
  still never sees the value (the v0.4.2 boundary holds; blending is arithmetic, not
  anchoring). The journal records the raw pooled number, the crowd, the weight, and
  the submitted blend, so blended-vs-raw is scoreable at resolution.
- **Double-count guard (`market_sourced`)**: if the research run's own source list
  cites a market/aggregator (Polymarket, Manifold, Kalshi, Metaculus, GJ Open, ...),
  the crowd already entered the estimate cognitively — the sighted brief tells the
  agent to blend what it finds — and the harness blend is SKIPPED. Effective crowd
  weight stays ~w instead of compounding to w + a(1-w).
- Blind mode and the bench NEVER blend (mechanical, not prose).

### Changed
- `blend.crowd_weight` default 0.8 -> 0.5: Halawi's 4:1-crowd optimum was calibrated
  on HUMAN crowds; a bot tournament exposes only other bots, of unproven quality. The
  even split is the prior until blended-vs-raw resolution data says otherwise.

## [0.4.9] - 2026-07-10

Re-forecast policy (review finding: the bot forecast each question exactly once while
the tournament scores forecasts over time — observed live as the crowd walking away
from a frozen Vanguard forecast, 55%->62% while the bot sat still).

### Added
- **`--refresh-hours N`**: a standing forecast qualifies for re-forecasting only once
  it is at least N hours old (0 = never, the default — behavior unchanged unless armed).
  The minimum-age condition is the cost gate: the cron fires every 10 minutes and the
  world rarely moves inside an hour, so ungated updates would re-spend on the same
  question every tick. Refreshes queue strictly AFTER never-forecasted questions (fresh
  coverage buys scoring time a standing forecast already has) and spend from the same
  `--budget`. Each refresh appends a new journal record at its own `forecast_at` —
  matching how the platform scores standing forecasts through time.
- `bot.yml` arms it at `--refresh-hours 48` on both provider paths (a dial, not a law:
  at ~15 open questions that bounds refresh spend at ~7-8 skill runs/day worst case,
  inside the per-run `--budget 3`).

## [0.4.8] - 2026-07-10

First fixes from the 59-agent deep review (41 raw findings -> 34 adversarially
confirmed; full roadmap in the review report). The three lowest-risk, highest-value
confirmed bugs, each with a regression test:

### Fixed
- **Pooling/scenario disclosure notes now LEAD the reasoning field** (were appended to
  the tail, where the record's 4000-char head-truncation silently deleted exactly the
  note saying which pooled number was actually submitted — a disclosure that can be
  truncated away is no disclosure at all).
- **Blind-mode denylist now blocks gjopen.com** (Good Judgment Open's actual forecast
  domain; only goodjudgment.io — the consultancy site — was listed).
- **Backtest/dry-run provenance**: ForecastRecord gains `dry_run: bool | None`
  (additive, no schema bump; None on older records = assume live), and `--post`
  backtests now default to a gitignored `bot/journal/backtests.jsonl` instead of the
  public preregistration journal — a debugging run can no longer write records that are
  byte-identical to live submissions into the scored track record.

## [0.4.7] - 2026-07-10

Contamination probe. The one leak timevault cannot close is the model's own weights,
and admissibility turned out to be empirical, not a model-card lookup: the live bot's
model (sonnet-5, training data through Jan 2026) fully covers BTF-2's Oct-Dec 2025
resolutions, and even the original n=85 run's opus-4.6 (stated Aug 2025) sits inside
the +3-4-month effective-knowledge drift the repo's own evaluation.md cites.

### Added
- **`bench/contamination_probe.py`** — asks a model directly, with every tool stripped
  (no --allowed-tools, full --disallowed-tools belt), whether each already-resolved
  question resolved YES/NO, from memory only, with an explicit unknown-over-guessing
  honesty contract. Scores recall accuracy on answered items against the majority-class
  baseline; flags (model, question) pairs as contaminated on confident-correct recall
  (confidence >= 0.75). Interpretation is DIFFERENTIAL by design: a model whose data
  covers the window (positive control) should light up; a genuinely earlier model
  should sit at baseline. Resumable; per-question rows in bench/results/*.probe.jsonl.
- Documented limit: the probe under-detects (latent knowledge a model does not surface
  as explicit memory still shapes forecasts) — 'clean' means admissible, never proven.

## [0.4.6] - 2026-07-09

Leak-proof pastcasting. The bench had two open leak paths that made every pastcast score
suspect: agents ran with LIVE WebSearch/WebFetch on resolved questions (the btf2 brief even
claimed "web access is disabled" — never enforced), and Read/Glob/Grep reached
bench/sets/*.jsonl where each question's RESOLUTION field sits in plaintext. Per
docs/evaluation.md's own standard (date-restricted retrieval leaks the future through
today's rankings — Paleka et al.), no result produced under those conditions is evidence.

### Added
- **`bench/timevault.py`** — time-locked research clients with a machine-verifiable
  no-future-data guarantee, enforced at one choke point (`_assert_pre_cutoff`): Wayback
  snapshots (CDX `to=` bound + post-redirect stamp re-verification — Wayback's nearest-
  capture redirect can otherwise serve a LATER snapshot; raw `id_` bytes gunzipped),
  Wikipedia revisions as-of (`rvstart`/`rvdir=older`, stamp verified), GDELT news
  discovery in a window ending at the cutoff (date-sorted, not relevance-sorted; strays
  re-checked client-side; content routed through Wayback, never the live page).
- **`bench/timevault_mcp.py`** — minimal stdio MCP server exposing the three tools; the
  cutoff rides in the SERVER's argv, so the agent cannot loosen it; tool descriptions
  state the cutoff; LeakError surfaces as tool output, never a protocol crash.
- **`run_bench.py --leakfree {none,timevault}`** — `none` enforces the frozen-dossier
  contract (no research tools at all); `timevault` allows ONLY the vault's MCP tools with
  `--strict-mcp-config` (no other MCP server rides along) and one combined
  `--disallowed-tools` belt covering WebSearch/WebFetch/Read/Glob/Grep/Bash/Write/Edit.
  Per-question cutoffs from the new structured `as_of` field (btf2 fetcher now writes it;
  a regex fallback reads existing sets). Result rows stamp `leakfree` so contaminated old
  results can never be pooled with clean ones.
- Red-team validated live: a haiku agent in the exact harness config, told to determine
  the Nov 4, 2025 NYC mayoral result under an Oct 23 cutoff, could not — Wikipedia
  as-of-cutoff still said `ongoing = yes`, nothing retrievable postdated the cutoff.
- 23 tests: choke-point enforcement, the redirect trap, MCP protocol, cmd wiring.

### Known limits (documented, not hidden)
- The model's own weights: pastcast questions must RESOLVE after the model's training
  cutoff — a set-selection duty the tool cannot enforce.
- Wikipedia title *search* fallback ranks by today's index (content is still as-of).
- GDELT rate-limits (~1 req/5s) and can be flaky; `search_news` degrades to an explicit
  "unavailable" note rather than failing the run — Wayback/Wikipedia carry the guarantee.

## [0.4.5] - 2026-07-08

Research-floor release. An audit of the first live tournament batch (9 questions) found
the under-research pattern lands exactly on the paths with no research structure: MC and
numeric questions are hard-wired single-run, so `need_dossier` never fires and nothing
structural ever asks them to research. The bot's most crowd-divergent calls sat on its
thinnest evidence — q44381 (Florida MC, mode 65-75% vs crowd's 55-65%) recorded with **zero**
sources, q44382 (47% on the lowest bucket vs crowd 20%) and q44511 with two. The fix is
prevention, not a post-hoc gate: the floor is announced in the research run's own prompt
and enforced in its existing validate/repair loop, so a thin run is re-prompted *before*
any forecast is accepted, pooled, recorded, or submitted.

Deliberately NOT done, after review: requiring a dossier + CoVe verification on single-run
questions. The dossier has no consumer when `runs=1` (it exists to feed reasoning runs and
is never journaled), CoVe verdicts arrive after the payload's number is already final, and
"emit a dossier so you research" is the behavior-forcing pattern v0.4.0 measured as a
regression — a source count is a contract field the harness checks in code.

### Added
- **`tiers.*.min_sources`** (low 1 / medium 3 / high 5): floor on DISTINCT actually-consulted
  sources the research (full) run must return. Announced via `SOURCE_FLOOR_SECTION` in the
  research run's system prompt; enforced in `one_run`'s repair loop (`distinct_source_count`,
  deduplicated after trimming so repeating one URL counts once). Reasoning runs are exempt —
  `[]` stays an honest answer where a run works from the shared dossier. Configs without the
  key inherit the defaults; `min_sources = 0` disables the floor.
- Tests: floor repair-retry on the observed q44381 failure class, duplicate-padding
  rejection, failure-ledger path, prompt announcement scoping (research run only,
  reasoning runs never), floor ≤ search budget invariant.

### Changed
- **Output contract**: the MC and numeric example payloads now show the `sources` field —
  the prose demanded it for every question type, but the examples the model pattern-matches
  omitted it on exactly the two types that under-reported. The "empty list is an honest
  answer" sentence is scoped to runs that genuinely retrieved nothing new (reasoning runs),
  with the research-run floor called out.

### Known limit
- A count can be padded with unread URLs. The public journal's per-question source list is
  the audit trail, and the multi-run path's CoVe premise check remains the partial guard;
  no mechanical check makes retrieval honest, it only makes skipping it visible.

## [0.4.4] - 2026-07-06

Post-mortem release for the first scored live miss (q44378 Lovable/DeepSeek/Perplexity
funding: submitted 8%, crowd 31%, cost $2.03). Two compounding failures, both traced to the
brief's single ambiguous timestamp: the agent read `Closes:` (the forecast-lock time) as the
event deadline, shrinking the contract's one-month event window to six days — and then held
8% with ~73 minutes left on the clock it believed, because the brief never told it the
current time. The dossier carried the misread into every reasoning run (draws 0.07/0.08/0.09
— tight agreement around a shared wrong frame), and nothing between research and pooling
re-read the criteria.

### Fixed
- **`build_brief` timestamps**: the brief now states `Now (UTC)` (the agent previously had
  no stated clock at all — the bench's AS-OF header never made it to the live bot) and
  `Scheduled resolution`, and **no longer includes the forecasting-close time at all** —
  when predictions lock is harness bookkeeping, useless for pricing the event, and it was
  the misread's raw material. You can't conflate a timestamp that isn't there.

### Added
- **Event-window line in the dossier contract** (`DOSSIER_SECTION`): the research run must
  state "event window: ___ → ___ per the criteria; as of <Now> ___% elapsed", derived from
  the resolution text — so reasoning runs inherit the correct window, not a misread.
- **Event-window premise in CoVe verification**: `verify_dossier` now receives the contract
  (criteria + timestamps) and the verifier must always check the dossier's assumed window
  against it as a text check; a window narrower or wider than the criteria is CONTRADICTED.
- **Temporal-coherence gate** (`reasoning.md` step 1): mandatory event-window and
  elapsed-fraction lines; the past portion of a window is a research question, not a
  forecast; P = P(already happened, unreported) + P(happens in remaining time). A number
  incoherent with the forecaster's own stated remaining-time arithmetic is the named failure.
- **Close-time ≠ event-window rule** (`question-hygiene.md`): a platform's lock time is
  bookkeeping about the forecaster; the event window comes from the criterion text alone.
- Tests for the new brief lines, the dossier/verify wording, and the version manifests.

## [0.4.3] - 2026-07-06

The tournament-hardening release: a robustness sweep (six-dimension adversarial review +
owner sign-off) before arming the hourly FutureEval cron. **Owner decision:** entering the
tournament now supersedes v0.4.0's validation-debt gate — the #8/#9 batteries stay queued
as in-flight validation while the bot competes; live resolutions are the outer loop anyway.
No forecasting-methodology changes; everything here is ops, honesty-of-record, and security.

### Fixed
- **`score --by blind` mislabeled every v0.4.2 bot record as blind.** v0.4.2 pinned
  `crowd.shown_to_agent` to `false` (correct — the value is never shown), which was also
  the only blind/sighted signal. Records now carry an explicit `blind` field; grouping
  prefers it and falls back to the legacy proxy only for pre-0.4.3 records (correct for
  everything published before the pin). The journal viewer tag follows the same rule.
- **The journal now records exactly the numbers submitted.** The binary band clamp and the
  MC floor/renormalize used to run *after* the record was appended, so the public
  preregistration journal could differ from what Metaculus received.
- **`validate_payload` returned exceptions instead of errors on non-numeric agent values**
  (e.g. `"probability": "likely"`), which skipped the repair retry and failed fixable
  payloads. Same class: optional numeric fields (`base_rate`, `expected_value`,
  `raw_draws`) now degrade to absent instead of crashing record creation.
- **MC payloads with invented option labels** passed validation (only *missing* labels were
  checked), siphoned probability mass, and would 400 at the API after full agent spend.
- **`open_posts` now follows pagination** up to `--limit`: the already-forecasted filter is
  client-side, so a single-page fetch silently hid new wave questions once more than a
  pageful of posts was open.
- **Transient-retry coverage**: Cloudflare origin blips (520/522/524) retry like 5xx, and a
  parseable `Retry-After` is honored (capped at 30 s).

### Added
- **Free skips before any agent spend** for questions the bot cannot submit: unsupported
  types, closed/upcoming group subquestions, continuous questions without numeric bounds.
  Skips exit 0 — a deterministic defect no longer re-runs the batch on the paid fallback
  every hour.
- **Per-question failure backoff** (`bot/journal/failures.jsonl`, committed with the
  journal so stateless CI runs see it): after 3 question-content failures in 24 h the
  question is skipped. Infra failures (auth outage, session limit) deliberately do not
  count — they are not the question's fault and must not poison the ledger.
- **Failure alerting in `bot.yml`**: any workflow failure — or a green run whose
  subscription step failed and silently shifted the workload to the metered fallback —
  opens a GitHub issue (once; an open alert issue suppresses new ones).
- **Secret-value guard before journal publication**: the leak-guard patterns cannot know
  credential values, so the commit step now greps the journal for the actual secrets
  (fail-closed) — closing the prompt-injection → public-journal exfiltration path.
- **Always-on `Read` deny for the agent subprocess** (`/proc/**`, `~/.claude/**`): the
  forecasting agent needs Read for the skill's own files, never for process environments
  or credential stores. Inert where the paths don't exist.
- **Deadline discipline**: the repair retry, the CoVe verification call, and
  still-failing research runs now respect `--deadline-minutes` (previously only gated
  between questions and after a first success), and the ensemble records
  `single_run(of N intended)` when it collapses to one run. The OpenRouter fallback step
  computes its deadline from the time actually remaining before the job timeout.
- **Fail-fast preflight**: a live run without `METACULUS_TOKEN` exits before any agent
  spend. On the OpenRouter path a $0 cost envelope is floored at a nominal $0.10/call so
  `--budget` can never be inert on exactly the metered path.
- **Test coverage for the live-submission branch** (found by the review: every prior test
  ran `dry_run=True`) — submitted-equals-journaled for binary/MC, discrete CDF sizing,
  comment-failure isolation — plus the pre-filters, the ledger, pagination, and retry
  behavior. 199 tests.

### Changed
- `@anthropic-ai/claude-code` is version-pinned in all workflows (an unattended hourly
  cron must not pick up a broken release — the fallback runs the same binary).
- `pyproject.toml` version now tracks `SCAFFOLD_VERSION` (was stuck at 0.1.0) and the
  manifest test enforces it.

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
