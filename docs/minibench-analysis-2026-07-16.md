# MiniBench full census vs the bot — 2026-07-16

Operator-supplied complete comparison (60 questions; community aggregate of ~125 bots
revealed at question close) joined against `bot/journal/forecasts.jsonl`, with the ten
largest gaps adversarially audited the same day against live primary sources (SEC
filings, court reporting, EU registries, CoinGecko/DRAMeXchange closes, JIHS data).
Dataset frozen in `tmp/mb_pairs_full.json` → committed as part of the counterfactual
harness inputs; diagnosis transcripts under the session's workflow directories.
Questions resolve 2026-07-23..25: everything here is disagreement + live-evidence
adjudication, NOT resolution scoring. The scoring is preregistered, below.

## The two census signatures

**Binary extremity (n=37 pairs).** The bot is further from 50% than the crowd on 31/37
(84%, one-sided binomial p = 0.00002); median |logit(bot)|/|logit(crowd)| = 1.42. Mean
bot−crowd = −6.1pp (the extremity skews toward the NO side).

**Numeric narrowness (n=21 pairs).** Bot central intervals narrower on 19/21 (p =
0.0001), median width ratio 0.62 — while the bot median sits inside the community
interval 20/21 times. Location is fine; dispersion is systematically half the crowd's.

## The reframe the live audit forces

Extremity relative to the crowd is NOT one thing. Auditing the top gaps against primary
sources on 2026-07-16:

Every "us" verdict from the first pass was then attacked by an independent skeptic
agent instructed to refute it (default-to-refute, fresh research). The table shows the
post-skeptic state.

| Question (gap pp) | Verdict | Why |
|---|---|---|
| SK Hynix Q2 earnings (−48.8) | **us, upheld** | SEC 6-K (Jul 15): earnings Jul 29 — after the window. Korean-press calendar (news1: "SK하닉 29일") was in our pre-forecast research. Skeptic: true P ≈ 3–8%; crowd 62% anchored on last year's Jul 24 and stale aggregator dates. |
| Utah Robinson brief (−31.7) | **us, upheld** | Court scheduling order: brief due Jul 28, 4 days past the window, public since Jul 10. Skeptic: true P ≈ 5–15%; could not push above ~15%. |
| EU GPAI ≥30 signatories (−29.6) | **us, high** (no skeptic pass) | Registry flat at ~23–24. Crowd conflated the Jul 22 deadline of the *other* EU Code of Practice (transparency); our journal explicitly flagged the conflation. |
| TAC ≤ $0.0020 (+26.2) | **crowd, high** | No close near $0.0020; the decline was already decelerating in our own cited data. Our worst confirmed miss. |
| DMA 6(11) decision (−23.4) | **crowd, high** | **Resolved YES Jul 16.** Institutional-process overdiscount: final signaled step + political pressure priced at 16%. |
| Dodgers best record (+16.7) | **crowd, med** | Leader plays 6 road games vs top-5 teams; chaser 6 home vs last-place. Our journal LISTED schedule strength as missing evidence, then forecast 82% anyway. |
| DMA non-compliance (−16.0) | **crowd, med** | FT (Jul 15): decision being prepared; same overdiscount family. |
| NBA investigation (−14.6) | **us, upheld** | Probe in month 11, actively expanding, no timetable. |
| DDR5 > $55 (−13.8) | **crowd, med** | $49.3 on Jul 16, pace accelerating toward $55; reference class was a hand-picked calm sub-window. |
| SOL > $85 (−13.7) | **contested** | Closes $74–78; first audit said us, skeptic sided crowd — both defensible on vol assumptions. |
| ECB deposit-rate hike (−9.2) | **us, high** (no skeptic pass) | Our 8% ≈ market-implied ~10%; crowd 17.2% over-anchored on the June hike. |
| Starship splashdown (−12.4, conditional) | **crowd, med** | Conditional-criterion mishandling: launch probability leaked into a conditional forecast. |
| Japan HFMD W27 (numeric) | **crowd-leaning** | Truth 7.03 (published hours post-forecast): our median closer-ish (6.1 vs 8.04) but our interval (5.78–6.47) excluded truth; crowd's covered it. |
| TAC > $0.0050 (−10.6) | **contested** | Closes far below, but token recovering; skeptic sided crowd. |
| Russia fuel ban (−13.4) | **contested** | Extension genuinely open; skeptic sided crowd. |

