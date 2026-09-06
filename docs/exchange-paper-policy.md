# Exchange paper-trading bot: policy and preregistered go-live rule

Adopted 2026-09-06. This document is the authority; `bot/run_exchange.py` and
`bot/score_exchange.py` implement it. Amend here first.

## Why this exists

The Manifold bot earned about 19% mark-to-market in eight weeks against a play-money book
with no commission, no spread and no arbitrageurs, betting quarter-Kelly on markets with at
least 25 unique bettors. Liquid Betfair and Smarkets political prices are roughly at
professional-forecaster level (Metaculus Pros still beat the best bots by ~20 peer points
per season), so the transferable edge is unknown and the honest prior is "half of it, then
minus the spread and commission". An expected-value model of the live strategy is dominated
by that one number: at a 1.5-point edge the exchange bot earns less than the index fund the
bankroll came out of; at 3 points it is a Sharpe ~0.85 sleeve uncorrelated with equities.
The paper phase exists to measure which world we are in before any pound is at risk.

Three real-money frictions the paper test pays that Manifold never did:

1. **The executable price, not the mid.** A YES bet is priced at the best *back* offer,
   a NO bet at the best *lay* bid; each is worse than the mid by half the spread.
2. **Commission on winnings**: Smarkets 2%, Betfair 6% (folded into net odds, so the EV
   gate, Kelly sizing and settled P&L are all net).
3. **Depth**: a stake is capped by the size resting at the touch. Politics books outside
   election periods carry tens to hundreds of pounds at best price; this cap is what bounds
   the live strategy, so it is measured, not assumed.

## What runs

Every six hours (`.github/workflows/exchange-paper.yml`), subscription-only, hard-capped in
Claude credits per tick, no venue credentials required for Smarkets (public reads) and a free
DELAYED app key for Betfair (prices lag 1-180 s; irrelevant at 2-week-plus horizons):

1. Pull every quoted politics / current-affairs contract from both venues
   (`bot/exchanges.py`, read-only by construction — there is no order code path).
2. **Snapshot** the book of every contract the journal already tracks into
   `bot/journal/exchange-prices.jsonl` (closing line + settlement, scored offline later).
3. **Select** fresh contracts: open, two-sided, at least £10 at the touch on both sides,
   spread at most 8 points, mid within [0.02, 0.98], closing 3-180 days out, at most two
   runners per market and three per event, not forecast in the last 3 days. Ranked by
   matched volume (deepest books first: the prices most worth testing against).
4. **Forecast** each contract twice through the same skill and loop the Manifold bot uses
   (`run_manifold.forecast_market`): BLIND (no prices; betfair.com, smarkets.com and the
   odds aggregators tool-blocked) and SIGHTED (the full book, commission, and the required
   `market_read` judgment). Sonnet-5 at medium tier, as Manifold.
5. **Paper bet** on the sighted number: side by divergence from the mid (>= 3 points),
   priced at the executable side, EV per pound at risk net of commission >= 5%, sized
   quarter-Kelly on a notional £10,000 bankroll, capped at 5% of bankroll and at the depth
   resting at the touch, floor £2 (Betfair's minimum). Journaled with the whole book at
   entry. Nothing is ever sent to a venue.

## Preregistered decision rule (computed by `bot/score_exchange.py verdict`)

Per paper bet the primary metric is **closing-line value**: `q_close / q_entry - 1` on the
side taken, where `q_close` is that side's mid at the last open snapshot. CLV needs no
settlement and is immune to outcome variance; it is the standard fast test of a betting edge.

**GO-LIVE-CANDIDATE** when ALL hold, pooled across venues:

| leg | threshold |
|---|---|
| paper bets with a scored closing line | n >= 200 |
| mean CLV | bootstrap CI90 lower bound > 0 (10k draws, seed 7) |
| settled paper bets | n >= 100 |
| settled ROI net of commission | > 0 |
| Brier(sighted) - Brier(book mid at entry) | <= -0.01 over the settled set |

**KILL** when n >= 200 scored bets and mean CLV <= 0: the edge did not transfer; the bot
continues forecast-only as a public benchmark, and no live phase is opened.

**HOLD** otherwise. No live betting, no sizing decisions, no "it looks good so far".

A GO-LIVE-CANDIDATE verdict authorises only the *proposal* of a live phase 1 (flat £5
stakes, Smarkets first at 2% commission, Betfair Expert Fee irrelevant at that scale) under
a separate policy document; it does not authorise a trade.

## Costs

Forecast pair ≈ $1.10 of subscription credit (Sonnet-5, medium). At the throughput the red
team estimated for these venues (2-5 fresh independent contracts a week outside elections,
more when runners of one market are counted) the paper phase costs $5-20 a week of
subscription credit and no capital. The workflow cap is $6 per tick, four ticks a day.

## Known limits, stated up front

- The Smarkets quote quantity unit and the exact events filter could not be verified from
  the sandbox that wrote this; `python bot/exchanges.py --probe` prints raw and scaled
  quotes so the first live run checks both in one glance. If sizes look 100x off, the knob
  is `SMARKETS_QUANTITY_SCALE`.
- CLV is measured against our own snapshots (every six hours), not tick data. The closing
  line is therefore "the last snapshot before close", which is slightly stale on markets
  that move in their final hours. This biases CLV toward zero, i.e. against us, which is the
  right direction for a gate.
- Runners of one market are correlated; the per-market cap limits, not removes, this.
  The bootstrap treats bets as independent, so the CI is a little too narrow. Read the
  CI90 as roughly a CI85 until a clustered bootstrap is added.
- Betfair's API terms are for personal use; reading delayed prices for a private paper
  test is within them, and no commercial use is made of the data.
