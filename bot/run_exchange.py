"""UK betting-exchange PAPER-trading bot: does the Manifold edge survive real-money prices?

The Manifold bot showed ~19% mark-to-market in eight weeks against a play-money book with no
commission, no spread and no arbitrageurs. Liquid Betfair/Smarkets political prices are
roughly professional-forecaster level, so the transferable edge is unknown and the honest
prior is "half of it". This bot measures that number BEFORE any pound is at risk:

  1. pull every quoted politics / current-affairs contract from Smarkets (public API) and
     Betfair (free delayed app key) — bot/exchanges.py, read-only by construction;
  2. run the SAME forecast skill twice per contract, BLIND (no prices, venue domains
     tool-blocked) and SIGHTED (the book: back/lay/mid/last/matched), through
     run_manifold.forecast_market so budget caps, validation, source floor and the required
     market_read judgment are one implementation;
  3. journal both forecasts and the paper bet the sighted number implies at the TOUCH price
     actually available, sized quarter-Kelly on a notional bankroll, capped by the size
     resting at that price and net of the venue's commission;
  4. snapshot the book of every open journaled contract each run, so closing-line value and
     settlement can be scored offline (bot/score_exchange.py) with no further API calls.

Nothing here can place an order. The go-live decision is preregistered in
docs/exchange-paper-policy.md and computed by the scorer, not argued from a chart.

Usage:
    python bot/run_exchange.py --limit 6 --tier medium
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
DEFAULT_AGENT_CMD = run_manifold.DEFAULT_AGENT_CMD

# ---- venue economics (docs/exchange-paper-policy.md) -----------------------------------
#: Commission on NET WINNINGS per market. Smarkets standard tier 2%; Betfair 6% base since
#: June 2026 (the Expert Fee above GBP 25k/yr of winnings is ignored at paper scale).
COMMISSION = {"smarkets": 0.02, "betfair": 0.06}
PAPER_BANKROLL_GBP = 10_000.0     # notional; sizing and the CLV test are scale-free anyway

# ---- selection policy ------------------------------------------------------------------
MIN_TOUCH_GBP = 10.0        # both sides must rest at least this at the touch: a book thinner
#                             than a small stake is not a price, it is a placeholder
CLOSE_MIN_DAYS = 3          # too soon and the closing line is the entry line
CLOSE_MAX_DAYS = 180        # too far and capital lock-up dominates any edge
MAX_SPREAD = 0.08           # back.prob - lay.prob; wider than this and "the price" is a guess
PRICE_BAND = (0.02, 0.98)   # mid outside this has no tradeable other side after commission
MAX_CONTRACTS_PER_MARKET = 2   # runners of one market are one correlated bet, not many
MAX_CONTRACTS_PER_EVENT = 3
REFORECAST_DEDUPE_DAYS = 3  # a contract forecast this recently is not re-forecast

# ---- paper-bet policy ------------------------------------------------------------------
DIVERGENCE_THRESHOLD = 0.03   # |p_sighted - mid| must clear this (hysteresis, as Manifold)
MIN_EXPECTED_RETURN_NET = 0.05  # EV per GBP at risk, NET of commission, on the chosen side.
#                               Manifold's gate is 0.08 gross with no commission; 0.05 net
#                               at 2-6% commission is the same bar expressed honestly.
KELLY_FRACTION = 0.25
STAKE_CAP_FRAC = 0.05         # <= 5% of bankroll per contract
STAKE_FLOOR_GBP = 2.0         # Betfair's minimum back stake; below it the bet is not real
MAX_PAPER_BETS_PER_RUN = 10

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
                     now: datetime | None = None) -> list[dict[str, Any]]:
    """Eligible contracts ranked by matched volume (deepest books first — the ones whose
    price is worth testing against), then tighter spread; capped per market and per event so
    one contest cannot fill a batch with correlated runners."""
    now = now or datetime.now(UTC)
    exclude = exclude or set()
    pool = [c for c in contracts if c["contract_id"] not in exclude and eligible(c, now)]
    pool.sort(key=lambda c: (-(c.get("matched_gbp") or 0.0), spread(c) or 1.0))
    per_market: dict[str, int] = {}
    per_event: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for c in pool:
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


def build_exchange_brief(contract: dict[str, Any], sighted: bool) -> str:
    """The agent-facing brief. Blind carries no price, size or volume anywhere."""
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
        back, lay = contract.get("back") or {}, contract.get("lay") or {}
        brief += SIGHTED_BOOK_SECTION.format(
            venue=str(contract.get("venue", "")).capitalize(),
            back_prob=_fmt(back.get("prob")), back_odds=_fmt(back.get("odds"), ".2f"),
            back_size=_fmt(back.get("size_gbp"), ".0f"),
            lay_prob=_fmt(lay.get("prob")), lay_odds=_fmt(lay.get("odds"), ".2f"),
            lay_size=_fmt(lay.get("size_gbp"), ".0f"),
            mid=_fmt(contract.get("mid")), last=_fmt(contract.get("last")),
            matched=_fmt(contract.get("matched_gbp"), ",.0f"),
            commission=COMMISSION.get(str(contract.get("venue")), 0.0),
        )
        brief += "\n" + run_bot.markets.market_facts_section(question_title(contract))
    return brief


# --------------------------------------------------------------------------- paper bet


def side_economics(p_us: float, contract: dict[str, Any], side: str) -> dict[str, float] | None:
    """Price paid, net odds, win probability and capacity for one side at the touch.

    YES buys at the best offer (``back.prob`` per GBP 1 payout). NO is a lay at the best bid:
    laying odds o against a backer's stake S carries liability S*(o-1), so our capital at
    risk per GBP 1 of NO-payout is (1 - lay.prob) and the resting size S supports a liability
    of S*(1-lay.prob)/lay.prob. Both sides are expressed as "stake GBPX to win b*X" so Kelly
    and the EV gate are one formula. ``b`` is NET of the venue's commission on winnings."""
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


