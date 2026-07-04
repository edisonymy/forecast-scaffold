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
  resolution. Two sources, both needing **no Metaculus token** (Metaculus firewalls bot
  accounts from its human crowd — an empirical sweep of ~1000 open public questions found
  its CP visible on 0 of them outside bot tournaments):
  * **ForecastBench** (default; `--from forecastbench`) —
    [question sets](https://github.com/forecastingresearch/forecastbench-datasets)
    (CC-BY-SA 4.0, fetched at runtime, never committed): each market-sourced question
    (Metaculus, Manifold, Polymarket, RAND Forecasting Initiative) carries the crowd
    probability at freeze time; Manifold/Polymarket can be refreshed live with
    `--refresh-crowd`. Curated, multi-source, leak-controlled, and the external
    leaderboard we'd submit to — **the headline benchmark.**
  * **Manifold direct** (`--from manifold --min-traders 30`) — pulls fresh, liquid,
    still-open binary markets straight from Manifold's public API, crowd = live market
    price. Play-money and creator-resolved, so noisier ground truth, but unlimited and
    free — **use it for cheap, frequent iteration between the biweekly ForecastBench
    drops.** Both sources emit the identical set format, so `run_bench`/`report` don't care
    which you used.
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
# 1. Build a frozen set (~40 open market questions, crowd values attached).
#    ForecastBench (curated, multi-source) — the headline benchmark:
python bench/fetch_set.py --n 40 --out bench/sets/2026-07-04.jsonl --refresh-crowd
#    ...or pull fresh liquid markets straight from Manifold for quick iteration:
python bench/fetch_set.py --from manifold --min-traders 30 --n 40 --out bench/sets/manifold-2026-07-04.jsonl

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
