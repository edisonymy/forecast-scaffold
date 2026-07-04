# Internal benchmark: effort-tier distillation

The scaffold's cheap tiers should approximate its expensive one. Treat `high` and the
human crowd as **teachers**, and `low` / `medium` / `auto` as **students**: run every tier
on the same frozen question set, blind, and measure how close each student's probability
lands to the teachers — per dollar. Tuning the tiers (and the `auto` triage rubric) means
closing that gap without paying the teacher's cost. Improvements land in the skill/config;
this harness only measures.

## Ground truth

Two targets, complementary:

* **Crowd** — the strongest available proxy for the true probability *today*, before
  resolution. Sourced from [ForecastBench](https://github.com/forecastingresearch/forecastbench-datasets)
  question sets (CC-BY-SA 4.0, fetched at runtime, never committed here): each
  market-sourced question (Metaculus, Manifold, Polymarket, RAND Forecasting Initiative)
  carries the crowd probability at freeze time. Manifold/Polymarket values can be
  refreshed live at set-build time from their public APIs (`--refresh-crowd`). This route
  needs no Metaculus token at all — Metaculus firewalls bot accounts from its human crowd,
  but ForecastBench republishes the freeze values and the other markets are public.
* **The `high` tier** — the scaffold's own best effort. Distance-to-high tells you what
  the extra spend buys; distance-to-crowd tells you whether it buys *truth*.

Resolution-based scoring (real Brier) comes later for free: ForecastBench publishes
resolution sets for the same question IDs, and the bot's own journal resolves via the
`calibrate` skill. Crowd-distance is the fast feedback loop; resolution is the slow
honest one.

## Protocol

* **Paired**: every tier sees the identical question set (paired evaluation cuts the
  sample size needed to detect a Brier gap by ~5-20x vs independent sets).
* **Blind, enforced**: the crowd value never enters the prompt, and aggregator domains
  are tool-blocked. Search snippets can still leak market odds in principle — treat
  suspiciously-perfect crowd agreement as a leak signal, not skill.
* **Versioned**: every row records `scaffold_version`, model, provider, and cost, so
  results are comparable only within a version and the history stays interpretable.
* **Open questions only**: sets are filtered to markets still open, so the outcome is
  not findable by research (no leakage from resolved questions).

## Metrics (report.py)

Per tier, against each teacher:

* `mean |Δp|` — mean absolute probability gap (headline number).
* `RMS Δp` — penalizes the occasional wild miss (the failure mode that costs
  tournaments; equals root of Brier-vs-teacher-as-outcome-probability).
* `mean KL` — KL(teacher‖student) in nats; the proper-scoring-flavored view.
* `mean |Δlogit|` — gap in log-odds; the right scale near 0 and 1 (probabilities are
  clamped to [0.001, 0.999] first).
* `$ / question` and total cost — the denominator of the whole exercise.

For `auto`: the resolved-tier distribution plus the same metrics, i.e. does the triage
rubric buy high-tier closeness at low-tier prices?

## Usage

```bash
# 1. Build a frozen set (~40 open market questions, crowd values attached)
python bench/fetch_set.py --n 40 --out bench/sets/2026-07-04.jsonl --refresh-crowd

# 2. Run tiers over it, paired + blind (resumable; (question, tier) pairs are skipped
#    once done). Use --provider openrouter to bill benchmark runs to OpenRouter credits.
python bench/run_bench.py bench/sets/2026-07-04.jsonl --tiers low,medium,high,auto \
  --provider openrouter \
  --agent-cmd "claude -p --model claude-sonnet-5 --output-format json --allowed-tools Read,Glob,Grep,WebSearch,WebFetch"

# 3. Score
python bench/report.py bench/sets/2026-07-04.jsonl
```

`bench/sets/` and `bench/results/` are gitignored: sets embed CC-BY-SA ForecastBench
content, and results are experiment data, not code. Publish conclusions (and the
report table) in the journal/docs instead.

## External yardsticks

* **ForecastBench leaderboard** — same question universe; submitting is 3 slots/round,
  ideal for a low-vs-auto-vs-high paired entry later.
* **Metaculus bot tournaments** (MiniBench/FutureEval) — spot peer score against other
  bots, human-crowd comparison included where Metaculus exposes it to bots.
