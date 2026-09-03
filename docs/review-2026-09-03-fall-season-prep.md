# Fall 2026 FutureEval prep — review and proposals (2026-09-03)

## Plain summary (read this first)

**Where we are.** Rank 44 of 267 in the summer season; prizes start at rank 40. Leaders
score ~18 points per question, we score ~4. Binary and multiple-choice are fine. Numeric
questions lose more than everything else earns, for two reasons: our ranges are about
two-thirds as wide as the crowd's on almost every question, and in July three answers
landed entirely outside our range (maximum penalty; fixed by escape mass since). On
binaries we are more extreme than the crowd on almost every question, usually toward
"no", and our low-probability calls resolve "yes" more often than we say. Since the
August fixes the per-question rate looks leader-level, but on only 26 questions.

**What the field says.** Model choice matters most. Research beats reasoning.
Extremizing hurts. Looking up similar past questions, ensembling several runs, and
fitting a calibration correction to your own record all help. Bots still trail the Pros
every season.

**Methodology changes (forecasting), in order of expected value:**
1. Ask three times on numeric and multiple-choice questions and average the quantiles
   (today only binaries are pooled). Widens ranges where runs disagree.
2. For any series question, make the research step record how much the series actually
   moved over windows of the same length (record-only first, like the v0.4.24
   checklist). Verified 2026-09-03: the harness reproduces the declared quartiles
   (submitted 25-75 width 2.17 vs 2.10 declared on TrendForce) and makes the tails
   slightly fatter, so the narrowness is elicited, not a harness artifact. If the
   record-only fact does not move declared widths, test a floor with a stated-reason
   escape hatch as the second step.
3. For "will X happen between A and B" questions, extend the existing reference-class +
   base-rate floor (today MC/numeric only) to binaries, with the base rate defined for a
   window of that length. Record-only. Lower priority than 4.
4. Run the already-built Platt calibration gate on the 100+ resolved tournament binaries.
   Free.
5. Same-template lookup: MiniBench regenerates the same templates every wave (BTC, DDR5,
   Starlink count, Foro Penal, Nino 3.4); resolved prior versions give known outcomes and
   known crowd widths, which answers item 2 directly for recurring questions. Scope to
   same-template first; the mechanism for one-off seasonal questions is thinner.
6. Smooth the numeric density (the spike-and-corners shape is a real pchip artifact;
   ~20 points per corner miss). Must preserve the declared quantiles (PIT at anchors) and
   clear the CI rule on the resolved set. After 1 and 2.
7. Test two independent research runs against one research + two reasoning runs at equal
   cost.
8. Fable 5.1 for the final estimate: 2x Opus per token ($10/$50 vs $5/$25). Final-run-
   only swap ≈ +$0.5-0.8/q (+$40 per MiniBench wave, +$500/season); all runs ≈ +$1.3-
   2.1/q (+$100/wave, +$1,100/season). Confirm subscription and Metaculus-credit
   coverage first. Fable guidance warns prescriptive prompts reduce its output quality.

**Ops changes:** auto-discover the Fall slug (exercise the discovery step alone, no
model spend); six-hour silence alarm; credit and AskNews renewal; weekly score readout
from the API. Dropped: the cheap fallback forecast (the 8 misses were an outage, which a
fallback would not survive; at most a retry when a failed question is 15 minutes from
close) and the testing-area dry run (MiniBench is the live test bed).

**Order:** 4 (free) → 1 and 2 as separate paired tests → 3 and 5 → 6, 7, 8. Ops before
mid-September.

**Status after the 2026-09-03 working session (v0.4.27):** operator kept opus-5 (8
dropped). Done: resolutions overlay + readout; 2 and 3 as record-only checklist items; 5
as same-template prior facts from the overlay (research run only); 4 run — STAY INERT
(CV delta +0.011, n=58; rerun at n>=120); 6 backtested paired vs pchip — not promotable
(+2.20/q, CI straddles, helps 35/90), preregistered for wave 4; slug discovery and the
journal alarm shipped. Open: 1 (numeric/MC pooling), 7, Market Pulse cadence.

---

Scope: where the bot actually stands on the platform record, what the Spring 2026 bot-maker
survey and the 2026 literature say, and a prioritized change list for the Fall season
(seasonal tournaments start every January, May and September; the Fall slug is not yet in
the projects API as of today, summer forecasting ends 2026-09-06).

