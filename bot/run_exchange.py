"""UK betting-exchange PAPER-trading bot: does the Manifold edge survive real-money prices?

The Manifold bot showed ~19% mark-to-market in eight weeks against a play-money book with no
commission, no spread and no arbitrageurs. Liquid Betfair/Smarkets political prices are
roughly professional-forecaster level, so the transferable edge is unknown and the honest
prior is "half of it". This bot measures that number BEFORE any pound is at risk, and it is
built to make money if the edge is real, not to score well on a benchmark:

  1. pull every quoted politics / current-affairs contract from Smarkets (public API) and
     Betfair (free delayed app key) — bot/exchanges.py, read-only by construction;
  2. quote every contract the journal already tracks BY ID (listings only return open
     markets, so this is the only way a settlement ever reaches the price file), and record
     any cross-venue back/lay lock as a rule-mismatch diagnostic;
  3. run the SAME forecast skill twice per fresh contract, BLIND (no prices, venue domains
     tool-blocked) and SIGHTED (the book on this venue AND on the matched twin venue, both
     rule texts), through run_manifold.forecast_market so budget caps, validation and the
     required market_read judgment are one implementation; plus a cheap reasoning-only
     SHADOW PROXY that never touches selection (it exists so the value of screening can be
     measured offline instead of assumed);
  4. journal the paper bet the sighted number implies: ROUTED to the venue with the larger
     pound-EV (EV per pound x the stake the book can absorb), priced at the executable side,
     sized quarter-Kelly on a notional bankroll, net of commission, capped by resting depth;
     with a MAKER quote (one tick inside the touch, ladder-snapped) recorded beside the
     taker fill so execution can be compared later;
  5. re-forecast an open position only when BOTH touches have moved against it (a pulled
     quote moves the mid without a trade), journaling the counterfactual stop-loss.

Nothing here can place an order. The go-live decision is preregistered in
docs/exchange-paper-policy.md and computed by bot/score_exchange.py on one population
(taker fills, held to settlement); every other arm is descriptive.

Usage:
    python bot/run_exchange.py --limit 6 --tier medium
    python bot/run_exchange.py --snapshot-only            # hourly, zero model credit
    python bot/run_exchange.py --fixture tests/fixtures/exchange_contracts.json --limit 2
Env:
    CLAUDE_CODE_OAUTH_TOKEN   subscription setup-token (unattended runs; local login also works)
    BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD [, BETFAIR_CERT_PATH, BETFAIR_KEY_PATH]
                              optional — Betfair is skipped when absent; Smarkets needs nothing
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

# ruff: noqa: E402  (imports follow the sys.path bootstrap above)
import exchanges
import run_bot
import run_manifold

from forecast_scaffold.core import ForecastRecord, Journal, _utc_now, clamp

DEFAULT_JOURNAL = ROOT / "bot" / "journal" / "exchange.jsonl"
DEFAULT_PRICES = ROOT / "bot" / "journal" / "exchange-prices.jsonl"
DEFAULT_ARBS = ROOT / "bot" / "journal" / "exchange-arbs.jsonl"
DEFAULT_AGENT_CMD = run_manifold.DEFAULT_AGENT_CMD

# ---- venue economics (docs/exchange-paper-policy.md) -----------------------------------
#: Commission on NET WINNINGS per market. Smarkets standard tier 2%; Betfair 6% base since
#: June 2026 (the Expert Fee above GBP 25k/yr of winnings is ignored at paper scale).
COMMISSION = {"smarkets": 0.02, "betfair": 0.06}
PAPER_BANKROLL_GBP = 10_000.0     # notional; sizing and the CLV test are scale-free anyway

# ---- selection policy ------------------------------------------------------------------
# [Red team 2026-09-06: contract SUPPLY, not compute, binds the n>=200 gate — every filter
# costs weeks of calendar; keep only the ones that protect the measurement.]
MIN_TOUCH_GBP = 5.0         # both sides must rest at least this at the touch: a book thinner
#                             than a small stake is not a price, it is a placeholder
CLOSE_MIN_DAYS = 3          # too soon and the closing line is the entry line
CLOSE_MAX_DAYS = 180        # too far and capital lock-up dominates any edge
MAX_SPREAD = 0.10           # back.prob - lay.prob; wider than this and "the price" is a guess
PRICE_BAND = (0.02, 0.98)   # mid outside this has no tradeable other side after commission
MAX_CONTRACTS_PER_MARKET = 2   # runners of one market are one correlated bet, not many
MAX_CONTRACTS_PER_EVENT = 4
REFORECAST_DEDUPE_DAYS = 3  # a contract forecast this recently is not re-forecast

# ---- paper-bet policy ------------------------------------------------------------------
DIVERGENCE_THRESHOLD = 0.03   # |p_sighted - mid| for the scorer's MOVEMENT metric only. It
#                               is deliberately NOT a bet gate: a fixed gap in probability
#                               points cannot admit a 3.5% -> 1% dead-outsider lay, which is
#                               a 2.5-point gap and one of the better pound-EV bets on the
#                               board. The EV gates below reject small gaps on their own (at
#                               even money a 1-point gap is +1% per pound: inside noise).
MIN_EXPECTED_RETURN_NET = 0.015  # EV per GBP at risk, NET of commission: a floor that only
#                               rejects bets inside commission noise. The real gate is in
#                               POUNDS (below): what is optimised is money, not a ratio.
MIN_EV_GBP_FRAC = 0.0002      # expected profit >= this x bankroll (GBP 2 at GBP 10k). A
#                               judgment lay of a 3% runner we put at 1% earns ~2% on
#                               liability but GBP 6-12 on the liability the book can absorb —
#                               several times the pound-EV of a 3-point edge on a 50/50 with
#                               GBP 50 of depth (red team 2026-09-06).
KELLY_FRACTION = 0.25
STAKE_CAP_FRAC = 0.05         # <= 5% of bankroll per contract
STAKE_FLOOR_GBP = 2.0         # Betfair's minimum back stake; below it the bet is not real
MAX_PAPER_BETS_PER_RUN = 10
LONGSHOT_MID = 0.05           # a NO on a runner priced at or below this is a "dead outsider"
#                               lay: Betfair only (its all-in/withdrawn-is-loser rules make
#                               settlement certain; Smarkets may void), and aggregate open
#                               longshot liability capped — a 1% calibration claim needs
#                               hundreds of settlements before it deserves size.
LONGSHOT_LIABILITY_CAP_FRAC = 0.10
LONGSHOT_VENUES = ("betfair",)

# ---- execution arms (descriptive; the gate is computed on TAKER fills only) --------------
MAKER_TTL_DAYS = 2.0          # a resting maker order is cancelled unfilled after this
MAKER_MIN_SPREAD_TICKS = 2    # improve the touch by one tick only when there is room to

# ---- position management ---------------------------------------------------------------
REFORECAST_ADVERSE = 0.10     # both touches moved this far against an open position
MAX_REFORECASTS_PER_RUN = 2

#: Shadow proxy (red team 2026-09-06): a cheap reasoning-only forecast journaled on every
#: forecast contract, blind and with web tools denied, that never touches selection or
#: betting. Tier "proxy" is not in config, so no source floor applies.
PROXY_TIER = "proxy"
PROXY_MODE = "proxy"

#: Blind runs must not read the venues (or the odds aggregators that mirror them).
BLIND_EXTRA_DISALLOWED = (
    "WebFetch(domain:betfair.com),WebFetch(domain:smarkets.com),"
    "WebFetch(domain:oddschecker.com),WebFetch(domain:oddsportal.com),"
    "WebFetch(domain:betfair.com.au)"
)

SIGHTED_BOOK_SECTION = (
    "\n\n## Market signals ({venue} exchange, real money)\n"
    "Best price to BACK (buy YES): {back_prob} (odds {back_odds}), GBP {back_size} available\n"
    "Best price to LAY (sell YES): {lay_prob} (odds {lay_odds}), GBP {lay_size} available\n"
    "Mid: {mid}   Last traded: {last}   Matched so far: GBP {matched}\n"
    "Commission on net winnings: {commission:.0%}\n\n"
    "This book prices the SAME contract you are forecasting: the settlement rules above ARE "
    "this market's terms, so there is no cross-platform mismatch to adjudicate. Reading this "
    "price is a REQUIRED step, not an option. But whether to move toward it is YOUR judgment "
    "call, never arithmetic: on a real-money exchange the mid of a deep, two-sided book is a "
    "strong anchor, and a thin or stale book is not. The judgment question is exactly which "
    "of two things is true — has the crowd priced in evidence you have not found (then move "
    "toward it), or is it herding, thin, or stale (then hold your own view)? You are "
    "REQUIRED to state in your reasoning WHICH of these you concluded, and why, before you "
    "lean on or away from this number, and to return that same conclusion as a REQUIRED json "
    'field "market_read" set to exactly one of "informed" | "herding" | "thin" | "stale".'
)

TWIN_BOOK_SECTION = (
    "\n\n## The same contract on {venue} (candidate match by title; real money)\n"
    "Best BACK {back_prob} (GBP {back_size}), best LAY {lay_prob} (GBP {lay_size}), "
    "mid {mid}, matched GBP {matched}, commission {commission:.0%}.\n"
    "Its settlement rules (verbatim): {rules}\n"
    "If these two venues settle the same event on different terms (withdrawal, dead heat, "
    "voiding), say so in your reasoning: a paper bet may be routed to this venue."
)


# --------------------------------------------------------------------------- selection


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def days_to_close(contract: dict[str, Any], now: datetime) -> float | None:
    close = _parse_iso(contract.get("close_time"))
    return None if close is None else (close - now).total_seconds() / 86_400.0


def spread(contract: dict[str, Any]) -> float | None:
    back, lay = contract.get("back"), contract.get("lay")
    return None if not (back and lay) else back["prob"] - lay["prob"]


def eligible(contract: dict[str, Any], now: datetime) -> bool:
    """Every filter, as a pure function of the normalised contract."""
    if contract.get("status") != "open" or contract.get("outcome") is not None:
        return False
    back, lay, mid = contract.get("back"), contract.get("lay"), contract.get("mid")
    if not (back and lay) or mid is None:
        return False
    if back["size_gbp"] < MIN_TOUCH_GBP or lay["size_gbp"] < MIN_TOUCH_GBP:
        return False
    sp = spread(contract)
    if sp is None or sp < 0 or sp > MAX_SPREAD:
        return False
    if not PRICE_BAND[0] <= mid <= PRICE_BAND[1]:
        return False
    dtc = days_to_close(contract, now)
    if dtc is None or not CLOSE_MIN_DAYS <= dtc <= CLOSE_MAX_DAYS:
        return False
    return bool(contract.get("name")) and bool(contract.get("market"))


def select_contracts(contracts: list[dict[str, Any]], limit: int, *,
                     exclude: set[str] | None = None,
                     now: datetime | None = None,
                     twins: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """Eligible contracts ranked by matched volume (deepest books first — the prices most
    worth testing against), then the sooner close, then the tighter spread; capped per market
    and per event so one contest cannot fill a batch with correlated runners. A contract's
    cross-venue twins are excluded once it is picked: the pair is forecast once and ROUTED.

    Deliberately NO model-derived ranking: a cheap forecast's divergence from the mid is
    80-90% its own noise (red team 2026-09-06), and ranking on it would select the
    population the gate is measured on for model error."""
    now = now or datetime.now(UTC)
    exclude = set(exclude or ())
    twins = twins or {}
    pool = [c for c in contracts if c["contract_id"] not in exclude and eligible(c, now)]
    pool.sort(key=lambda c: (-(c.get("matched_gbp") or 0.0), days_to_close(c, now) or 1e9,
                             spread(c) or 1.0))
    per_market: dict[str, int] = {}
    per_event: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    taken: set[str] = set()
    for c in pool:
        if c["contract_id"] in taken:
            continue
        mk = f"{c['venue']}:{c['market_id']}"
        ev = f"{c['venue']}:{c['event']}"
        # A two-runner market is ONE binary: its second runner is the same bet with the
        # sign flipped, so taking both would journal one view twice. Multi-runner markets
        # keep the small cap (runners are correlated, not identical).
        market_cap = 1 if len(c.get("runners") or []) == 2 else MAX_CONTRACTS_PER_MARKET
        if per_market.get(mk, 0) >= market_cap:
            continue
        if per_event.get(ev, 0) >= MAX_CONTRACTS_PER_EVENT:
            continue
        per_market[mk] = per_market.get(mk, 0) + 1
        per_event[ev] = per_event.get(ev, 0) + 1
        out.append(c)
        taken.add(c["contract_id"])
        taken.update(twins.get(c["contract_id"], []))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- brief


def question_title(contract: dict[str, Any]) -> str:
    event, market, name = contract.get("event", ""), contract.get("market", ""), contract["name"]
    inner = f"{market} ({event})" if event and event != market else market
    return f"Will '{name}' be the winning selection in the '{inner}' market?"


def criteria_text(contract: dict[str, Any]) -> str:
    venue = str(contract.get("venue", "")).capitalize()
    lines = [
        f"Resolves YES if {venue} settles the selection '{contract['name']}' as the WINNER of "
        f"the market '{contract.get('market', '')}' in the event '{contract.get('event', '')}', "
        "and NO if it is settled as a loser. A voided or cancelled market annuls the question.",
    ]
    runners = [r for r in contract.get("runners") or [] if r]
    if runners:
        lines.append("Selections listed in this market: " + "; ".join(runners[:25])
                     + (" (…)" if len(runners) > 25 else ""))
    if contract.get("rules"):
        lines.append("Venue settlement rules (verbatim): " + str(contract["rules"])[:3000])
    return "\n".join(lines)


def _fmt(value: Any, spec: str = ".3f") -> str:
    return "n/a" if value is None else format(value, spec)


def _book_fields(contract: dict[str, Any]) -> dict[str, Any]:
    back, lay = contract.get("back") or {}, contract.get("lay") or {}
    return dict(
        venue=str(contract.get("venue", "")).capitalize(),
        back_prob=_fmt(back.get("prob")), back_odds=_fmt(back.get("odds"), ".2f"),
        back_size=_fmt(back.get("size_gbp"), ".0f"),
        lay_prob=_fmt(lay.get("prob")), lay_odds=_fmt(lay.get("odds"), ".2f"),
        lay_size=_fmt(lay.get("size_gbp"), ".0f"),
        mid=_fmt(contract.get("mid")), last=_fmt(contract.get("last")),
        matched=_fmt(contract.get("matched_gbp"), ",.0f"),
        commission=COMMISSION.get(str(contract.get("venue")), 0.0),
    )


def build_exchange_brief(contract: dict[str, Any], sighted: bool,
                         twin: dict[str, Any] | None = None) -> str:
    """The agent-facing brief. Blind carries no price, size or volume anywhere; sighted
    carries this venue's book and, when the harness matched the contract on the other
    venue, that book and its rules too — so a bet routed there rests on rules the run read."""
    parts = [
        f"# Question: {question_title(contract)}",
        "Type: binary",
        f"Now (UTC): {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} — anchor every "
        "elapsed/remaining-time statement to this timestamp.",
        f"Closes: {contract.get('close_time') or 'unknown'} — the venue's scheduled market "
        "close; the event window itself comes from the settlement rules below.",
        "\n## Resolution criteria (verbatim — the contract)",
        criteria_text(contract),
    ]
    brief = "\n".join(parts)
    if sighted:
        brief += SIGHTED_BOOK_SECTION.format(**_book_fields(contract))
        if twin is not None:
            fields = _book_fields(twin)
            brief += TWIN_BOOK_SECTION.format(
                rules=(str(twin.get("rules") or "(none published)")[:1500]), **fields)
        brief += "\n" + run_bot.markets.market_facts_section(question_title(contract))
    return brief


# --------------------------------------------------------------------------- paper bet


def side_economics(p_us: float, contract: dict[str, Any], side: str) -> dict[str, float] | None:
    """Price paid, net odds, win probability and capacity for one side at the touch.

    YES buys at the best offer (``back.prob`` per GBP 1 payout). NO is a lay at the best bid:
    laying odds o against a backer's stake S carries liability S*(o-1), so our capital at
    risk per GBP 1 of NO-payout is (1 - lay.prob) and the resting size S supports a liability
    of S*(1-lay.prob)/lay.prob. Both sides are expressed as "stake GBP X to win b*X" so
    Kelly and the EV gate are one formula. ``b`` is NET of the venue's commission."""
    c = COMMISSION.get(str(contract.get("venue")), 0.0)
    book = contract.get("back" if side == "YES" else "lay")
    if not book:
        return None
    if side == "YES":
        price, win_p, capacity = book["prob"], p_us, book["size_gbp"]
    else:
        q = book["prob"]
        price, win_p, capacity = 1.0 - q, 1.0 - p_us, book["size_gbp"] * (1.0 - q) / q
    if not 0.0 < price < 1.0:
        return None
    b = (1.0 / price - 1.0) * (1.0 - c)
    ev = win_p * b - (1.0 - win_p)
    kelly = max(win_p - (1.0 - win_p) / b, 0.0) if b > 0 else 0.0
    return {"price": price, "b": b, "win_p": win_p, "ev_net": ev, "kelly": kelly,
            "capacity_gbp": capacity, "commission": c}