Post-skeptic scoreboard: **us 5 (3 skeptic-upheld + 2 high-confidence unattacked),
crowd 6, contested 3.** The decisive pattern survives verification: the THREE LARGEST
gaps are all our upheld/strong wins, and they share one mechanism — **a concrete
schedule / docket / registry / market-implied anchor that our research found and the
herding crowd did not.** That is the research-agency edge the roadmap says we're
buying. A global de-extremization would trade our best property (rare, huge,
schedule-backed divergences) for our worst (moderate extrapolation overshoots); only
the preregistered outcome test can price that trade, which is why it is subgrouped.

## Confirmed failure modes (all four have concrete fixes)

1. **Extrapolation overconfidence** (TAC-low, Dodgers): trailing momentum extrapolated
   at full speed; symmetric-strength shortcut on schedule-driven standings; and — worst —
   ignoring our own `missing_evidence` flag (Dodgers).
2. **Institutional-process overdiscount** (DMA×2): "steps remain → unlikely" on
   processes in their final signaled step. Note this is the same question family as our
   edge — the differentiator is whether a concrete schedule EXISTS. Found-schedule →
   trust it (we win); no-schedule-but-momentum → don't collapse to single digits (we lose).
3. **Numeric interval narrowness** (19/21 census-wide; HFMD truth outside our interval):
   dispersion, not location.
4. **Conditional-criterion mishandling** (Starship): P(condition) leaked into a
   conditional probability.

## Interventions (mapped, staged, testable)

- **research.md v2 addendum** (`docs/proposals-research-v2.md`, awaiting approval):
  schedule-first for institutional deadlines (hardens the edge), no-schedule momentum
  rule (fixes mode 2), barrier-question vol/semantics facts (mode 1), bottom-up partial
  aggregates + live registry-count anchor + adjacent-entity disambiguation (modes 1,3),
  opponent-schedule for standings (mode 1).
- **Reasoning-side gates** (same doc, separate subsection; touch production prompts →
  operator approval): the missing-evidence gate (a named decisive gap forces
  base-rate-ward movement / widening) and the conditional-question guard.
- **Numeric dispersion**: owned by the numeric-uncertainty work stream; this census
  (19/21, ratio 0.62) plus the preregistered w=1.6 counterfactual is the evidence base.
- **Journal integrity**: 6 submitted-but-unjournaled rows (lost in the 2026-07-12 git
  incident) backfilled from the platform record (`scripts/backfill_journal.py`);
  values cross-check against the operator table.

## Preregistered outcome tests ($0, score after Jul 23–25)

`bench/analysis/minibench_counterfactuals.py`, frozen 2026-07-16 before any resolution:
- Binary logit shrink a ∈ {0.5, **0.573**, 0.7, 0.85, 1.0} — global AND per-subgroup
  (tags frozen outcome-blind from journal reasoning: schedule/momentum/other,
  `minibench-2026-07-tags.json`). Registered prediction: shrink HURTS 'schedule',
  HELPS 'other'; net global effect ~neutral, which would kill any blanket shrink and
  justify a question-shape-conditional policy instead.
- Numeric widen w ∈ {1.0, 1.3, **1.6**, 2.0} on pinball loss + 50% coverage.
- Decision rule: promote only on CI90 excluding zero; one wave is underpowered for
  small effects — a straddling CI means collect more waves, change nothing.

## What this does NOT license

No production prompt changed. No recalibration armed. The extremity and width
signatures are crowd-relative; the preregistered outcome test is the arbiter. The
research-rule additions ride the existing research.md v2 approval gate.

---

# RESOLVED — 2026-07-26 readout of the preregistered tests

57 resolutions pulled straight from the platform record (`bench/analysis/minibench-2026-07-resolutions.json`;
36 binaries, 21 numerics — 2 unresolved, 1 annulled, and 6 non-MiniBench FutureEval rows
that share the resolve_by window are excluded). Frozen output:
`bench/analysis/minibench-2026-07-readout-2026-07-26.txt`. Leaderboard: 60 questions
predicted, 99.7% live coverage, total score 589.

