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

## The three phases of a medium/high question (v0.4.28)

Angle mode runs `run_angles` **independent full-research runs** — production is Angle P, a
plain replicate, three at medium and four at high. Two further phases sit on top of it, each
behind a tier flag, and every phase's pool is journaled beside the number actually
submitted, so which architecture wins is a measurement rather than an argument.

| Phase | Flag | What it is | Default | What it adds per question (opus-5, medium) |
|---|---|---|---|---|
| 1 — parallel research | `run_angles` | k independent research runs, pooled | **on** (3 / 4 runs) | ≈ **$4.0** total |
| 2 — shared evidence | `share_evidence` | every run also writes the estimate-free dossier; each is then re-asked once with the OTHER runs' dossiers — evidence, never their numbers or reasoning — and the second round is pooled instead | **off, every tier** | ≈ **+$1.3** |
| 3 — supervisor | `supervisor` | one reconciler sees every dossier and every estimate **with** its reasoning, lists the disagreements, classifies each FACTUAL or JUDGMENT, settles the factual ones, and issues the final number | **on** at medium/high | ≈ **+$0.4** reasoning-only (**$4.4** all-in), **+$1.0–1.5** on the questions that disagree |

**Phase 2 is off by design** (operator decision, 2026-09-03): it is an experiment switch,
not production. Circulating other forecasters' material is the documented way to collapse a
group's variance *without* improving its mean accuracy (Lorenz et al. 2011, PNAS — N=144,
"remarkably little" social influence needed), which is why the harness journals
`spread_phase1` / `spread_phase2` and the scorer refuses to keep the phase on a variance
collapse with no accuracy gain.

**The supervisor's research budget is conditional on disagreement.** Runs that already agree
have no factual dispute worth searching, so the harness measures the spread of the phase the
reconciler consumes and compares it with the tier's threshold:
`supervisor_search_spread` on a probability scale (binary: max−min probability; MC: max−min
of the pooled leader) and `supervisor_search_spread_iqr` in IQR units for continuous
questions (max−min of the run medians ÷ the pooled p75−p25, so 1.0 = "a full interquartile
range apart"). Below it, the reconciler runs **reasoning-only** — web tools denied at the CLI,
not merely discouraged in the prompt — and reconciles on the evidence in front of it. At or
above it, it runs **research-capable** with up to the tier's `searches` targeted checks. The
spread decides once, before the call; a factual conflict the reconciler notices afterwards
cannot re-arm the tools, which keeps a question's cost predictable. `supervisor.mode` and
`supervisor.spread` journal which path was taken and the number that chose it.

The reconciler is explicitly told **not to average**: the pool is already computed and
journaled beside it, so a number that merely re-derives it adds nothing, and a compromise
between two claims about the world is not itself a claim about the world. Its own number is
submitted only when it validates; otherwise the pool it consumed is, and `aggregation` says
which (`supervisor(angles=P,P,P)` vs `geo_mean_odds(angles=P,P,P)`). Both phases obey the
same budget and deadline stops as a run slot: an exhausted budget skips the phase, prints
why, and falls back one level.

Research depth does **not** vary by tier (operator, 2026-09-03): `searches = 5` and
`min_sources = 3` at low, medium and high. A tier says how much independent *judgment* a
question gets — how many runs, and what synthesizes them — never how carefully one run reads
the world. The old ladder made a low-tier run research badly on purpose, and "research
sources used" is the strongest measured correlate of bot performance in the Fall 2025
FutureEval survey (r = +0.42, p = .006), while aggregation counts were not significant.

## Traces: what each run actually thought

The journal records what the bot forecast; the **trace** records what each run thought. Once
a question is three-to-seven agent calls deep, the submitted number is the one thing that
cannot be reverse-engineered back into its reasoning, and "the pool moved because run 2
found X" is exactly the review question. Every record therefore names a
`trace_path` — `traces/<record_id>.json`, relative to the journal directory — holding one
object per agent call:

```
phase / stage / run_index / angle / mode / model / cost_usd / seconds / ok
estimate (the run's OWN number, type-shaped) / reasoning / sources / dossier
reconciliation + disagreements (supervisor) / searches_used (when reported)
validation_errors_first_attempt (only when a repair retry happened)
```

plus each phase's pool, the spreads, and what was finally submitted. The dossier (non-angle)
path is traced the same way, so old-design runs stay readable. Text fields are capped at
6 KB and the whole file at ~60 KB — these are committed on every hourly run, so an unbounded
dossier would bloat the repo forever. Writing happens **after** the journal append and is
fail-open: a trace that cannot be written prints a warning and never costs a forecast. Since
`bot.yml` stages `bot/journal/` wholesale, traces are committed with the journal and pass
through the same leak guard (which is fail-closed on any line, trace lines included).

`bench/analysis/phase_pools.py` scores all three arms paired at resolution — log score for
binaries, the platform's continuous formula for numerics, bootstrap CI90 on each delta —
plus the herding check. **Preregistered rules:** phase 2 is kept only if its paired delta vs
phase 1 is ≥ 0 *and* the spread ratio is not below 0.5 alongside a non-positive delta; the
supervisor is kept only if its paired delta vs the phase it consumed has a CI90 excluding
zero on the positive side after two MiniBench waves (n ≥ 40 binaries, or 60 mixed).

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