def maker_quote(contract: dict[str, Any], side: str) -> dict[str, Any] | None:
    """The resting order the maker arm would place: one ladder tick inside the touch when
    the spread has room, else joining the touch. YES rests a back order at LONGER odds than
    the best back; NO rests a lay at SHORTER odds than the best lay. Fills are judged later
    by the scorer, and only when a trade prints strictly through the price (lower bound)."""
    back, lay = contract.get("back"), contract.get("lay")
    if not (back and lay):
        return None
    ticks = exchanges.ticks_between(back["odds"], lay["odds"])
    improve = ticks >= MAKER_MIN_SPREAD_TICKS
    if side == "YES":
        touch = exchanges.snap_nearest(back["odds"])
        odds = exchanges.snap_nearest(touch + exchanges.odds_tick(touch)) if improve else touch
    else:
        touch = exchanges.snap_nearest(lay["odds"])
        odds = (exchanges.snap_nearest(touch - exchanges.odds_tick(touch - 1e-9))
                if improve else touch)
    prob = 1.0 / odds
    return {"odds": round(odds, 4), "prob": round(prob, 6), "improved": improve,
            "spread_ticks": ticks, "ttl_days": MAKER_TTL_DAYS,
            # Capital at risk per unit for the maker fill, same convention as the taker.
            "price": round(prob if side == "YES" else 1.0 - prob, 6)}