## Verdict by the preregistered rule: NOTHING PROMOTES

- **Binary logit shrink.** a=0.573 vs identity: mean Brier delta **-0.0026**, CI90
  [-0.0245,+0.0174] — straddles. Grid: a=0.5 0.1585 / a=0.573 0.1552 / a=0.7 0.1529 /
  a=0.85 0.1542 / a=1.0 0.1578. a=0.7 is nominally best; that is a post-hoc grid pick and
  the primary comparison straddles, so no shrink ships.
- **Subgroup predictions are falsified in direction** (all underpowered, no CI clears):
  registered call was shrink HURTS 'schedule' and HELPS 'momentum'/'other'. Measured:
  shrink HELPS 'schedule' (n=3, 0.4496 identity → 0.3203 at a=0.5) and HURTS 'momentum'
  (n=9, +0.0101) and 'other' (n=24, +0.0061). The question-shape-conditional shrink
  policy this was meant to justify is not supported.
- **Numeric widening.** w=1.6 vs identity: mean pinball delta **+746** (worse), CI90
  [-0.34,+2238]. 50% central-interval coverage **11/21 = 52%** against a 50% target.

## The preregistered numeric metric asked the wrong question

Two defects, both visible only now:

1. Mean pinball over raw units is **scale-dominated** — one question priced in 10^5 (TAC
   TVL) swamps twenty priced in 1–100, so the "primary" numeric comparison was
   effectively a one-question test. Its CI is not interpretable.
2. MiniBench does not score pinball. It scores the **log density the submitted CDF puts
   at the outcome**. Interval width is nearly irrelevant to that; what matters is mass
   where the outcome landed.

`bench/analysis/minibench_numeric_tails.py` (EXPLORATORY, written after resolution)
scores the tournament's own quantity: outcome → internal [0,1] location → submitted-CDF
density there → 100·log2(density/uniform), the units peer score moves in 1:1.

## What actually cost us points: asymmetry, not width

- Interval **width is calibrated**: 25–75 coverage 11/21 (52%, target 50%), 10–90
  coverage 16/21 (76%, target 80%). Symmetric widening therefore buys nothing, and
  aggressive widening actively costs (tail-only t=3.0: **-693** total log score).
- The outcome landed **above our median in 15/21 questions (71%)** — one-sided binomial
  p=0.039. Our numeric medians are biased LOW.
- **All five negative-scoring numerics were upper-tail misses**, four of them barely
  above p90: UKMTO attacks 8 (p90 8), NIFC large fires 78 (p90 66), Ebola deaths 1271
  (p90 1230), Japan HFMD 7.03 (p90 7.0), Brent 96.78 (p90 97). The worst single score,
  -119, came from an outcome **0.43% above p90** — past the outermost declared
  percentile the CDF builder has nothing to interpolate against but the edge of the
  question's range, so mass thins out abruptly (median 6.4x drop at the cliff across
  this wave, max 21x, min 1.0x).
- Counterfactual totals over the same 21 questions, in LEADERBOARD POINTS
  (submitted = 469; see the units correction below):

  | transform | total | Δ | worst q | inside 10–90 |
  |---|---|---|---|---|
  | submitted | 469 | — | -119 | 16/21 |
  | global widen w=1.3 | 565 | +96 | -64 | 19/21 |
  | **right-tail only r=1.5** | **618** | **+149** | **-52** | 20/21 |
  | right-tail only r=2.0 | 547 | +78 | -61 | 20/21 |
  | no open-bound halving | 512 | +43 | -92 | 19/21 |
  | uniform mixture e=0.10 | 496 | +27 | -82 | 17/21 |
  | tail-only (both) t=1.5 | 476 | +7 | -52 | 20/21 |
  | shift up d=0.1 | 511 | +42 | -70 | 15/21 |

  Right-tail widening at r=1.5 is worth **+7.1 leaderboard points per question**, CI90
  [-21.5,+35.1] — straddles zero at n=21, so by the standing rule it is a hypothesis,
  not a shipped change.

### Units and bucket correction (2026-07-26, same day)

