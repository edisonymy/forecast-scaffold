# bot/ — the Metaculus FutureEval tournament harness

This directory proves the plugin's claim that one set of skills drives every surface: the bot is a
thin consumer that runs the **same `forecast` skill** headlessly against tournament questions.
No forecasting logic lives here — only API plumbing (`metaculus.py`), orchestration
(`run_bot.py`), and the public journal (`journal/forecasts.jsonl`, committed on every run as a
tamper-evident track record).

## Setup

1. **Metaculus**: create a bot account and get a token at metaculus.com/futureeval (participate
   page). Set `METACULUS_TOKEN`.
2. **Agent**: any headless agent CLI works via `--agent-cmd`; the default is `claude -p`
   (Claude Code CLI with an `ANTHROPIC_API_KEY`, or a subscription login locally).
   Metaculus sponsors LLM/search credits for tournament participants each season — check the
   current season's announcement.
3. Install the package once: `pip install -e .` from the repo root (the bot imports
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

fetch open questions (skip ones already forecast) → **auto-effort triage** (one cheap agent call
→ low/medium/high; override with `--effort`) → run the `forecast` skill with the question brief
(resolution criteria verbatim, options/bounds, community prediction at fetch time) under a
fenced-JSON output contract → validate the payload (`core` validators; one repair retry with the
errors quoted) → record to `journal/` (with `crowd` captured for later edge-vs-crowd analysis) →
submit (binary probability / renormalized MC / percentiles built into a platform-valid CDF by
`percentiles_to_cdf`) → optional private comment with the reasoning (`--comment`).

## Workflows

- `.github/workflows/bot-test.yml` — manual dispatch, defaults to a dry run; use for the
  testing-area phase (set `dry_run: false` and the sandbox tournament id).
- `.github/workflows/bot.yml` — the tournament cron (every 20 minutes, offset; concurrency-guarded,
  never cancels an in-flight run). Commits the journal after each run. Requires repo secrets
  `METACULUS_TOKEN` and `ANTHROPIC_API_KEY`, and the `TOURNAMENT_ID` repository variable.

## Honesty rules

- The journal is append-only and public; runs commit it even when forecasts look bad.
- `--dry-run` records but never submits; there is no mode that submits without recording.
- Community prediction is captured **at forecast time** — that's the baseline the track record is
  judged against (see docs/evaluation.md).