def paper_bet(p_us: float, contract: dict[str, Any], bankroll: float, *,
              already_positioned: bool = False,
              longshot_liability_open: float = 0.0) -> dict[str, Any] | None:
    """The would-be bet at the touch, or None with no side worth taking.

    Direction follows the divergence from the MID (as Manifold: YES when we are above the
    market), but the bet is priced at the executable side, which is worse than the mid by
    half the spread — the first real-money haircut the paper test must pay. The gate is in
    POUNDS: quarter-Kelly stake, capped by the 5% rule and by the depth resting at the
    touch, times the net EV per pound must clear MIN_EV_GBP_FRAC x bankroll."""
    if already_positioned:
        return None
    mid = contract.get("mid")
    if mid is None or p_us == mid:
        return None
    side = "YES" if p_us > mid else "NO"
    econ = side_economics(p_us, contract, side)
    if econ is None or econ["ev_net"] < MIN_EXPECTED_RETURN_NET:
        return None
    longshot = side == "NO" and mid <= LONGSHOT_MID
    if longshot and str(contract.get("venue")) not in LONGSHOT_VENUES:
        return None
    raw = KELLY_FRACTION * econ["kelly"] * bankroll
    cap = STAKE_CAP_FRAC * bankroll
    stake = min(raw, cap, econ["capacity_gbp"])
    capped_by = "kelly" if stake == raw else ("cap" if stake == cap else "depth")
    if longshot:
        room = LONGSHOT_LIABILITY_CAP_FRAC * bankroll - longshot_liability_open
        if room < STAKE_FLOOR_GBP:
            return None
        if stake > room:
            stake, capped_by = room, "longshot_cap"
    if stake < STAKE_FLOOR_GBP:
        return None
    ev_gbp = stake * econ["ev_net"]
    if ev_gbp < MIN_EV_GBP_FRAC * bankroll:
        return None
    return {
        "venue": contract["venue"], "contract_id": contract["contract_id"],
        "outcome": side, "stake_gbp": round(stake, 2), "price": round(econ["price"], 6),
        "odds": round(1.0 / econ["price"], 4), "net_odds_b": round(econ["b"], 4),
        "expected_return_net": round(econ["ev_net"], 4), "ev_gbp": round(ev_gbp, 2),
        "kelly_raw_gbp": round(raw, 2), "capacity_gbp": round(econ["capacity_gbp"], 2),
        "capped_by": capped_by, "commission": econ["commission"],
        "mid_at_bet": round(mid, 6), "longshot": longshot, "bankroll_gbp": bankroll,
        "maker": maker_quote(contract, side), "dry_run": True,
    }