The first version of this readout was scored in `100*log2` units with a left-closed
bucket rule and a uniform reference of 1.0. Verified against Metaculus/metaculus source
(`scoring/score_math.py`, `utils/the_math/formulas.py`), the platform uses:

    k        = max(int(u*N + 1 - 1e-10), 1)          # RIGHT-closed: an outcome exactly
                                                     # on a bucket edge scores BELOW it
    baseline = (1 - 0.05*open_bounds) / N            # 0.05 if the outcome is out of bounds
    score    = 50 * ln( mass_in_bucket_k / baseline )

Three consequences. (1) Every figure in the first version was **2.885x too large**
(`100*log2 = 144.27*ln` vs `50*ln`); all numbers above are restated. (2) The left-closed
rule was biased pessimistic exactly at declared percentiles — 35 of this wave's 105 sit
on a bucket edge. (3) MiniBench pays a PEER score, `50*(ln p - mean of the field's ln p)`:
the field term is independent of our forecast, so it cancels in any paired comparison of
two of our own forecasts and every DELTA above is a delta in real leaderboard points,
while the absolute levels are baseline scores and are not what the leaderboard shows.
Also corrected: an out-of-bound outcome is scored against a flat 0.05 baseline and, under
peer, is near-neutral because every bot's out-of-bound mass is pinned near the same floor
— so pushing mass past a bound is not a way to buy tail insurance. `bucket_index`,
`platform_pmf` and `platform_score` are locked by tests in `tests/test_minibench_analysis.py`.

### A cheaper lever the audit surfaced: the open-bound halving

`percentiles_to_cdf` places the bound anchor at `1 - 0.5*(1-max_frac)` when the upper
bound is open (`core.py:1046-1048`), i.e. it assumes half of the outermost declared decile
lies outside the question's range. Measured across this wave, our declared p90 actually
lands at a mean CDF of **0.930**: every distribution we submit is sharper than the one we
elicited. Removing the halving is worth **+43 points (+2.0/question)** and needs no new
elicitation at all — it is a pure harness change. It is inherited from the upstream
reference implementation, so changing it is a deliberate divergence, not a bug fix.

## The binary side points the same direction

Globally unbiased (mean p 0.285 vs base rate 0.278, Brier 0.1578) and sharp where it
counts (11 questions under 10%: zero resolved YES). The leak is one band:

| our p | n | mean p | resolved YES |
|---|---|---|---|
| 0–10% | 11 | 0.061 | 0% |
| **10–25%** | **11** | **0.127** | **27%** |
| 25–50% | 5 | 0.327 | 40% |
| 50–75% | 5 | 0.625 | 40% |
| 75–100% | 4 | 0.861 | 75% |

The 10–25% band under-forecasts by ~2x (3/11 vs ~1.4 expected; not significant alone,
p≈0.13), and it holds both big institutional-process misses (EC DMA non-compliance
decision at 0.120, DMA Art. 6(11) specification at 0.156 — both YES). Direction matches
the independently recorded "institutional-process overdiscount" and "deadline-optimism
tail" findings.

**Unified diagnosis: we under-predict change and accumulation inside the question
window.** Numeric outcomes land above our median; low-but-live binaries fire more often
than we say. This is one bias with two faces, not two findings.

## Preregistered for the NEXT wave (frozen 2026-07-26, before it opens)

The 2026-07-27 MiniBench also carries a model change (sonnet-5 → opus-5, repo-wide), so
that wave is confounded for any prompt-level comparison. Registered now:

- **Primary numeric transform: right-tail widen r=1.5** (p90' = p50 + 1.5·(p90 − p50);
  p10/p25/p50/p75 untouched), scored on 100·log2 density with a paired bootstrap CI90,
  same rule: promote only if the CI excludes zero in its favor.
- Secondary, reported not tested: r=2.0, global w=1.3, uniform mixture e=0.10.
- **Median-bias sign test**: fraction of outcomes above our median, target 50%.
  Two waves pooled (this one 15/21) is the first adequately powered look.
- Binary: no shrink. Report the 10–25% band's realized rate as a standing monitor.
- The scale-dominated pinball metric is retired; `minibench_numeric_tails.py` is the
  numeric scorer from here.
