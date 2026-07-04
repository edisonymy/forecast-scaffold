# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/)
and mirror `.claude-plugin/plugin.json`.

## [Unreleased]

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
