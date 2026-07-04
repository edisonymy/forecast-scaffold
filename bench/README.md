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
#    The auto tier runs ONLY its router call by default (--auto-mode router): its
#    forecast is imputed from the routed tier's paired row, which is cheaper AND less
#    noisy than re-running the forecast. --auto-mode full re-runs it anyway; the
#    duplicate doubles as a run-to-run repeatability probe.
python bench/run_bench.py bench/sets/2026-07-04.jsonl --tiers low,medium,high,auto \
  --provider openrouter --concurrency 6 \
  --agent-cmd "claude -p --model claude-sonnet-5 --output-format json --allowed-tools Read,Glob,Grep,WebSearch,WebFetch"

# 3. Score
python bench/report.py bench/sets/2026-07-04.jsonl
```

`bench/sets/` and `bench/results/` are gitignored: sets embed CC-BY-SA ForecastBench
content, and results are experiment data, not code. Publish conclusions (and the
report table) in the journal/docs instead.

## Iteration protocol (how to tune without overfitting the benchmark)

The benchmark is a dev signal, not the objective. Rules, in force from scaffold v0.1.0:

1. **Noise floor first.** The repeatability section of the report (same tier, same
   question, run twice) sets the floor: with per-question run-to-run noise σ, the paired
   standard error on a mean-|Δp| difference over N questions is ≈ σ·√2/√N. A change is
   accepted only if the improvement clears ~2× that SE on the dev set — otherwise it's
   indistinguishable from re-rolling the dice.
2. **Preregister each change.** Before running, write down (issue or journal entry): the
   hypothesis, the single change made, and which metric should move which way. One change
   per `scaffold_version` bump. No post-hoc metric shopping — the multi-metric table
   exists so a change that improves mean |Δp| while degrading RMS or cost is caught, not
   cherry-picked around.
3. **Dev / confirm split.** Tune on one question set; confirm on a *fresh* set the tuned
   version has never seen (next ForecastBench drop, or a `--from manifold` pull). A win
   that appears on dev and disappears on confirm was overfit — revert.
4. **Retire mined sets.** After ~3 tuning cycles against the same set, retire it from
   decision-making (keep it as a regression suite). ForecastBench ships a fresh 500
   biweekly; there is no reason to keep mining one batch.
5. **Stratify by source.** A change that helps only one source (e.g. Polymarket phrasing)
   is a niche adaptation, not scaffold skill — the per-source table is the check.
6. **Resolution is the outer loop.** Crowd distance is trusted only as long as resolution
   Brier (ForecastBench resolution sets + the journal via `calibrate`) keeps agreeing
   with it. If versions improve on crowd distance but not on resolutions, the proxy has
   been gamed — reground on resolutions. This check runs on every version once it has
   ~20+ resolved questions, and it is the score that can't be overfit prospectively.
7. **Freshness hygiene.** Live-refreshed crowd only, where a live source exists; drop
   questions whose live price sits at an extreme (≤0.03 / ≥0.97 — effectively resolved)
   or whose refresh fails. The 2026-07-04 baseline's largest "miss" was a question the
   Supreme Court had already decided: the bot was right and the 3-week-old freeze value
   was wrong. Freeze-only sources (Metaculus, INFER) stay flagged in the report.

## External yardsticks

* **ForecastBench leaderboard** — same question universe; submitting is 3 slots/round,
  ideal for a low-vs-auto-vs-high paired entry later.
* **Metaculus bot tournaments** (MiniBench/FutureEval) — spot peer score against other
  bots, human-crowd comparison included where Metaculus exposes it to bots.
