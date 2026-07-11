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
  (a) |sighted forecast − market| >= 0.05
      [AMENDED 2026-07-11, operator: "i dont mind if we are less cautious about bets" —
      was 0.08; the objective is signal volume, and the caps below already bound risk];
  (b) no open position in the market;
  (c) hygiene screens pass (>= 25 unique bettors, closes 3–60 days out, meme/self-
      referential excluded, <= 3 markets per topic group per run; selection takes half
      top-volume + half mid-volume so the batch includes markets thin enough to beat)
      [AMENDED 2026-07-11: bettor floor was 50, selection was pure volume-rank].
- `market_read` (informed/herding/thin/stale) is REQUIRED and journaled but is NO LONGER
  a bet gate [AMENDED 2026-07-11]: it is a preregistered hypothesis — at review we test
  whether informed-read bets underperform. The original gate starved the signal: liquid
  markets read "informed" almost by construction.
- Hard caps: <= 10 bets/run, <= 1 run/day, total open exposure <= 30% of balance,
  refuse all betting below 50% of adoption bankroll (1,100 mana floor).
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
books). Model-cost creep (<= ~30 calls/day at one run/day).
