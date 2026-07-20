# Manifold bot: mana & betting policy

Approved by the operator 2026-07-11 ("automatically move to next phase when previous phase
passes. approved."). This document is the authoritative policy; `bot/run_manifold.py`
implements it and `bot/journal/manifold-phase.json` records which phase is live and the
evidence behind every promotion. Amendments happen here first.

**Objective.** The bot exists for *signal* — fast feedback on scaffold quality via price
movement, P&L, and the blind-vs-sighted A/B — not mana maximization. Every rule keeps the
measurements interpretable.

Bankroll at adoption: 2,200 mana (user EdisonYiCA2S). Percentages bind to the balance
read live at each run, not to this snapshot.

## Cloud cadence and model-credit boundary

[AMENDED 2026-07-12, operator: cloud runs hourly; up to $5/hour of Claude subscription
credits is acceptable. Clarified explicitly: never use OpenRouter for Manifold.]

- The default-branch GitHub workflow schedules at most one run per hour. A dropped or delayed
  cron tick is not replayed.
- Manifold forecasting is Claude-subscription-only. The unattended path requires an explicit
  `CLAUDE_CODE_OAUTH_TOKEN` setup-token and rejects Anthropic API or gateway routing before
  doing any model, market, journal, or phase work. There is no paid-provider fallback.
- Each scheduled invocation has a hard $5 USD-equivalent Claude credit cap. Before every
  subprocess, the runner passes the exact unspent remainder to Claude's native
  `--max-budget-usd` and also accumulates the returned `total_cost_usd`. A failed or timed-out
  subprocess, missing/non-positive cost telemetry, or a reported native-cap breach reserves
  the entire unknown remainder, stops all further calls, and fails the workflow closed.
- At 24 successful ticks, the theoretical ceiling is $120 of notional subscription usage per
  day. This is a usage-equivalent ceiling, not permission to bill an API account. The
  three-day per-market forecast dedupe and lack of eligible markets usually reduce realized
  use. Manual/local invocations obey the same maximum $5 cap.
- The bot has a 45-minute run deadline inside a 55-minute job boundary, leaving time to
  leak-check and publish the preregistration journal before the next hourly tick.

## Phases (transitions are AUTOMATIC and journaled)

### Phase 0 — dry-run validation (zero mana)
Forecast pairs journaled, would-be bets recorded, nothing POSTed.
**Promotion to 1** when the journal holds >= 3 valid blind/sighted pairs from a completed
run (payloads validated, both modes present, market price recorded) and >= 1 bet decision
was evaluated without error.

### Phase 1 — flat-stake calibration (~2 weeks expected)
- Stake: **flat 25 mana** per qualifying bet (~1% of adoption bankroll). Flat, not sized:
  equal-weighted bets keep movement statistics clean.
- Bet only when ALL hold:
  (a) |sighted forecast − market| >= 0.03
      [AMENDED 2026-07-20, operator: "make more predictions … the standards are too tight" —
      was 0.05 (2026-07-11: was 0.08). Journal evidence at amendment: median sighted
      divergence 0.018; 0.05 admitted 18% of sighted forecasts, 0.03 admits 35%. The caps
      below still bound risk. Note for a future phase-2 promotion: entry divergence 0.03
      equals the convergence-exit band, so revisit (a) before phase 2 goes live];
  (b) no open position in the market;
  (c) hygiene screens pass (>= 25 unique bettors, closes 3–60 days out, meme/self-
      referential excluded, <= 3 markets per topic group per run; selection takes half
      top-volume + half mid-volume so the batch includes markets thin enough to beat)
      [AMENDED 2026-07-11: bettor floor was 50, selection was pure volume-rank].
      [AMENDED 2026-07-20: markets with a journaled pair newer than the 3-day dedupe are
      excluded BEFORE selection, not skipped after — volume-ranked selection is stable
      hour to hour, so post-selection skipping had collapsed throughput to zero pairs per
      run once the top of the ranking was saturated (no journal records 07-18..07-20
      despite green hourly runs).]
- `market_read` (informed/herding/thin/stale) is REQUIRED and journaled but is NO LONGER
  a bet gate [AMENDED 2026-07-11]: it is a preregistered hypothesis — at review we test
  whether informed-read bets underperform. The original gate starved the signal: liquid
  markets read "informed" almost by construction.
- Hard caps: <= 10 bets/run, <= 1 scheduled run/hour, total open exposure <= 30% of
  balance, refuse all betting below 50% of adoption bankroll (1,100 mana floor).
- Exit: hold to resolution.
- **Promotion to 2** when, among live divergent bets at least 7 days old, the market has
  moved TOWARD our entry forecast in significantly more than half of cases (exact
  binomial one-sided p < 0.05, n >= 50), AND sighted Brier <= blind Brier over >= 10
  resolved pairs. **Kill criterion** (also automatic): if at n >= 50 the toward-rate is
  <= 50%, betting stops (phase file marks `killed`) and the bot continues forecast-only —
  the blind-vs-sighted feed stays valuable even when the crowd is smarter than us.

### Phase 2 — earned sizing
- Stake: **quarter-Kelly**, capped. Kelly fraction for YES at market m with our p:
  (p − m)/(1 − m) (mirror for NO); stake = 0.25 x kelly x balance, cap 5% of balance,
  floor 10 mana. Quarter because our p is noisy (full Kelly overbets under estimation
  error) and CPMM slippage shrinks realized edge.
- Convergence exit: sell when the market comes within 3 points of our forecast — signal
  banked, capital recycled.
- Re-forecast open positions only when the market moves > 10 points against us.
- Phase-1 caps stay (bets/run, exposure, floor) with the stake cap replacing flat stakes.

## Standing risk notes
Creator mis-resolution (mitigated by bettor floor + hygiene; accepted residual — play
money). Correlated batches (topic cap). Self-influence (small stakes on >= 50-bettor
books). Model-cost creep is bounded by the hourly subscription-credit policy above.
