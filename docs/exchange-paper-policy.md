# Exchange paper-trading bot: policy, strategy and preregistered go-live rule

Adopted 2026-09-06; strategy layer added the same day after two red-team passes (summaries
below). This document is the authority; `bot/run_exchange.py` and `bot/score_exchange.py`
implement it. Amend here first.

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

Hourly (`.github/workflows/exchange-paper.yml`). Every tick, at zero model credit:

1. Pull every quoted politics / current-affairs contract from both venues
   (`bot/exchanges.py`, read-only by construction — there is no order code path), and
   match the same contract across venues by event + market + runner tokens and close date.
2. **Quote every contract the journal tracks BY ID** into `bot/journal/exchange-prices.jsonl`.
   Listings only return open markets, so by-id quoting is the only way a settlement ever
   reaches the price file (a bug the red team caught in the first version).
3. Record any **cross-venue back/lay lock** (after both commissions) to
   `bot/journal/exchange-arbs.jsonl` with both venues' rule texts. This is a diagnostic,
   not a trade list: a lock that persists across consecutive ticks is almost always a
   settlement-rule mismatch (withdrawal, dead heat, voiding), which is exactly what must be
   known before the same contract is ever routed between venues for real.

On the ticks at 00, 06, 12 and 18 UTC, subscription-only and hard-capped at $6 of credit:

4. **Re-forecast** an open paper position only when BOTH touches have moved at least 10
   points against it since entry (a pulled quote moves the mid without a trade; two
   touches moving together is a repricing). At most two per tick; the counterfactual
   stop-loss is scored offline.
5. **Select** fresh contracts: open, two-sided, at least GBP 5 at the touch on both sides,
   spread at most 10 points, mid within [0.02, 0.98], closing 3-180 days out, at most two
   runners per market (one for a two-runner market) and four per event, not forecast in
   the last 3 days; a contract's cross-venue twin is not forecast separately. Ranked by
   matched volume, then the sooner close, then the tighter spread. **Never by a model's
   divergence from the mid**: a cheap forecast's divergence is 80-90% its own noise, and
   ranking on it would select the population the gate is measured on for model error.
6. **Forecast** each contract twice through the skill and loop the Manifold bot uses
   (`run_manifold.forecast_market`): BLIND (no prices; betfair.com, smarkets.com and the
   odds aggregators tool-blocked) and SIGHTED (this venue's book, the twin venue's book and
   rules when matched, commission, and the required `market_read` judgment). Plus a
   **shadow proxy**: one reasoning-only call (web tools denied) journaled with
   `source.mode = "proxy"` that never touches selection or betting; it exists so the value
   of screening can be measured offline as corr(|p_proxy − mid|, CLV) instead of assumed.
7. **Paper bet** on the sighted number, **routed** to the venue with the larger pound-EV:
   side by the sign of (p − mid), priced at the executable side, sized quarter-Kelly on a
   notional GBP 10,000, capped at 5% of bankroll and by the depth resting at the touch,
   floor GBP 2. Gates are in POUNDS: net EV per pound ≥ 1.5% (commission noise) AND
   expected profit ≥ GBP 2. A NO on a runner at or below 5% is a **dead-outsider lay**:
   Betfair only (its all-in / withdrawn-is-loser rules make settlement certain; Smarkets may
   void) and aggregate open longshot liability ≤ 10% of bankroll. Beside the taker fill a
   **maker quote** is journaled: one ladder tick inside the touch when the spread has room,
   else joining the touch. Nothing is ever sent to a venue.

## Preregistered decision rule (computed by `bot/score_exchange.py verdict`)

The gate is computed on ONE population: **taker fills at the touch, held to settlement,
every paper bet** (no venue, execution-arm or longshot sub-population), and on **absolute
closing-line value in probability points** — `q_close − q_entry` on the side taken, where
`q_close` is that side's mid at the last open snapshot. Relative CLV has a variance
dominated by low-priced bets and would let a few longshots decide the verdict. CLV needs no
settlement and is immune to outcome variance; it is the standard fast test of a betting edge.

**GO-LIVE-CANDIDATE** when ALL hold, pooled across venues:

| leg | threshold |
|---|---|
| paper bets with a scored closing line | n >= 200 |
| mean CLV points | bootstrap CI90 lower bound > 0 (10k draws, seed 7) |
| settled paper bets | n >= 100 |
| settled ROI net of commission | > 0 |
| Brier(sighted) - Brier(book mid at entry) | <= -0.01 over the settled set |

**KILL** when n >= 200 scored bets and mean CLV points <= 0: the edge did not transfer;
the bot continues forecast-only as a public benchmark, and no live phase is opened.

**HOLD** otherwise. No live betting, no sizing decisions, no "it looks good so far".

A GO-LIVE-CANDIDATE verdict authorises only the *proposal* of a live phase 1 (flat GBP 5
stakes, Smarkets first at 2% commission) under a separate policy document; it does not
authorise a trade.