def route_bet(p_us: float, candidates: list[dict[str, Any]], bankroll: float, *,
              positioned: set[str], longshot_liability_open: float = 0.0,
              ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Price the same forecast on every venue that lists the contract and take the venue
    with the larger POUND-EV (EV per pound x the stake its book absorbs). Returns
    (chosen contract, bet, alternatives) — the alternatives are journaled so routing can be
    scored later (was the thinner-but-cheaper venue ever the right call?)."""
    priced: list[tuple[dict[str, Any], dict[str, Any]]] = []
    alternatives: list[dict[str, Any]] = []
    for c in candidates:
        bet = paper_bet(p_us, c, bankroll, already_positioned=c["contract_id"] in positioned,
                        longshot_liability_open=longshot_liability_open)
        alternatives.append({"venue": c["venue"], "contract_id": c["contract_id"],
                             "mid": c.get("mid"), "ev_gbp": bet["ev_gbp"] if bet else None,
                             "stake_gbp": bet["stake_gbp"] if bet else None})
        if bet is not None:
            priced.append((c, bet))
    if not priced:
        return None, None, alternatives
    chosen, bet = max(priced, key=lambda cb: cb[1]["ev_gbp"])
    if len(candidates) > 1:
        bet["route"] = {"chosen": chosen["venue"], "alternatives": alternatives}
    return chosen, bet, alternatives


# --------------------------------------------------------------------------- journal


def build_record(contract: dict[str, Any], forecast: dict[str, Any], pair_id: str, *,
                 bet: dict[str, Any] | None = None, bet_contract: dict[str, Any] | None = None,
                 reforecast_of: str | None = None) -> ForecastRecord:
    """One journal record per mode. ``dry_run`` is ALWAYS True here: this bot never trades.
    The book at forecast time rides in ``source.book`` and the mid in ``crowd``. A routed
    bet carries the venue it was priced on in ``source.paper_bet.venue``."""
    source: dict[str, Any] = {
        "platform": contract["venue"],
        "question_id": contract["contract_id"],
        "url": contract.get("url", ""),
        "pair_id": pair_id,
        # "blind" | "sighted" | "proxy" — the proxy is a blind call at the proxy tier; the
        # pair's blind record is the only one scored as the A/B's blind arm.
        "mode": forecast.get("record_mode") or forecast["mode"],
        "contract": {
            "event": contract.get("event", ""), "market": contract.get("market", ""),
            "name": contract["name"], "n_runners": len(contract.get("runners") or []),
            "market_id": contract.get("market_id"), "selection_id": contract.get("selection_id"),
        },
        "book": {k: contract.get(k) for k in ("back", "lay", "mid", "last", "matched_gbp")},
    }
    if forecast.get("market_read"):
        source["market_read"] = forecast["market_read"]
    if reforecast_of:
        source["reforecast_of"] = reforecast_of
    if bet is not None:
        bet = dict(bet)
        target = bet_contract or contract
        if target["contract_id"] != contract["contract_id"]:
            bet["book"] = {k: target.get(k) for k in ("back", "lay", "mid", "last", "matched_gbp")}
        source["paper_bet"] = bet
    mid = contract.get("mid")
    return ForecastRecord(
        question=question_title(contract)[:500],
        question_type="binary",
        resolution_criterion=criteria_text(contract)[:2000],
        forecast_at=_utc_now(),
        resolve_by=(contract.get("close_time") or "")[:10] or None,
        source=source,
        reference_class=forecast["reference_class"],
        base_rate=forecast["base_rate"],
        probability=clamp(float(forecast["probability"]), 0.01, 0.99),
        raw_draws=forecast["raw_draws"],
        effort=forecast.get("tier"),
        model=forecast["model"] or run_bot._model_from_cmd(forecast["agent_cmd"]),
        provider=forecast.get("provider"),
        blind=forecast["blind"],
        dry_run=True,
        cost_usd=forecast["cost_usd"] or None,
        crowd={
            "value": mid, "source": f"{contract['venue']} exchange mid",
            "at": _utc_now(), "shown_to_agent": not forecast["blind"],
        } if mid is not None else None,
        reasoning=forecast["reasoning"],
        what_would_change_my_mind=forecast["what_would_change_my_mind"],
        research=(
            {"n_searches": len(forecast["sources"]), "sources": forecast["sources"]}
            if forecast["sources"] else None
        ),
    )


def journal_rows(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _bet_of(row: dict[str, Any]) -> dict[str, Any] | None:
    src = row.get("source") or {}
    bet = src.get("paper_bet")
    return bet if isinstance(bet, dict) else None


def tracked_contract_ids(rows: list[dict[str, Any]], settled: set[str] | None = None) -> set[str]:
    """Every contract id the journal refers to (forecast or bet on) and not yet settled."""
    settled = settled or set()
    out: set[str] = set()
    for r in rows:
        src = r.get("source") or {}
        for cid in (src.get("question_id"), (src.get("paper_bet") or {}).get("contract_id")):
            if cid and str(cid) not in settled:
                out.add(str(cid))
    return out


def positioned_contract_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(b["contract_id"]) for r in rows if (b := _bet_of(r)) and b.get("contract_id")}


def open_longshot_liability(rows: list[dict[str, Any]], settled: set[str]) -> float:
    return sum(float(b["stake_gbp"]) for r in rows
               if (b := _bet_of(r)) and b.get("longshot")
               and str(b.get("contract_id")) not in settled)


def recently_forecast_ids(rows: list[dict[str, Any]], now: datetime | None = None,
                          days: float = REFORECAST_DEDUPE_DAYS) -> set[str]:
    now = now or datetime.now(UTC)
    out: set[str] = set()
    for r in rows:
        src = r.get("source") or {}
        at = _parse_iso(r.get("forecast_at") or r.get("created"))
        if src.get("question_id") and at and (now - at) <= timedelta(days=days):
            out.add(str(src["question_id"]))
    return out


def snapshot_rows(contracts: dict[str, dict[str, Any]],
                  at: str | None = None) -> list[dict[str, Any]]:
    """One price row per contract: what the scorer needs for closing line + settlement."""
    at = at or _utc_now()
    rows = []
    for cid, c in sorted(contracts.items()):
        rows.append({
            "at": at, "contract_id": cid, "venue": c.get("venue"),
            "back": c.get("back"), "lay": c.get("lay"), "mid": c.get("mid"),
            "last": c.get("last"), "matched_gbp": c.get("matched_gbp"),
            "status": c.get("status"), "outcome": c.get("outcome"),
            "close_time": c.get("close_time"),
        })
    return rows


def settled_ids(prices_path: str | Path) -> set[str]:
    return {str(r["contract_id"]) for r in journal_rows(prices_path)
            if r.get("outcome") is not None}


def append_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


append_snapshots = append_jsonl


def lock_rows(contracts: list[dict[str, Any]], twins: dict[str, list[str]],
              at: str | None = None) -> list[dict[str, Any]]:
    """Cross-venue back/lay locks observed this tick. A ledger, not a trade list: locks
    that survive two consecutive ticks are almost always a settlement-rule mismatch
    between the venues (the scorer flags persistence), so both rule texts ride along."""
    at = at or _utc_now()
    by_id = {c["contract_id"]: c for c in contracts}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for cid, others in twins.items():
        for oid in others:
            key = tuple(sorted((cid, oid)))
            if key in seen or cid not in by_id or oid not in by_id:
                continue
            seen.add(key)
            lock = exchanges.cross_venue_lock(by_id[cid], by_id[oid], COMMISSION)
            if lock is None:
                continue
            a, b = by_id[lock["back_id"]], by_id[lock["lay_id"]]
            rows.append({"at": at, "pair": list(key), **lock,
                         "title": question_title(a)[:160],
                         "rules_back": str(a.get("rules") or "")[:600],
                         "rules_lay": str(b.get("rules") or "")[:600]})
    return rows


def adverse_positions(rows: list[dict[str, Any]], quotes: dict[str, dict[str, Any]],
                      settled: set[str]) -> list[dict[str, Any]]:
    """Open paper bets whose BOTH touches have moved >= REFORECAST_ADVERSE against them
    since entry, not yet re-forecast. A pulled quote moves the mid without a trade; two
    touches moving together is a repricing."""
    already = {str((r.get("source") or {}).get("reforecast_of")) for r in rows}
    out: list[dict[str, Any]] = []
    for r in rows:
        bet = _bet_of(r)
        src = r.get("source") or {}
        if not bet or src.get("reforecast_of") or src.get("pair_id") in already:
            continue
        cid = str(bet.get("contract_id"))
        if cid in settled or cid not in quotes:
            continue
        book0 = bet.get("book") or src.get("book") or {}
        now_c = quotes[cid]
        b0, l0, b1, l1 = book0.get("back"), book0.get("lay"), now_c.get("back"), now_c.get("lay")
        if not (b0 and l0 and b1 and l1):
            continue
        if bet["outcome"] == "YES":
            moved = (b0["prob"] - b1["prob"] >= REFORECAST_ADVERSE
                     and l0["prob"] - l1["prob"] >= REFORECAST_ADVERSE)
        else:
            moved = (b1["prob"] - b0["prob"] >= REFORECAST_ADVERSE
                     and l1["prob"] - l0["prob"] >= REFORECAST_ADVERSE)
        if moved:
            out.append({"record": r, "contract": now_c, "pair_id": src.get("pair_id")})
    return out


def _merge_names(quote: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """By-id quotes carry no names on Betfair; take them from the journal record."""
    meta = (record.get("source") or {}).get("contract") or {}
    c = dict(quote)
    for k in ("event", "market", "name"):
        if not c.get(k) and meta.get(k):
            c[k] = meta[k]
    c.setdefault("runners", [])
    return c


# --------------------------------------------------------------------------- run


def _forecast_pair(contract: dict[str, Any], twin: dict[str, Any] | None,
                   args: argparse.Namespace, config: dict[str, Any],
                   budget_state: dict[str, Any], deadline: float | None,
                   ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    def brief(c: dict[str, Any], sighted: bool) -> str:
        return build_exchange_brief(c, sighted, twin=twin if sighted else None)

    blind_fc = run_manifold.forecast_market(
        contract, "blind", args.tier, args, config, budget_state, deadline,
        brief_builder=brief, extra_blind_disallowed=BLIND_EXTRA_DISALLOWED)
    if blind_fc is None:
        print("  skip: blind forecast failed; sighted call not started")
        return None, None
    sighted_fc = run_manifold.forecast_market(
        contract, "sighted", args.tier, args, config, budget_state, deadline,
        brief_builder=brief, extra_blind_disallowed=BLIND_EXTRA_DISALLOWED)
    if sighted_fc is None:
        print("  skip: sighted forecast failed")
        return blind_fc, None
    for fc in (blind_fc, sighted_fc):
        fc["tier"] = args.tier
        fc["provider"] = args.provider
    return blind_fc, sighted_fc


def _forecast_proxy(contract: dict[str, Any], args: argparse.Namespace,
                    config: dict[str, Any], budget_state: dict[str, Any],
                    deadline: float | None) -> dict[str, Any] | None:
    fc = run_manifold.forecast_market(
        contract, "blind", PROXY_TIER, args, config, budget_state, deadline,
        brief_builder=build_exchange_brief,
        extra_blind_disallowed=BLIND_EXTRA_DISALLOWED + "," + run_bot.NO_WEB_DISALLOWED)
    if fc is not None:
        fc.update({"tier": PROXY_TIER, "provider": args.provider, "record_mode": PROXY_MODE})
    return fc


def _budget_stop(budget_state: dict[str, Any], budget: float, deadline: float | None) -> bool:
    if float(budget_state["usd"]) >= budget - run_manifold.BUDGET_EPSILON_USD:
        print("credit cap reached; leaving remaining work for the next run")
        return True
    if deadline is not None and time.monotonic() >= deadline:
        print("wall-clock deadline reached; leaving remaining work for the next run")
        return True
    return False


def run(args: argparse.Namespace) -> int:
    snapshot_only = bool(getattr(args, "snapshot_only", False))
    if getattr(args, "provider", "subscription") != "subscription":
        print("exchange paper bot is subscription-only; refusing a non-subscription provider")
        return 2
    budget = float(getattr(args, "budget", run_manifold.MAX_CREDIT_BUDGET_USD) or 0.0)
    if (not math.isfinite(budget) or budget <= 0
            or budget > run_manifold.MAX_CREDIT_BUDGET_USD + run_manifold.BUDGET_EPSILON_USD):
        print(f"credit budget must be > $0 and <= ${run_manifold.MAX_CREDIT_BUDGET_USD:.2f}")
        return 2
    if not snapshot_only:
        auth_error = run_manifold.subscription_auth_error(
            bool(getattr(args, "require_subscription_auth", False)))
        if auth_error:
            print(f"subscription-auth preflight failed: {auth_error}")
            return 2
    deadline_minutes = float(getattr(args, "deadline_minutes", 0.0) or 0.0)
    deadline = time.monotonic() + deadline_minutes * 60 if deadline_minutes > 0 else None
    budget_state: dict[str, Any] = {
        "usd": 0.0, "uncertain": False, "subscription_deferred": False, "budget_deferred": False,
    }
    print("PAPER — nothing is traded. "
          + ("snapshot-only tick (no model calls)" if snapshot_only
             else f"Claude subscription credit cap: ${budget:.2f} this run"))

    config = run_bot.load_config()
    journal_path = args.journal or str(DEFAULT_JOURNAL)
    prices_path = args.prices or str(DEFAULT_PRICES)
    arbs_path = getattr(args, "arbs", None) or str(DEFAULT_ARBS)
    Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
    journal = Journal(journal_path)
    rows_before = journal_rows(journal_path)
    now = datetime.now(UTC)
    fixture = getattr(args, "fixture", None)
    venues = tuple(args.venue or ("smarkets", "betfair"))

    # ---- 1. the listing (selection + locks) and the by-id quotes of tracked contracts ----
    contracts = exchanges.load_contracts(venues=venues, fixture=fixture)
    by_id = {c["contract_id"]: c for c in contracts}
    twins = exchanges.match_contracts(contracts)
    print(f"pulled {len(contracts)} quoted contract(s) from {', '.join(venues)}; "
          f"{sum(len(v) for v in twins.values()) // 2} cross-venue twin pair(s)")

    settled = settled_ids(prices_path)
    tracked = tracked_contract_ids(rows_before, settled)
    quotes = exchanges.quote_contracts(tracked, fixture=fixture) if tracked else {}
    # A fresh listing quote beats a by-id one when both exist (it carries names/rules).
    for cid in list(quotes):
        if cid in by_id:
            quotes[cid] = by_id[cid]
    snaps = snapshot_rows(quotes)
    append_jsonl(prices_path, snaps)
    newly_settled = sorted(s["contract_id"] for s in snaps if s.get("outcome") is not None)
    missing = sorted(cid for cid in tracked if cid not in quotes)
    print(f"snapshot: {len(snaps)} tracked contract(s) quoted"
          + (f", {len(newly_settled)} settled" if newly_settled else "")
          + (f", {len(missing)} unavailable" if missing else ""))
    locks = lock_rows(contracts, twins)
    append_jsonl(arbs_path, locks)
    if locks:
        print(f"lock ledger: {len(locks)} cross-venue lock(s) observed "
              f"(best {max(row['lock_return_on_capital'] for row in locks):+.2%} on capital)")
    if snapshot_only:
        print("done (snapshot-only)")
        return 0

    # ---- 2. re-forecast open positions that BOTH touches moved against ----------------
    settled |= set(newly_settled)
    reforecasts = 0
    for hit in adverse_positions(rows_before, quotes, settled)[:MAX_REFORECASTS_PER_RUN]:
        if _budget_stop(budget_state, budget, deadline):
            break
        contract = _merge_names(hit["contract"], hit["record"])
        side = hit["record"]["source"]["paper_bet"]["outcome"]
        print(f"- REFORECAST {question_title(contract)[:80]!r} (both touches moved "
              f">= {REFORECAST_ADVERSE:.0%} against the open {side})")
        blind_fc, sighted_fc = _forecast_pair(contract, None, args, config, budget_state,
                                              deadline)
        if blind_fc is None or sighted_fc is None:
            continue
        pair_id = f"{_utc_now()[:10]}-{uuid4().hex[:8]}"
        for fc in (blind_fc, sighted_fc):
            journal.append(build_record(contract, fc, pair_id, reforecast_of=hit["pair_id"]))
        reforecasts += 1
        print(f"  journaled re-forecast pair {pair_id} (blind {blind_fc['probability']:.2f} / "
              f"sighted {sighted_fc['probability']:.2f}); stop-loss counterfactual scored offline")

    # ---- 3. fresh contracts: forecast once, route the bet ------------------------------
    fresh = recently_forecast_ids(rows_before, now)
    positioned = positioned_contract_ids(rows_before)
    longshot_open = open_longshot_liability(rows_before, settled)
    selected = select_contracts(contracts, args.limit, exclude=fresh, now=now, twins=twins)
    print(f"selected {len(selected)} contract(s)")
    if not selected and not reforecasts:
        print(f"credit usage accounted: ${float(budget_state['usd']):.2f} / ${budget:.2f}")
        return 0

    bets = 0
    for contract in selected:
        if _budget_stop(budget_state, budget, deadline):
            break
        cid = contract["contract_id"]
        twin_ids = [t for t in twins.get(cid, []) if t in by_id]
        twin = by_id[twin_ids[0]] if twin_ids else None
        print(f"- {question_title(contract)[:90]!r} (mid={contract.get('mid')}, "
              f"matched=GBP {contract.get('matched_gbp')}"
              + (f", twin on {twin['venue']} mid={twin.get('mid')}" if twin else "") + ")")
        blind_fc, sighted_fc = _forecast_pair(contract, twin, args, config, budget_state,
                                              deadline)
        if blind_fc is None or sighted_fc is None:
            continue
        pair_id = f"{_utc_now()[:10]}-{uuid4().hex[:8]}"
        journal.append(build_record(contract, blind_fc, pair_id))
        if getattr(args, "proxy", True):
            proxy_fc = _forecast_proxy(contract, args, config, budget_state, deadline)
            if proxy_fc is not None:
                journal.append(build_record(contract, proxy_fc, pair_id))
                print(f"  proxy {proxy_fc['probability']:.2f} (reasoning-only, descriptive)")

        p_sighted = sighted_fc["probability"]
        candidates = [contract] + ([twin] if twin is not None else [])
        chosen, bet, _alts = route_bet(
            p_sighted, candidates, float(args.bankroll),
            positioned=positioned if bets < args.max_bets else set(positioned) | {cid},
            longshot_liability_open=longshot_open)
        if bet is not None and chosen is not None:
            bets += 1
            positioned.add(chosen["contract_id"])
            if bet.get("longshot"):
                longshot_open += bet["stake_gbp"]
            maker = bet["maker"]["prob"] if bet.get("maker") else "n/a"
            print(f"  PAPER-BET {bet['outcome']} GBP {bet['stake_gbp']:.2f} at {bet['price']:.3f}"
                  f" on {chosen['venue']} (p_us={p_sighted:.2f} vs mid {chosen['mid']:.3f}, "
                  f"EV GBP {bet['ev_gbp']:.2f} = {bet['expected_return_net']:+.1%} net, capped "
                  f"by {bet['capped_by']}, maker {maker}, read={sighted_fc.get('market_read')})")
        journal.append(build_record(contract, sighted_fc, pair_id, bet=bet, bet_contract=chosen))
        print(f"  journaled pair {pair_id} (blind {blind_fc['probability']:.2f} / "
              f"sighted {p_sighted:.2f})")
        # Entry snapshots: the books the bet was priced against, in the price file too.
        entry = {cid: contract}
        if twin is not None:
            entry[twin["contract_id"]] = twin
        append_jsonl(prices_path, snapshot_rows(entry))

    print(f"done (paper): {len(selected)} contract(s), {bets} paper bet(s), "
          f"{reforecasts} re-forecast(s)")
    qualifier = " (unknown usage reserved)" if budget_state["uncertain"] else ""
    print(f"credit usage accounted: ${float(budget_state['usd']):.2f} / ${budget:.2f}{qualifier}")
    if budget_state.get("budget_deferred"):
        print("BUDGET-DEFER: Claude --max-budget-usd cap reached; next tick will retry")
        return 0
    if budget_state.get("subscription_deferred"):
        print("SUBSCRIPTION-DEFER: Claude session limit; next tick will retry")
        return 0
    return 1 if budget_state["uncertain"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=6,
                        help="how many contracts to forecast this run (default 6)")
    parser.add_argument("--tier", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--venue", action="append", choices=["smarkets", "betfair"],
                        help="restrict to a venue (repeatable; default both)")
    parser.add_argument("--fixture", default=None,
                        help="JSON file of normalised contracts instead of the live venues")
    parser.add_argument("--snapshot-only", dest="snapshot_only", action="store_true",
                        help="quote tracked contracts, record locks, no model calls")
    parser.add_argument("--bankroll", type=float, default=PAPER_BANKROLL_GBP,
                        help=f"notional GBP bankroll for sizing (default {PAPER_BANKROLL_GBP:.0f})")
    parser.add_argument("--max-bets", dest="max_bets", type=int, default=MAX_PAPER_BETS_PER_RUN)
    parser.add_argument("--no-proxy", dest="proxy", action="store_false",
                        help="skip the shadow reasoning-only proxy forecast per contract")
    parser.add_argument("--provider", default="subscription", choices=("subscription",))
    parser.add_argument("--budget", type=float, default=run_manifold.MAX_CREDIT_BUDGET_USD,
                        help="Claude subscription credit cap for this invocation")
    parser.add_argument("--deadline-minutes", type=float, default=0.0)
    parser.add_argument("--require-subscription-auth", action="store_true")
    parser.add_argument("--timeout", type=int, default=1200, help="seconds per agent call")
    parser.add_argument("--agent-cmd", dest="agent_cmd", default=DEFAULT_AGENT_CMD)
    parser.add_argument("--journal", default=None, help=f"default {DEFAULT_JOURNAL}")
    parser.add_argument("--prices", default=None, help=f"default {DEFAULT_PRICES}")
    parser.add_argument("--arbs", default=None, help=f"default {DEFAULT_ARBS}")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
