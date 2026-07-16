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