def paper_bet(p_us: float, contract: dict[str, Any], bankroll: float, *,
              already_positioned: bool = False) -> dict[str, Any] | None:
    """The would-be bet at the touch, or None with no side worth taking.

    Direction follows the divergence from the MID (as Manifold: YES when we are above the
    market), but the bet is priced at the executable side, which is worse than the mid by
    half the spread — that is the first real-money haircut the paper test must pay."""
    if already_positioned:
        return None
    mid = contract.get("mid")
    if mid is None or abs(p_us - mid) < DIVERGENCE_THRESHOLD:
        return None
    side = "YES" if p_us > mid else "NO"
    econ = side_economics(p_us, contract, side)
    if econ is None or econ["ev_net"] < MIN_EXPECTED_RETURN_NET:
        return None
    raw = KELLY_FRACTION * econ["kelly"] * bankroll
    cap = STAKE_CAP_FRAC * bankroll
    stake = min(raw, cap, econ["capacity_gbp"])
    capped_by = "kelly" if stake == raw else ("cap" if stake == cap else "depth")
    if stake < STAKE_FLOOR_GBP:
        return None
    return {
        "outcome": side, "stake_gbp": round(stake, 2), "price": round(econ["price"], 6),
        "odds": round(1.0 / econ["price"], 4), "net_odds_b": round(econ["b"], 4),
        "expected_return_net": round(econ["ev_net"], 4), "kelly_raw_gbp": round(raw, 2),
        "capacity_gbp": round(econ["capacity_gbp"], 2), "capped_by": capped_by,
        "commission": econ["commission"], "mid_at_bet": round(mid, 6),
        "bankroll_gbp": bankroll, "dry_run": True,
    }


# --------------------------------------------------------------------------- journal


