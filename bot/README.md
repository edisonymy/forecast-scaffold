# bot/ — the Metaculus FutureEval tournament harness

This directory proves the plugin's claim that one set of skills drives every surface: the bot is a
thin consumer that runs the **same `forecast` skill** headlessly against tournament questions.
No forecasting logic lives here — only API plumbing (`metaculus.py`), orchestration
(`run_bot.py`), and the public journal (`journal/forecasts.jsonl`, committed on every run as a
tamper-evident track record).

## Setup

1. **Metaculus**: create a bot account and get a token at metaculus.com/futureeval (participate
   page). Set `METACULUS_TOKEN`.
2. **Agent**: any headless agent CLI works via `--agent-cmd`; the default mirrors the
   production command in `bot.yml` (`claude -p` with a pinned model, the JSON envelope
   for cost/model capture, and `--allowed-tools` hardening).
   Auth options, pick one: a local subscription login (nothing to configure), a
   `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` (subscription Agent SDK credit — the
   right choice for CI), or an `ANTHROPIC_API_KEY` (pay-per-token). Metaculus also sponsors
   LLM/search credits for tournament participants each season — check the current season's
   announcement and its request form.
3. **Provider** (`--provider`, default `subscription`): `openrouter` routes the same
   `claude` CLI through OpenRouter's Anthropic-compatible endpoint, billed to OpenRouter
   credits (e.g. Metaculus's sponsored $100) instead of the subscription. Needs
   `OPENROUTER_API_KEY`; a bare `--model claude-opus-5` is rewritten to the
   `anthropic/claude-opus-5` slug automatically. In `bot.yml` the OpenRouter step also
   runs as an automatic **fallback** when the subscription step fails (rate limit, auth
   outage): the rerun skips already-forecasted questions, so nothing double-submits.
   Caveats: `cost_usd` in the journal is the CLI's own estimate, which may not exactly
   match OpenRouter's billing (check openrouter.ai/activity), and Claude Code's built-in
   WebSearch tool is Anthropic-served — verify it works on this path before relying on it
   (WebFetch is client-side and unaffected).
4. Install the package once: `pip install -e .` from the repo root (the bot imports
   `forecast_scaffold.core` for the journal, validators, and CDF construction).

## The ladder (do not skip steps)

1. **Offline dry-run** — fetch real questions, run the skill, validate, record — no submission:
   `python bot/run_bot.py --tournament <id> --dry-run --limit 3`
   Watch the format-violation/skip rate; it should be ~0 before going further.
2. **bot-testing-area** — Metaculus's sandbox tournament; live submissions, no stakes.
3. **MiniBench** — the biweekly ~60-question fast tournament; the main leak-free iteration loop.
4. **The seasonal FutureEval tournament** — register the bot for the season; note the mandatory
   bot-maker survey to be prize-eligible.

## How a question flows

fetch open questions (skip ones already forecast, unsupported, closed, unbounded, or backed
off after repeated failures — see `journal/failures.jsonl`) → **auto-effort triage** (one
cheap agent call → low/medium/high; override with `--effort`) → run the `forecast` skill with
the question brief (resolution criteria verbatim, options/bounds — never the fetchable
"crowd": a bot token only ever sees other bots' aggregates, which are journaled as a
benchmark and withheld from the agent) under a fenced-JSON output contract → validate the
payload (`core` validators; one repair retry with the errors quoted) → record to `journal/`
(the record carries exactly the numbers submitted) → submit (binary probability /
renormalized MC / percentiles built into a platform-valid CDF by `percentiles_to_cdf`) →
optional private comment with the reasoning (`--comment`).

## Pooling independent runs (every question type)

The tier's `runs` count is how many genuinely independent agent processes forecast one
question, and since v0.4.28 it applies to **all** types, not just binaries. Binaries pool
with `geo_mean_odds`; numeric/discrete/date quantile-average their five percentiles (in log
space when the question has a `zero_point`), averaging the declared escape masses over the
runs that declared one; multiple choice takes a per-option geometric mean, renormalized.
Quantile averaging is shape-preserving — it recentres on the runs' consensus and averages
their widths — so it is the ensemble lever, not by itself a fix for the measured
under-dispersion. Because that is a live question, every pooled record journals the
**single-run counterfactual**: `percentiles_run1` / `probabilities_run1` (the research
run's own answer, which is exactly what a one-run harness would have submitted) alongside
the per-run values in `run_percentiles` / `run_escapes` / `run_probabilities`. Nothing is
A/B split and no question is spent on a control arm;
`bench/analysis/pooled_vs_single.py` rebuilds both arms at resolution, scores them with the
platform's own continuous formula, and carries the preregistered decision rule (KEEP
pooling only if the paired CI90 excludes zero on the positive side at n >= 60).

## Workflows

- `.github/workflows/bot-test.yml` — manual dispatch, defaults to a dry run; use for the
  testing-area phase (set `dry_run: false` and the sandbox tournament id). Never commits.
- `.github/workflows/bot.yml` — the tournament workflow (hourly cron + manual dispatch;
  concurrency-guarded, never cancels an in-flight run). Commits the journal after each run
  behind the leak-guard *and* a secret-value guard; opens a GitHub issue on failure or
  when the run silently fell back to the metered provider. Requires repo secrets
  `METACULUS_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `LEAK_PATTERNS` (and optionally
  `OPENROUTER_API_KEY` for the fallback), plus the `TOURNAMENT_ID` repository variable —
  unioned with the workflow's own `EXTRA_TOURNAMENTS` env, the in-repo way to enter a
  round via pull request (the variable stays the master roster and kill switch).

`--tournament` is backstopped by `--discover` (on by default): before fetching questions,
the bot calls Metaculus's public `/projects/tournaments/` endpoint and unions in the slug
of any currently-active "FutureEval Bot Tournament" / "AI Forecasting Benchmark Tournament"
season, which closes the gap between Metaculus creating a new one (every January, May and
September) and someone remembering to update `TOURNAMENT_ID`. It only ever *adds* slugs —
a configured tournament is never dropped — and fails open: any API hiccup or shape change
prints a one-line warning and falls back to the configured list unchanged. `--no-discover`
restores the old byte-for-byte behavior, and `--post` backtests skip discovery entirely.

## Reading the human crowd (public questions)

Metaculus deliberately hides the human community prediction from **bot accounts** on all
public (non-tournament) questions — the API returns null aggregates to the bot token, and
the anonymous API / legacy api2 / download-data endpoints are all closed. Only bot
tournaments expose a (bot-)crowd to bots. If you want the human number for offline
analysis, `bot/crowd.py` reads it with a **personal-account** token in
`METACULUS_CP_TOKEN` — measurement only, by design never imported by `run_bot` and never
visible to the agent. For crowd-labeled benchmark questions that need no Metaculus access
at all, see `bench/` (ForecastBench freeze values + live Manifold/Polymarket prices).

## Outage alarm

`bot.yml`'s own "Alert on trouble" step only fires when a *step* fails — it can't see a
run that reports success while quietly doing nothing (on 2026-08-01 the bot went silent
for a day and missed 6 questions, unnoticed). `.github/workflows/journal-alarm.yml` is an
independent hourly tripwire for exactly that failure mode: `scripts/journal_alarm.py`
checks that the tournament workflow has had a recent *successful* run (via `gh run list`)
and, when there are open tournament questions, that `bot/journal/forecasts.jsonl` has a
recent Metaculus row — alarming (exit 1) if either check fails, which opens one
deduplicated "Bot alarm: journal silence" issue rather than paging on every tick.

## Resolutions overlay

`bench/sync_resolutions.py` writes `bot/journal/resolutions.jsonl` (append-only;
platform resolution, normalized outcome, our own spot peer / baseline scores, PIT under
our submitted CDF); the research run reads it for same-template prior facts
(`bot/priors.py`, record-only); `.github/workflows/resolution-sync.yml` runs it every 6
hours; `--readout` prints the season tables.

## Honesty rules

- The journal is append-only and public; runs commit it even when forecasts look bad.
- `--dry-run` records but never submits; there is no mode that submits without recording.
- Community prediction is captured **at forecast time** — that's the baseline the track record is
  judged against (see docs/evaluation.md).