Evidence pulled live today (all via the bot's own token; scratch copies of the raw API
pulls are in this session's scratchpad, the community scrape is frozen in
`bench/analysis/minibench-2026-08-24-community-scrape-2026-09-03.txt`):
- Summer 2026 leaderboard (project 33022) and Spring 2026 (32916) entries.
- `my_forecasts.score_data` for all 148 summer questions we forecast (81 scored so far).
  NOTE: this endpoint DOES return our own spot peer / baseline scores per question with the
  bot token — the HANDOVER claim that scores are browser-only applies to the community
  aggregate, not to our own scores. Automate this (proposal P3a).
- The current MiniBench (2026-08-24 wave, 60 q) scraped from the operator's logged-in
  browser session: community median/IQR vs ours on all 23 binaries and 35 numerics.
- Market Pulse 26Q3 leaderboard and question structure.

## 1. Where we stand

**Summer 2026 seasonal (unfinalized, 2026-09-03):**

| | value |
|---|---|
| rank | 44 of 267 entrants (40 prize-winners; prize line 479 pts) |
| our score | +336.7 over 81 scored questions, +4.2/q |
| leaders | +3647 / +3582 / +3530 over ~200 q, i.e. +17..+19/q |
| coverage | 81 of 193.7 max — we entered 2026-07-06, so ~120 scored questions predate us |
| by type | binary +8.9/q (n=33), MC +23.3/q (n=19), discrete -9.0/q (n=16), numeric -19.6/q (n=13) |
| by month | July -5.6/q (n=55); August +24.8/q (n=26) |
| by model | sonnet-5 -3.3/q (n=46); opus-5 +13.9/q (n=35) — confounded with month/version |
| missed after go-live | 8 of 155 openable questions; 6 of them on 2026-08-01 (one-day outage) |

Reading: at full-season coverage and July's per-question rate we would have landed
around the prize line; at August's rate (+24.8/q, n=26, small) we would be top-3
territory. The three catastrophic losses (Bluesky likers -247.6, cyclosporiasis -219.0,
Labour 7-poll -218.6) are all out-of-range outcomes on scaffold versions BEFORE v0.4.23
escape mass; nothing at the -195.6 floor has recurred since. The whole numeric+discrete
deficit (-399 over 29 q) is ~1.2x our entire positive score.

Spot-peer arithmetic that matters for the Fall: a missing forecast scores exactly 0; our
median question scores +12 to +17. Every missed question therefore costs roughly a median
question's worth. Coverage is the cheapest score there is.

**Summer binary calibration (n=33, Brier 0.202, base rate 45% yes):**
buckets 0-0.1 and 0.1-0.2 resolved YES 3/9 times at mean p≈0.10; buckets 0.7-1.0 resolved
YES 7/8 at mean p≈0.83. Small n, but the low side is where we under-predict YES.