def build_record(contract: dict[str, Any], forecast: dict[str, Any], pair_id: str, *,
                 bet: dict[str, Any] | None = None) -> ForecastRecord:
    """One journal record per mode. ``dry_run`` is ALWAYS True here: this bot never trades.
    The book at forecast time rides in ``source.book`` and the mid in ``crowd``."""
    source: dict[str, Any] = {
        "platform": contract["venue"],
        "question_id": contract["contract_id"],
        "url": contract.get("url", ""),
        "pair_id": pair_id,
        "mode": forecast["mode"],
        "contract": {
            "event": contract.get("event", ""), "market": contract.get("market", ""),
            "name": contract["name"], "n_runners": len(contract.get("runners") or []),
            "market_id": contract.get("market_id"), "selection_id": contract.get("selection_id"),
        },
        "book": {k: contract.get(k) for k in ("back", "lay", "mid", "last", "matched_gbp")},
    }
    if forecast.get("market_read"):
        source["market_read"] = forecast["market_read"]
    if bet is not None:
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


def open_contract_ids(rows: list[dict[str, Any]], settled: set[str] | None = None) -> set[str]:
    """Contract ids the journal still tracks (any record not yet settled per the snapshots)."""
    settled = settled or set()
    return {str(r["source"]["question_id"]) for r in rows
            if isinstance(r.get("source"), dict) and r["source"].get("question_id")
            and str(r["source"]["question_id"]) not in settled}


def positioned_contract_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r["source"]["question_id"]) for r in rows
            if isinstance(r.get("source"), dict) and r["source"].get("paper_bet")}


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


