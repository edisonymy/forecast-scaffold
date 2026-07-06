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
   `OPENROUTER_API_KEY`; a bare `--model claude-sonnet-5` is rewritten to the
   `anthropic/claude-sonnet-5` slug automatically. In `bot.yml` the OpenRouter step also
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

## Workflows

- `.github/workflows/bot-test.yml` — manual dispatch, defaults to a dry run; use for the
  testing-area phase (set `dry_run: false` and the sandbox tournament id). Never commits.
- `.github/workflows/bot.yml` — the tournament workflow (hourly cron + manual dispatch;
  concurrency-guarded, never cancels an in-flight run). Commits the journal after each run
  behind the leak-guard *and* a secret-value guard; opens a GitHub issue on failure or
  when the run silently fell back to the metered provider. Requires repo secrets
  `METACULUS_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `LEAK_PATTERNS` (and optionally
  `OPENROUTER_API_KEY` for the fallback), plus the `TOURNAMENT_ID` repository variable.

## Reading the human crowd (public questions)

Metaculus deliberately hides the human community prediction from **bot accounts** on all
public (non-tournament) questions — the API returns null aggregates to the bot token, and
the anonymous API / legacy api2 / download-data endpoints are all closed. Only bot
tournaments expose a (bot-)crowd to bots. If you want the human number for offline
analysis, `bot/crowd.py` reads it with a **personal-account** token in
`METACULUS_CP_TOKEN` — measurement only, by design never imported by `run_bot` and never
visible to the agent. For crowd-labeled benchmark questions that need no Metaculus access
at all, see `bench/` (ForecastBench freeze values + live Manifold/Polymarket prices).

## Honesty rules

- The journal is append-only and public; runs commit it even when forecasts look bad.
- `--dry-run` records but never submits; there is no mode that submits without recording.
- Community prediction is captured **at forecast time** — that's the baseline the track record is
  judged against (see docs/evaluation.md).