### Everything else the scorer prints is DESCRIPTIVE

With n ≈ 200 nothing else is powered, and sixteen sub-populations under a one-sided 5% gate
would be a 30-55% family-wise false-GO rate. So these are reported, never gated on:

- **Maker vs taker**, paired within bet. A maker order counts as FILLED only when a later
  snapshot shows a NEW last-traded price (changed since the previous observation) that
  printed *strictly through* our price within a 2-day TTL — a touch that merely crossed it,
  a print exactly at it, or an unchanged stale print is not a fill (most top-of-book moves
  in politics books are pulls, not trades). This is a lower bound; an honest fill model
  needs traded-volume ladders, which the delayed Betfair key withholds.
- **Exit past fair value vs hold**: close the position at the first snapshot where the
  executable exit side sits at or past our own fair value. Exiting earlier pays the spread
  again to free capital that is idle anyway.
- **Stop-loss vs hold** for re-forecast positions, **longshot lays** P&L, **routing**
  counts, **lock-ledger persistence**, and the **shadow-proxy** correlation.

## Costs

Forecast pair ≈ $1.25 of subscription credit, the proxy ≈ $0.10-0.20 (Sonnet-5). Contract
supply, not credit, binds: the red team measured that at the venues' throughput the bot
spends $3-15 a week against a $168 weekly cap, and that the n >= 200 gate takes months at
0.4-2.5 bets a week — which is why every selection filter above is as loose as the
measurement allows, and why the election window (US midterms 2026-11-03, when per-race
books carry GBP 1k-10k at the touch) is the time to raise `--limit`.

## What was proposed and dropped, and why

- **LLM screen before full research, with an exploration arm.** Compute is not the binding
  input; a reasoning-only proxy has ~0.15 sd of noise against a 0.05 market, so screening on
  it selects for model error and buys no enrichment per research dollar; the exploration
  arm would need 400-1,000 bets per arm to say anything. Kept only as the shadow proxy.
- **Within-market overround arbitrage.** Betfair's cross-matching fills those itself; a
  snapshot showing one is delayed data or a suspended runner.
- **EV-per-day hurdle.** It binds only on 100-180-day closes and prunes exactly the stale,
  wide, long-dated books where a slow judgment edge lives, to free capital worth ~0.
- **Mid-triggered re-forecasts and convergence exits at the mid.** A one-sided pull moves
  the mid four points on an eight-point book with no trade.
- **Polymarket lead-lag arbitrage.** Minutes-scale, competed, and the bot is slow.
- **Market making.** Hourly snapshots cannot simulate two-sided fills honestly.

## Known limits, stated up front

- Smarkets units and fields were verified live on 2026-09-06 against the venue's OpenAPI
  spec (real payloads in `tests/fixtures/smarkets_open_raw.json` / `smarkets_settled_raw.json`).
  What the sandbox had guessed wrong, now fixed: a quote `quantity` is the resting order's
  TOTAL POT in 1/10000 GBP, so the backer stake at the touch is `quantity x price / 1e8`
  (the first version overstated depth about 2x at evens and 20x on a 5% runner); the
  events listing is id-ascending and paged by 100, so one page returned only the oldest
  container events and none of the recent by-elections (all pages are walked now: 213
  events, 601 contracts, 139 eligible); settlement is `contract.state_or_outcome`
  (winner / loser / deadheat / voided / reduced) with market `state == "settled"`, and the
  quote, volume and last-price endpoints answer empty after settlement; matched volume and
  the last executed price are separate endpoints (`/volumes/` in whole GBP,
  `/last_executed_prices/` as a percent string), not fields of the market or quote.
  A dead heat is journaled as closed with no outcome. The Betfair delayed-key catalogue and
  closed-market book are still unverified live (the key is the operator's).
- CLV is measured against hourly snapshots, not tick data. The closing line is "the last
  open snapshot", slightly stale on markets that move in their final hour. This biases CLV
  toward zero, i.e. against us, which is the right direction for a gate.
- Runners of one market are correlated; the per-market cap limits, not removes, this.
  The bootstrap treats bets as independent, so the CI is a little too narrow. Read the
  CI90 as roughly a CI85 until a clustered bootstrap is added.
- The maker fill test is a lower bound (above). The odds ladder applied is Betfair's;
  Smarkets' finer ticks make its maker arm slightly conservative.
- A contract and its cross-venue twin are one bet: dedupe and position guards close over
  twins, the entry snapshot carries the pair's own timestamp and is never a closing line,
  a routed bet is scored only against its own venue's snapshots, voided contracts
  (withdrawn runners, cancelled markets) and ids a venue stops returning are retired from
  tracking — each of these was a defect the code red team reproduced in the first version.
- Betfair's API terms are for personal use; reading delayed prices for a private paper
  test is within them, and no commercial use is made of the data. The venue login is
  hidden from the agent subprocess like every other credential.