def append_snapshots(prices_path: str | Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    p = Path(prices_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- run


def run(args: argparse.Namespace) -> int:
    if getattr(args, "provider", "subscription") != "subscription":
        print("exchange paper bot is subscription-only; refusing a non-subscription provider")
        return 2
    budget = float(getattr(args, "budget", run_manifold.MAX_CREDIT_BUDGET_USD) or 0.0)
    if (not math.isfinite(budget) or budget <= 0
            or budget > run_manifold.MAX_CREDIT_BUDGET_USD + run_manifold.BUDGET_EPSILON_USD):
        print(f"credit budget must be > $0 and <= ${run_manifold.MAX_CREDIT_BUDGET_USD:.2f}")
        return 2
    auth_error = run_manifold.subscription_auth_error(
        bool(getattr(args, "require_subscription_auth", False))
    )
    if auth_error:
        print(f"subscription-auth preflight failed: {auth_error}")
        return 2
    deadline_minutes = float(getattr(args, "deadline_minutes", 0.0) or 0.0)
    deadline = time.monotonic() + deadline_minutes * 60 if deadline_minutes > 0 else None
    budget_state: dict[str, Any] = {
        "usd": 0.0, "uncertain": False, "subscription_deferred": False, "budget_deferred": False,
    }
    print(f"Claude subscription credit cap: ${budget:.2f} this run (PAPER — nothing is traded)")

    config = run_bot.load_config()
    journal_path = args.journal or str(DEFAULT_JOURNAL)
    prices_path = args.prices or str(DEFAULT_PRICES)
    Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
    journal = Journal(journal_path)
    rows_before = journal_rows(journal_path)
    now = datetime.now(UTC)

    venues = tuple(args.venue or ("smarkets", "betfair"))
    contracts = exchanges.load_contracts(venues=venues, fixture=getattr(args, "fixture", None))
    by_id = {c["contract_id"]: c for c in contracts}
    print(f"pulled {len(contracts)} quoted contract(s) from {', '.join(venues)}")

    # ---- snapshots FIRST: the closing line and settlements of everything we already track.
    # Cheap (no model calls) and the whole point of the run when no new contract qualifies.
    tracked = open_contract_ids(rows_before, settled=settled_ids(prices_path))
    snaps = snapshot_rows({cid: by_id[cid] for cid in tracked if cid in by_id})
    append_snapshots(prices_path, snaps)
    missing = sorted(cid for cid in tracked if cid not in by_id)
    print(f"snapshot: {len(snaps)} tracked contract(s) quoted"
          + (f", {len(missing)} no longer listed" if missing else ""))

    # ---- selection: fresh contracts only; a contract with an open paper bet is not re-bet.
    fresh = recently_forecast_ids(rows_before, now)
    positioned = positioned_contract_ids(rows_before)
    selected = select_contracts(contracts, args.limit, exclude=fresh, now=now)
    print(f"selected {len(selected)} contract(s)")
    if not selected:
        print(f"credit usage accounted: $0.00 / ${budget:.2f}")
        return 0

    bets = 0
    for contract in selected:
        if float(budget_state["usd"]) >= budget - run_manifold.BUDGET_EPSILON_USD:
            print("credit cap reached; leaving remaining contracts for the next run")
            break
        if deadline is not None and time.monotonic() >= deadline:
            print("wall-clock deadline reached; leaving remaining contracts for the next run")
            break
        cid = contract["contract_id"]
        print(f"- {question_title(contract)[:90]!r} (mid={contract.get('mid')}, "
              f"matched=GBP {contract.get('matched_gbp')})")
        blind_fc = run_manifold.forecast_market(
            contract, "blind", args.tier, args, config, budget_state, deadline,
            brief_builder=build_exchange_brief, extra_blind_disallowed=BLIND_EXTRA_DISALLOWED,
        )
        if blind_fc is None:
            print("  skip: blind forecast failed; sighted call not started")
            continue
        sighted_fc = run_manifold.forecast_market(
            contract, "sighted", args.tier, args, config, budget_state, deadline,
            brief_builder=build_exchange_brief, extra_blind_disallowed=BLIND_EXTRA_DISALLOWED,
        )
        if sighted_fc is None:
            print("  skip: sighted forecast failed")
            continue
        for fc in (blind_fc, sighted_fc):
            fc["tier"] = args.tier
            fc["provider"] = args.provider
        pair_id = f"{_utc_now()[:10]}-{uuid4().hex[:8]}"
        journal.append(build_record(contract, blind_fc, pair_id))

        p_sighted = sighted_fc["probability"]
        bet = paper_bet(
            p_sighted, contract, float(args.bankroll),
            already_positioned=(cid in positioned or bets >= args.max_bets),
        )
        if bet is not None:
            bets += 1
            positioned.add(cid)
            print(f"  PAPER-BET {bet['outcome']} GBP {bet['stake_gbp']:.2f} at {bet['price']:.3f} "
                  f"(p_us={p_sighted:.2f} vs mid {contract['mid']:.3f}, EV net "
                  f"{bet['expected_return_net']:+.2%}, capped by {bet['capped_by']}, "
                  f"read={sighted_fc.get('market_read')})")
        journal.append(build_record(contract, sighted_fc, pair_id, bet=bet))
        print(f"  journaled pair {pair_id} (blind {blind_fc['probability']:.2f} / "
              f"sighted {p_sighted:.2f})")
        # The entry snapshot: the book the bet was priced against, in the price file too, so
        # the scorer never has to reach back into the journal for time zero.
        append_snapshots(prices_path, snapshot_rows({cid: contract}))

    print(f"done (paper): {len(selected)} contract(s), {bets} paper bet(s)")
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
    parser.add_argument("--bankroll", type=float, default=PAPER_BANKROLL_GBP,
                        help=f"notional GBP bankroll for sizing (default {PAPER_BANKROLL_GBP:.0f})")
    parser.add_argument("--max-bets", dest="max_bets", type=int, default=MAX_PAPER_BETS_PER_RUN)
    parser.add_argument("--provider", default="subscription", choices=("subscription",))
    parser.add_argument("--budget", type=float, default=run_manifold.MAX_CREDIT_BUDGET_USD,
                        help="Claude subscription credit cap for this invocation")
    parser.add_argument("--deadline-minutes", type=float, default=0.0)
    parser.add_argument("--require-subscription-auth", action="store_true")
    parser.add_argument("--timeout", type=int, default=1200, help="seconds per agent call")
    parser.add_argument("--agent-cmd", dest="agent_cmd", default=DEFAULT_AGENT_CMD)
    parser.add_argument("--journal", default=None, help=f"default {DEFAULT_JOURNAL}")
    parser.add_argument("--prices", default=None, help=f"default {DEFAULT_PRICES}")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