**MiniBench 2026-08-24 wave, live vs community (unresolved, diagnostic only):**
- Binaries: we are MORE EXTREME than the community on 22 of 23, lower on 18 of 23, mean
  logit shift -0.52 (toward NO). Same signature the 2026-07-27 wave showed ("all six big
  losses were us more extreme in both directions").
- Numerics: our 25-75 interval is NARROWER on 31 of 35, median width ratio 0.67 — byte-
  identical to July (0.62) and the 2026-08-10 wave (0.67). v0.4.26's dispersion contract
  did NOT move the width signature. Median placement is unbiased (0.00 IQR).
- Summer PIT check on the 27 continuous questions with usable resolutions: 25-75 coverage
  44%, 10-90 coverage 70%, symmetric tails (4 below p10, 4 above p90).

**Market Pulse 26Q3 (bot-eligible, $7.5k):** rank 37, coverage 5 of 43 because we entered
2026-08-19 mid-round. Top-8 all have full coverage; 8th place earned $350. The Aug 31 batch
(8 questions) is forecast. 26Q4 is pre-entered in `bot.yml`. This tournament rewards
continuous updating; our refresh gate is 48h.

## 2. What the field says (Sep 2026)

**Spring 2026 bot-maker survey (Metaculus, 2026-09-02, n=48 respondents / 42 analyzed,
33 features):** nothing survives multiple-comparison correction; suggestive correlations
with spot peer: GPT-5.4 final model +0.42, checks similar questions/markets +0.34, web
scraping +0.33, development hours +0.32, extremizes predictions -0.30, OpenAI web search
+0.27, research-over-reasoning emphasis +0.26, researches subquestions +0.24. Near zero:
Claude Opus final model (-0.01; Opus 4.6 +0.16), caps -0.03, base rates +0.02, manual
review 0.00, AskNews +0.06, self-critique/red-team -0.07, multi-model ensemble -0.10,
aggregates multiple forecasts -0.19. Authors' takeaway: add good web scraping, prioritize
research features over reasoning features, try native web search. Heavy selection bias
(62% of respondents won prizes vs 28% of participants).

**"AI Forecasting in 2026: what 11 analyses say" (EA Forum synthesis):** strong evidence
for: current frontier reasoning model; a harness (~9 months of base-model progress);
agentic/iterative search over one-shot retrieval; 3-7 diverse runs ensembled; Platt
scaling post hoc (+0.016 Brier binary, +0.005 MC, p<0.001 in the Metaculus calibration
study). Moderate: 2+ distinct research sources; looking up similar resolved questions
(34% of winners vs 0% of non-winners); ~28 LLM calls/q among winners vs 7. Does NOT work:
betting on one search provider; scaffolding before reliability (bugs cost 80+ pts/q);
naive resolved-vs-unresolved state handling; multi-personality aggregation; Bayesian-
framed prompts; training to minimize community-prediction deviance.

**Papers:** BLF (arXiv 2604.18576) — iterative tool loop with a structured belief state,
K trials pooled in logit space with shrinkage, hierarchical Platt; beats GPT-5/Grok on 400
ForecastBench questions; question variability explains 62% of variance. "Is capability a
liability?" (2605.22672) — more capable models over-shift UPPER quantiles on superlinear
series; single-threshold metrics hide it; matches our July upper-tail signature.
"What LLM forecasters know but don't say" (2607.08046) — activation probes beat stated
confidence (not portable to an API bot). ForecastBench trend: parity with superforecasters
extrapolated to ~Nov 2026; Metaculus's own extrapolation for single-prompt bots vs Pros:
~Jun 2027. Pros still beat every bot every season so far.

**Platform changes to plan for:** seasonal questions open for 2-3 hours only (289 of 336
summer questions had a 3h window, 46 had 2h), up to 5 at a time at random hours.
MiniBench questions have been LLM-generated and LLM-resolved since 2026-06-29 (short-
horizon data-series questions; 35 of 60 in the current wave are continuous). Metaculus's
`forecasting-tools` added Fable 5.1 as a supported model on 2026-09-01. The official bot
template gained an opt-in `metaculus-bot-review` integration (2026-08-27) that reads
forecasting-tools-format comments — our comments are private and in our own format, so
it is not directly usable, but it signals that post-hoc review loops are now standard.

## 3. Diagnosis

1. **Continuous questions are the deficit, and the deficit is tails, not centers.** Centers
   are unbiased vs the crowd; widths are 0.62-0.67x the crowd's across four waves; 10-90
   coverage is 70-76% instead of 80%; the catastrophes were undeclared out-of-range mass.
   Escape mass (v0.4.23) fixes the floor; pchip (v0.4.25) is the only transform with a CI
   that excludes zero; the dispersion contract (v0.4.26) polices self-consistency but the
   self-stated SDs are themselves narrow, so the width signature is intact.
2. **Binaries are systematically more extreme than the crowd, mostly toward NO.** The
   survey's strongest negative correlate is extremizing. Our own low buckets under-call
   YES. The Platt layer exists, ships inert, and its activation gate (temporal CV) was
   never run because n was too small — it no longer is.
3. **The bot never pools continuous or MC forecasts** (`n_runs = 1` forced for non-binary
   types, `bot/run_bot.py:1198-1199`). Every leader ensembles. Quantile-averaging across
   independent runs widens exactly where run disagreement is largest, which is the
   missing width mechanism, and it adds research diversity at the same time.
4. **Coverage and reliability leak points.** One day-long outage cost 6 questions (~70-100
   expected points); each seasonal miss is ~a median question. There is no cheap fallback
   submission when the full pipeline fails.
5. **Model:** opus-5 rows score +13.9/q vs sonnet-5 -3.3/q (confounded, but the sign
   agrees with every external analysis that model choice dominates). Within the Claude-
   only constraint the remaining lever is Fable 5.1 for the final estimate.

## 4. Proposals (ordered; each with its decision rule)

### P0 — must be true before the Fall slug opens (~mid-September)

- **P0a Auto-discover the seasonal slug.** `collect_open_posts` skips unknown slugs
  silently; a late `TOURNAMENT_ID` edit means zero coverage for the first days. Add a
  discovery step: list `/api/projects/tournaments/`, select names matching
  `FutureEval Bot Tournament` with `forecasting_end_date` in the future, union with the
  variable. Fail loudly (open an issue) when zero seasonal slugs are active. Also
  pre-enter `market-pulse-26q4` (done) and keep `minibench`.
- **P0b (dropped 2026-09-03 after operator review).** A cheap fallback forecast was
  proposed; the 8 summer misses were a one-day outage that a fallback would not have
  survived, and per-question failures are rare (1 in failures.jsonl). At most: retry a
  failed question once when it is within 15 minutes of close.
- **P0c Outage alarm.** The 2026-08-01 gap was invisible for a day. Add a step that opens
  an issue if the journal has no new row for 6 hours while the seasonal slug has open
  questions (the kicker runs every 10 min; the check is cheap).
- **P0d (dropped).** MiniBench is the live test bed every two weeks; only the slug-
  discovery step (P0a) needs exercising, and that needs no model spend.
- **P0e Credits.** Submit the Metaculus credit form for Anthropic credits now; the
  OpenRouter fallback path already bills opus rates. Renew AskNews for the Fall season
  (per-season renewal is required).

### P1 — continuous questions (the measured deficit)

- **P1a Pool continuous and MC forecasts across runs.** Lift the `n_runs = 1` rule for
  numeric/discrete/MC at medium and high tiers (3 runs), pool percentiles by quantile
  averaging (Vincentization) with the escape masses averaged, MC by geometric-mean-then-
  renormalize. Preregistered test on the next two MiniBench waves: paired single-run vs
  pooled on all continuous questions (~35/wave, so n≈70). Promote if paired spot-peer
  delta CI90 excludes zero, otherwise keep the single run. Cost: ~2x per continuous
  question (~$1.5 extra each, ~$100 per wave).
- **P1b Empirical-dispersion floor as a research move (not a transform).** For any
  question whose resolution source is a time series (prices, indices, counts, polls), the
  research run must fetch the series and record the empirical 10-90 range of h-step
  changes for the actual horizon h; the declared p10-p90 may not be narrower than that
  range unless the run states why (a scheduled event, a bound). This is the mechanical
  fix for what the dispersion contract cannot catch (self-stated SDs that are too small).
  Harness check mirrors the v0.4.26 guard; journal the pre-guard percentiles for paired
  scoring. Decision rule as P1a.
- **P1c Keep the standing preregistration**: pchip + global widen w=1.15 decided at
  n>=90 pooled by the CI-excludes-zero rule. Do not ship early; do not stack it with P1a
  in the same wave (P1a already widens).
- **P1e Smooth the interior density (operator observation 2026-09-03, verified).** The
  platform PDF plots for the two DDR5 questions show a cusp at the median and corners at
  the anchors. Rebuilding the TrendForce submission (p10..p90 = 53.2/54.3/55.2/56.4/58.0,
  escape 0.006/0.015) reproduces it: pchip bin mass per 0.155 unit is 29.5e-3 at 54.2,
  46e-3 at 54.9, 36e-3 at 55.2 — a 1.56x trough-to-peak swing inside the IQR. Cause: five
  anchors imply piecewise densities (13.6%/unit for p10-p25, 27.8%/unit for p25-p50,
  20.8%/unit for p50-p75) and pchip is only C1, so the density has a kink at every anchor
  and a spike inside the tightest segment. Score cost: peer score is 50*ln(mass), so an
  outcome landing in the 54.2 trough loses ~22 points relative to a density that spreads
  the same mass smoothly. Candidates, all $0 to backtest on the existing 69-question
  `minibench_smooth_cdf.py` harness (extend with n>=90 from waves 4-5): (i) least-squares
  fit of a two-piece (split) normal or skew-t to the five quantiles — smooth by
  construction, quantiles approximately preserved; (ii) kernel-smooth the pchip PDF with
  bandwidth = half the smallest anchor gap and re-cumulate; (iii) pchip through
  quantile anchors in log-odds/probit space. Decision rule as P1a; run it paired against
  pchip, not linear. Caveat: on both DDR5 questions our IQR is 2.1-4.0 units vs the
  crowd's 6.8-8.9 — width (P1a/P1b) dominates shape by an order of magnitude; do not let
  P1e delay them.
- **P1d Escape-mass boldness on volatile series.** Crypto declarations were 0.4-0.8% vs
  crowd ~4.5% out-of-range. The P1b floor should fix this by construction (the empirical
  h-step range on BTC crosses the range edges); measure, do not inject.

### P2 — binaries

- **P2a Run the Platt activation gate now.** Fit on all resolved tournament binaries with
  forecast_at before 2026-08-10 (summer July rows + MiniBench waves 1-2), score on rows
  after; the layer only activates if out-of-sample Brier improves (existing
  `recalibration_cv` refusal rule). n is now ~100+ resolved binaries. This is the one
  post-hoc lever with p<0.001 support in the Metaculus data, and it is already built and
  gated. Expected direction: shrink low probabilities upward. Cost: $0.
- **P2b Similar-resolved-question lookup as a research move (roadmap step 5, now the
  best-supported unimplemented item).** Search `/api/posts/?search=<entities>&statuses=
  resolved` (community prediction is visible on resolved non-bot questions and the rules
  allow training on it), and Manifold/Polymarket resolved contracts, record resolution +
  final crowd probability as a reference-class anchor. Survey +0.34; 34% of Fall-2025
  winners vs 0% of non-winners. A/B paired on MiniBench binaries, promote at RES up and
  Brier not worse.
- **P2d Window-frequency floor for event-in-window binaries.** For "will X happen between
  A and B" questions the research run must count occurrences of X in windows of length
  (B-A) over the trailing 12-24 months and record the empirical frequency; the submitted
  probability may not fall below it without a stated reason (a scheduled gap, a regime
  change). The binary analogue of P1b. Motivation: our low-bucket calls resolved YES 3/9
  at mean p 0.10 this summer, and most of the current wave's too-low calls are exactly
  this shape (missile launches, orbital launches, product showcases). Harness check as
  in v0.4.26 with pre-guard values journaled; paired test on MiniBench binaries.
- **P2e Research runs instead of reasoning lenses (equal-cost test).** Today one run
  researches and two re-reason over its dossier. Every reasoning-side lever we measured
  was null and research agency is the measured mechanism, so test 2 independent research
  runs + pooling vs 1 research + 2 lenses at matched cost. Prior evidence is mixed: the
  tranche found angles (3 research runs) no better than high at 3x cost, so treat this as
  a real test.
- **P2c Keep the no-extremize rule; keep the untrimmed geo-mean pool.** Survey -0.30 for
  extremizing; our own measured null. Nothing here changes.

### P3 — process and measurement

- **P3a Season readout script.** `bench/season_readout.py`: pull `my_forecasts.
  score_data` for every journaled question (rate-limit aware, 2.5s spacing, 429 backoff),
  join to the journal, print by type / tier / model / version / month, PIT table for
  continuous, binary buckets. Run weekly in CI, commit the readout. Today's manual pull
  is the prototype.
- **P3b Telemetry gaps.** `n_full_reads` is null in every journal row; `sources` equals
  `n_searches`. Fix the wiring so the research-agency claim can be tested against the
  survey's scraping correlate.
- **P3c Model A/B: Fable 5.1 for the final estimate.** Same harness, same prompts, one
  model swap in `run_models` for the estimate runs only, paired on one MiniBench wave.
  Promote at CI-excludes-zero; this is the single largest correlate in every external
  analysis and the only one we have not touched inside the Claude-only constraint.
  Check subscription coverage and per-question cost first (opus already costs ~$1.3/q
  medium, ~$2.1/q high).
- **P3d Market Pulse cadence.** Questions live 1-2 weeks and the tournament pays for
  continuous updating: set `--refresh-hours 24` for market-pulse slugs (the code already
  orders refreshes after never-forecasted questions, so seasonal coverage is unaffected).

## 5. What not to do (evidence on file)

- No global "be more careful"/hedging prompts, no reasoning tips (two A/Bs negative).
- No extremization, no crowd-weight blend by fiat (untestable: CP hidden live).
- No cross-model or cross-provider ensembles (portability constraint; survey -0.10).
- No shipping three changes in one wave again; P1a, P1b, P2a, P3c are four separate
  paired tests. Order: P2a (free) and P1a first, P1b second, P3c when credits allow.

## 6. Fall opening checklist

1. P0a-P0e merged and dry-run green on 32977.
2. Platt gate run (P2a) and its verdict journaled either way.
3. P1a preregistration text committed before the first Fall MiniBench wave.
4. `bench/season_readout.py` first run on the summer record, frozen as the baseline.
5. AskNews and credit renewals confirmed in CI secrets (fail-open smoke step already
   exists).

Sources: Metaculus Spring 2026 bot survey (notebook 45382, 2026-09-02); FutureEval
resources page (notebook 38928); Summer 2026 announcement (notebook 43340); "AI
Forecasting in 2026: What 11 Analyses Say" (EA Forum); arXiv 2604.18576, 2605.22672,
2607.08046, 2409.19839 (ForecastBench); Metaculus/forecasting-tools and metac-bot-template
commit logs; Metaculus API pulls dated 2026-09-03.
