"""Tests for the exchange PAPER bot (bot/exchanges.py, bot/run_exchange.py,
bot/score_exchange.py). Nothing here touches the network; every venue call and every agent
call is stubbed. Covered: venue normalisation for both raw API shapes (back >= lay, units,
settlement), selection filters and caps, the blind brief hiding the book while the blind
agent-cmd blocks the venues, paper-bet economics on both sides (executable price, commission,
depth cap, Kelly, gates), the run loop journaling pairs + snapshots and deduping, the scorer's
CLV / P&L / three-way Brier / preregistered verdict, the workflow contract, and the structural
guarantee that the venue module has no order-placing code path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

import exchanges  # noqa: E402
import run_bot  # noqa: E402
import run_exchange  # noqa: E402
import score_exchange  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "exchange_contracts.json"
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
SOURCES = ["https://example.com/a", "https://example.com/b", "https://example.com/c"]


def fixture_contracts() -> list[dict[str, Any]]:
    return exchanges.load_contracts(fixture=str(FIXTURE))


def fenced(probability: float, market_read: str | None = "herding") -> str:
    payload: dict[str, Any] = {
        "probability": probability, "reasoning": "stub", "reference_class": "cases",
        "base_rate": 0.3, "raw_draws": [probability], "sources": SOURCES,
        "what_would_change_my_mind": ["new data"],
    }
    if market_read is not None:
        payload["market_read"] = market_read
    return "```json\n" + json.dumps(payload) + "\n```"


class ScriptedAgent:
    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        self.calls.append({"cmd": cmd, "prompt": prompt})
        return fenced(self.probability), 0.01, "claude-sonnet-5"


def make_args(tmp_path: Path, **over: Any) -> argparse.Namespace:
    base = dict(
        limit=10, tier="medium", venue=None, fixture=str(FIXTURE),
        bankroll=10_000.0, max_bets=10, provider="subscription", timeout=60,
        agent_cmd=run_exchange.DEFAULT_AGENT_CMD, budget=6.0, deadline_minutes=0.0,
        require_subscription_auth=False,
        journal=str(tmp_path / "exchange.jsonl"), prices=str(tmp_path / "exchange-prices.jsonl"),
        arbs=str(tmp_path / "exchange-arbs.jsonl"), snapshot_only=False, proxy=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------- venues

SMK_EVENT = {"id": "500", "name": "Next UK Prime Minister", "state": "upcoming",
             "start_datetime": "2027-06-30T12:00:00Z", "full_slug": "/politics/uk/next-pm"}
SMK_MARKET = {"id": "900", "event_id": "500", "name": "Next Prime Minister", "state": "open",
              "description": "<p>Settled on the person <b>appointed</b>.</p>"}
SMK_CONTRACT = {"id": "1", "market_id": "900", "name": "Wes Streeting", "state": "open"}
SMK_QUOTE = {"bids": [{"price": 2800, "quantity": 3_500_000}, {"price": 2700, "quantity": 100}],
             "offers": [{"price": 3000, "quantity": 4_200_000}, {"price": 3100, "quantity": 50}],
             "last_executed_price": 2900}


def test_smarkets_normalise_maps_offers_to_back_and_bids_to_lay() -> None:
    c = exchanges.smarkets_normalise(SMK_CONTRACT, SMK_MARKET, SMK_EVENT, SMK_QUOTE,
                                     ["Wes Streeting", "Angela Rayner"])
    assert c["contract_id"] == "smarkets:900:1" and c["venue"] == "smarkets"
    # Lowest offer is the cheapest YES; highest bid is the best lay. Basis points -> prob.
    assert c["back"]["prob"] == 0.30 and c["lay"]["prob"] == 0.28
    assert c["back"]["prob"] >= c["lay"]["prob"]
    assert c["back"]["size_gbp"] == 420.0 and c["lay"]["size_gbp"] == 350.0
    assert c["mid"] == 0.29 and c["last"] == 0.29
    assert c["rules"] == "Settled on the person appointed ."
    assert c["close_time"] == "2027-06-30T12:00:00Z"
    assert c["url"].endswith("/politics/uk/next-pm")
    assert c["status"] == "open" and c["outcome"] is None and c["runners"][1] == "Angela Rayner"


def test_smarkets_normalise_settlement_and_one_sided_book() -> None:
    settled = exchanges.smarkets_normalise(
        {**SMK_CONTRACT, "state": "settled", "outcome": "winner"}, SMK_MARKET, SMK_EVENT, {}, [])
    assert settled["status"] == "closed" and settled["outcome"] is True
    assert settled["back"] is None and settled["lay"] is None and settled["mid"] is None
    loser = exchanges.smarkets_normalise(
        {**SMK_CONTRACT, "state": "settled", "outcome": "loser"}, SMK_MARKET, SMK_EVENT, {}, [])
    assert loser["outcome"] is False


def test_smarkets_contracts_walks_events_markets_contracts_quotes(monkeypatch) -> None:
    calls: list[str] = []

    def get(url: str) -> Any:
        calls.append(url)
        if "/events/?" in url:
            assert "type_domain=politics" in url and "type_domain=current_affairs" in url
            assert "state=upcoming" in url
            return {"events": [SMK_EVENT], "pagination": {"next_page": None}}
        if url.endswith("/events/500/markets/"):
            return {"markets": [SMK_MARKET]}
        if url.endswith("/markets/900/contracts/"):
            return {"contracts": [SMK_CONTRACT, {**SMK_CONTRACT, "id": "2", "name": "Rayner"}]}
        if url.endswith("/markets/900/quotes/"):
            return {"1": SMK_QUOTE, "2": {"bids": [], "offers": []}}
        raise AssertionError(url)

    monkeypatch.setattr(exchanges.time, "sleep", lambda s: None)
    got = exchanges.smarkets_contracts(get)
    assert [c["name"] for c in got] == ["Wes Streeting", "Rayner"]
    assert got[0]["runners"] == ["Wes Streeting", "Rayner"]
    assert got[1]["mid"] is None  # unquoted runner survives normalisation, fails selection
    assert len(calls) == 4


BF_MARKET = {"marketId": "1.2345", "marketName": "Trump to leave office before end of 2026?",
             "marketStartTime": "2026-12-31T23:59:00.000Z", "totalMatched": 950000.0,
             "event": {"id": "77", "name": "US Politics"},
             "description": {"rules": "<b>Settled</b> per official announcement."},
             "runners": [{"selectionId": 11, "runnerName": "No"},
                         {"selectionId": 12, "runnerName": "Yes"}]}
BF_BOOK = {"marketId": "1.2345", "status": "OPEN", "totalMatched": 951000.0, "runners": [
    {"selectionId": 11, "status": "ACTIVE", "lastPriceTraded": 1.11,
     "ex": {"availableToBack": [{"price": 1.11, "size": 2500.0}, {"price": 1.1, "size": 9000.0}],
            "availableToLay": [{"price": 1.12, "size": 1800.0}, {"price": 1.13, "size": 500.0}]}},
    {"selectionId": 12, "status": "ACTIVE", "lastPriceTraded": 9.8,
     "ex": {"availableToBack": [{"price": 9.0, "size": 300.0}, {"price": 8.8, "size": 40.0}],
            "availableToLay": [{"price": 10.0, "size": 210.0}]}},
]}


def test_betfair_normalise_best_back_is_highest_odds_and_lay_lowest() -> None:
    rows = exchanges.betfair_normalise(BF_MARKET, BF_BOOK)
    by = {r["name"]: r for r in rows}
    no, yes = by["No"], by["Yes"]
    assert no["contract_id"] == "betfair:1.2345:11"
    assert no["back"]["odds"] == 1.11 and no["back"]["size_gbp"] == 2500.0
    assert no["lay"]["odds"] == 1.12 and no["lay"]["size_gbp"] == 1800.0
    assert no["back"]["prob"] > no["lay"]["prob"]
    assert yes["back"]["odds"] == 9.0 and yes["lay"]["odds"] == 10.0
    assert yes["mid"] == pytest.approx((1 / 9.0 + 1 / 10.0) / 2, abs=1e-6)
    assert no["rules"] == "Settled per official announcement."
    assert no["close_time"] == "2026-12-31T23:59:00Z" and no["matched_gbp"] == 951000.0
    assert no["runners"] == ["No", "Yes"] and no["url"].endswith("/market/1.2345")


def test_betfair_normalise_settlement_from_runner_status() -> None:
    book = {**BF_BOOK, "status": "CLOSED", "runners": [
        {"selectionId": 11, "status": "WINNER"}, {"selectionId": 12, "status": "LOSER"}]}
    by = {r["name"]: r for r in exchanges.betfair_normalise(BF_MARKET, book)}
    assert by["No"]["outcome"] is True and by["Yes"]["outcome"] is False
    assert by["No"]["status"] == "closed" and by["No"]["back"] is None


def test_betfair_contracts_posts_catalogue_then_books_in_chunks() -> None:
    posted: list[tuple[str, dict[str, Any]]] = []

    def post(url: str, body: dict[str, Any]) -> Any:
        posted.append((url, body))
        if url.endswith("/listMarketCatalogue/"):
            assert body["filter"]["eventTypeIds"] == [exchanges.BETFAIR_POLITICS_EVENT_TYPE]
            return [BF_MARKET]
        if url.endswith("/listMarketBook/"):
            assert body["marketIds"] == ["1.2345"]
            assert body["priceProjection"]["priceData"] == ["EX_BEST_OFFERS"]
            return [BF_BOOK]
        raise AssertionError(url)

    rows = exchanges.betfair_contracts("app", "token", post)
    assert len(rows) == 2 and len(posted) == 2


def test_betfair_login_requires_success_status() -> None:
    ok = exchanges.betfair_login("app", "u", "p",
                                 post=lambda *a: {"token": "T", "status": "SUCCESS"})
    assert ok == "T"
    with pytest.raises(RuntimeError):
        exchanges.betfair_login(
            "app", "u", "p",
            post=lambda *a: {"token": "", "status": "INVALID_USERNAME_OR_PASSWORD"})


def test_load_contracts_is_fail_open_per_venue(monkeypatch, capsys) -> None:
    def boom(get=None, **kw):
        raise RuntimeError("smarkets down")
    monkeypatch.setattr(exchanges, "smarkets_contracts", boom)
    monkeypatch.delenv("BETFAIR_APP_KEY", raising=False)
    assert exchanges.load_contracts() == []
    out = capsys.readouterr().out
    assert "smarkets: lookup failed" in out and "betfair: not configured" in out


def test_quote_by_id_reaches_settled_markets_on_both_venues() -> None:
    def get(url: str) -> Any:
        if url.endswith("/markets/900/"):
            return {"markets": [{**SMK_MARKET, "state": "settled"}]}
        if url.endswith("/markets/900/contracts/"):
            return {"contracts": [{**SMK_CONTRACT, "state": "settled", "outcome": "winner"}]}
        if url.endswith("/markets/900/quotes/"):
            return {}
        raise AssertionError(url)
    import time as _t
    got = exchanges.smarkets_quote_by_market_ids(["900"], get, pace=0.0)
    assert got["smarkets:900:1"]["outcome"] is True and got["smarkets:900:1"]["status"] == "closed"

    def post(url: str, body: dict[str, Any]) -> Any:
        assert url.endswith("/listMarketBook/") and body["marketIds"] == ["1.2345"]
        return [{**BF_BOOK, "status": "CLOSED", "runners": [
            {"selectionId": 11, "status": "WINNER"}, {"selectionId": 12, "status": "LOSER"}]}]
    books = exchanges.betfair_books_by_market_ids(["1.2345"], "app", "tok", post)
    assert books["betfair:1.2345:11"]["outcome"] is True
    assert books["betfair:1.2345:12"]["outcome"] is False
    # The facade splits ids by venue and only asks each venue for its own markets.
    got = exchanges.quote_contracts(["smarkets:900:1", "betfair:1.2345:12", "betfair:1.2345:11"],
                                    smarkets_get=get, betfair_post=post,
                                    betfair_session=("app", "tok"))
    assert set(got) == {"smarkets:900:1", "betfair:1.2345:12", "betfair:1.2345:11"}
    del _t


def test_odds_ladder_snapping_and_ticks() -> None:
    assert exchanges.odds_tick(1.5) == 0.01 and exchanges.odds_tick(2.5) == 0.02
    assert exchanges.odds_tick(9.09) == 0.2 and exchanges.odds_tick(15.0) == 0.5
    assert exchanges.snap_odds(2.015, "down") == 2.0 and exchanges.snap_odds(2.015, "up") == 2.02
    assert exchanges.ticks_between(1.11, 1.12) == 1
    assert exchanges.ticks_between(3.3333, 3.5714) == 4
    assert exchanges.snap_nearest(1.1111) == 1.11 and exchanges.snap_nearest(3.3333) == 3.35
    assert exchanges.ticks_between(3.0, 3.0) == 0 and exchanges.ticks_between(3.0, 2.0) == 0


def test_venue_module_has_no_order_code_path() -> None:
    """The read-only claim is structural: no name in the module places, cancels or sizes."""
    names = [n.lower() for n in dir(exchanges)]
    forbidden = ("place", "order", "cancel", "trade", "stake", "wager")
    assert not [n for n in names if any(f in n for f in forbidden)]
    assert not [n for n in names if re.search(r"(^|_)bets?(_|$)", n)]
    source = (ROOT / "bot" / "exchanges.py").read_text(encoding="utf-8")
    assert "placeOrders" not in source and "/orders/" not in source


# --------------------------------------------------------------------------- selection


def test_selection_filters_and_ranks_by_matched_volume() -> None:
    picked = run_exchange.select_contracts(fixture_contracts(), 10, now=NOW)
    ids = [c["contract_id"] for c in picked]
    # Thin special (GBP 4 at the touch, 15-point spread) is excluded; deepest book first.
    assert "smarkets:901:7" not in ids
    assert ids[0].startswith("betfair:1.2345") and "smarkets:900:1" in ids
    # The two-runner Betfair market contributes ONE contract: its runners are one bet.
    assert len(ids) == 2


@pytest.mark.parametrize("over,reason", [
    ({"status": "suspended"}, "not open"),
    ({"outcome": True}, "settled"),
    ({"lay": None}, "one-sided"),
    ({"back": {"prob": 0.30, "odds": 3.33, "size_gbp": 3.0}}, "thin touch"),
    ({"back": {"prob": 0.40, "odds": 2.5, "size_gbp": 500.0}}, "spread too wide"),
    ({"close_time": (NOW + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}, "closes too soon"),
    ({"close_time": (NOW + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")}, "closes too late"),
    ({"close_time": None}, "no close"),
])
def test_eligible_rejects(over: dict[str, Any], reason: str) -> None:
    base = dict(fixture_contracts()[0])
    base.update(over)
    if "back" in over or "lay" in over:
        base = exchanges._finish(base)
    assert run_exchange.eligible(fixture_contracts()[0], NOW), "baseline must be eligible"
    assert not run_exchange.eligible(base, NOW), reason


def test_eligible_price_band() -> None:
    c = dict(fixture_contracts()[0])
    c.update({"back": {"prob": 0.995, "odds": 1.005, "size_gbp": 900.0},
              "lay": {"prob": 0.99, "odds": 1.01, "size_gbp": 900.0}})
    assert not run_exchange.eligible(exchanges._finish(c), NOW)


def test_selection_caps_runners_per_market_and_honours_exclude() -> None:
    base = dict(fixture_contracts()[1], runners=["A", "B", "C"])
    trio = [dict(base, contract_id=f"betfair:1.2345:{i}", selection_id=str(i)) for i in (1, 2, 3)]
    picked = run_exchange.select_contracts(trio, 10, now=NOW)
    assert len(picked) == run_exchange.MAX_CONTRACTS_PER_MARKET
    two_runner = [dict(fixture_contracts()[1]), dict(fixture_contracts()[2])]
    assert len(run_exchange.select_contracts(two_runner, 10, now=NOW)) == 1
    picked = run_exchange.select_contracts(trio, 10, exclude={"betfair:1.2345:1"}, now=NOW)
    assert [c["contract_id"] for c in picked] == ["betfair:1.2345:2", "betfair:1.2345:3"]


# --------------------------------------------------------------------------- brief


def test_blind_brief_hides_the_book_and_sighted_shows_it() -> None:
    c = fixture_contracts()[0]
    blind = run_exchange.build_exchange_brief(c, sighted=False)
    assert "Wes Streeting" in blind and "Resolution criteria" in blind
    assert "Angela Rayner" in blind  # the other runners are context, not prices
    for leak in ("0.30", "0.28", "420", "350", "185", "Market signals", "market_read",
                 "Commission"):
        assert leak not in blind, leak
    sighted = run_exchange.build_exchange_brief(c, sighted=True)
    assert "Market signals (Smarkets exchange, real money)" in sighted
    assert "BACK (buy YES): 0.300" in sighted and "LAY (sell YES): 0.280" in sighted
    assert "GBP 420" in sighted and "GBP 350" in sighted
    assert "Commission on net winnings: 2%" in sighted
    assert '"market_read"' in sighted and "REQUIRED" in sighted


def test_blind_agent_cmd_blocks_the_venues(monkeypatch) -> None:
    agent = ScriptedAgent(0.5)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    args = make_args(Path("/tmp"))
    config = run_bot.load_config()
    c = fixture_contracts()[0]
    import run_manifold
    fc = run_manifold.forecast_market(
        c, "blind", "medium", args, config, brief_builder=run_exchange.build_exchange_brief,
        extra_blind_disallowed=run_exchange.BLIND_EXTRA_DISALLOWED)
    assert fc is not None and fc["blind"] is True
    cmd = agent.calls[0]["cmd"]
    for domain in ("betfair.com", "smarkets.com", "oddschecker.com", "manifold.markets"):
        assert f"WebFetch(domain:{domain})" in cmd
    assert cmd.count("--disallowed-tools") == 1
    fc = run_manifold.forecast_market(
        c, "sighted", "medium", args, config, brief_builder=run_exchange.build_exchange_brief,
        extra_blind_disallowed=run_exchange.BLIND_EXTRA_DISALLOWED)
    assert fc is not None and "smarkets.com" not in agent.calls[1]["cmd"]
    assert "Market signals" in agent.calls[1]["prompt"]


# --------------------------------------------------------------------------- paper bet


def test_yes_side_prices_at_the_back_offer_net_of_commission() -> None:
    c = fixture_contracts()[0]  # smarkets back 0.30 / lay 0.28, 2% commission
    econ = run_exchange.side_economics(0.40, c, "YES")
    assert econ["price"] == 0.30 and econ["capacity_gbp"] == 420.0
    assert econ["b"] == pytest.approx((1 / 0.30 - 1) * 0.98)
    assert econ["ev_net"] == pytest.approx(0.40 * econ["b"] - 0.60)
    assert econ["kelly"] == pytest.approx(0.40 - 0.60 / econ["b"])


def test_no_side_is_a_lay_at_the_bid_with_liability_capacity() -> None:
    c = fixture_contracts()[0]  # lay 0.28 with GBP 350 of backer stake resting
    econ = run_exchange.side_economics(0.20, c, "NO")
    assert econ["price"] == pytest.approx(0.72) and econ["win_p"] == pytest.approx(0.80)
    # Laying GBP 350 at odds 1/0.28 carries liability 350 * (1-0.28)/0.28 = GBP 900.
    assert econ["capacity_gbp"] == pytest.approx(350.0 * 0.72 / 0.28)


def test_paper_bet_gates_and_sizing() -> None:
    c = fixture_contracts()[0]
    bank = 10_000.0
    assert run_exchange.paper_bet(0.30, c, bank) is None  # 1 point over the mid: negative EV
    assert run_exchange.paper_bet(0.40, c, bank, already_positioned=True) is None
    bet = run_exchange.paper_bet(0.45, c, bank)
    assert bet is not None and bet["outcome"] == "YES" and bet["price"] == 0.30
    assert bet["dry_run"] is True and bet["commission"] == 0.02
    # Quarter-Kelly on GBP 10k for p=0.45 at 0.30 (net odds 2.29) is GBP 524: above the 5% cap
    # (GBP 500), which is itself above the GBP 420 resting at the touch, so depth binds.
    assert bet["kelly_raw_gbp"] == pytest.approx(523.7, abs=1.0)
    assert bet["stake_gbp"] == 420.0 and bet["capped_by"] == "depth"
    # A smaller divergence is Kelly-bound and well under both caps.
    small = run_exchange.paper_bet(0.345, c, bank)
    assert small is not None and small["capped_by"] == "kelly" and small["stake_gbp"] < 420.0
    # NO side: p=0.20 vs mid 0.29 -> lay; capacity GBP 900 > cap GBP 500 -> cap binds.
    no = run_exchange.paper_bet(0.20, c, bank)
    assert no is not None and no["outcome"] == "NO" and no["capped_by"] == "cap"
    assert no["stake_gbp"] == 500.0


def test_paper_bet_gates_are_in_pounds_not_ratios() -> None:
    fav = fixture_contracts()[1]  # betfair favourite: back 0.90, lay 0.89, 6% commission
    # 0.93 vs mid 0.895: +2.7% per pound net of 6% commission — a thin ratio, but on a book
    # that absorbs the 5%-of-bankroll cap it is GBP 13 of expected profit, so it is a bet.
    bet = run_exchange.paper_bet(0.93, fav, 10_000.0)
    assert bet is not None and bet["outcome"] == "YES" and bet["capped_by"] == "cap"
    assert bet["ev_gbp"] == pytest.approx(500.0 * bet["expected_return_net"], abs=0.1)
    assert bet["ev_gbp"] >= run_exchange.MIN_EV_GBP_FRAC * 10_000.0
    # Inside commission noise per pound -> no bet whatever the depth.
    assert run_exchange.paper_bet(0.905, fav, 10_000.0) is None
    # A real edge on a book that only absorbs GBP 5: the pound-EV is pennies -> no bet.
    thin = dict(fixture_contracts()[0], back={"prob": 0.50, "odds": 2.0, "size_gbp": 5.0},
                lay={"prob": 0.49, "odds": 2.04, "size_gbp": 5.0})
    thin = exchanges._finish(thin)
    assert run_exchange.side_economics(0.56, thin, "YES")["ev_net"] > 0.05
    assert run_exchange.paper_bet(0.56, thin, 10_000.0) is None
    # A tiny bankroll sizes below Betfair's GBP 2 minimum -> no bet.
    assert run_exchange.paper_bet(0.45, fixture_contracts()[0], 20.0) is None


def test_longshot_lays_are_betfair_only_and_liability_capped() -> None:
    yes = fixture_contracts()[2]  # betfair 'Yes' longshot: mid 0.105
    dead = exchanges._finish(dict(yes, back={"prob": 0.04, "odds": 25.0, "size_gbp": 60.0},
                                  lay={"prob": 0.03, "odds": 33.3, "size_gbp": 12.0}))
    bet = run_exchange.paper_bet(0.01, dead, 10_000.0)
    assert bet is not None and bet["outcome"] == "NO" and bet["longshot"] is True
    # Capacity: GBP 12 of backer stake at 0.03 supports GBP 12 * 0.97 / 0.03 of liability.
    assert bet["capacity_gbp"] == pytest.approx(12 * 0.97 / 0.03, abs=0.01)
    assert bet["capped_by"] == "depth" and bet["ev_gbp"] > 2.0
    # Same book on Smarkets (which may void): refused.
    assert run_exchange.paper_bet(0.01, dict(dead, venue="smarkets"), 10_000.0) is None
    # Aggregate longshot liability cap binds and can close the door entirely.
    capped = run_exchange.paper_bet(0.01, dead, 10_000.0, longshot_liability_open=850.0)
    assert capped is not None and capped["capped_by"] == "longshot_cap"
    assert capped["stake_gbp"] == pytest.approx(150.0)
    # With only GBP 100 of room the pound-EV (GBP 1.9) falls under the GBP 2 floor.
    assert run_exchange.paper_bet(0.01, dead, 10_000.0, longshot_liability_open=900.0) is None
    assert run_exchange.paper_bet(0.01, dead, 10_000.0, longshot_liability_open=999.0) is None


def test_maker_quote_improves_one_tick_when_the_spread_allows() -> None:
    c = fixture_contracts()[0]  # back odds 3.3333, lay 3.5714: five 0.05 ticks apart
    yes = run_exchange.maker_quote(c, "YES")
    assert yes["improved"] and yes["spread_ticks"] == 4  # 3.35 -> 3.55 on the 0.05 ladder
    assert yes["odds"] > c["back"]["odds"] and yes["prob"] < c["back"]["prob"]
    assert yes["price"] == yes["prob"]
    no = run_exchange.maker_quote(c, "NO")
    assert no["improved"] and no["odds"] < c["lay"]["odds"] and no["prob"] > c["lay"]["prob"]
    assert no["price"] == pytest.approx(1 - no["prob"])
    tight = fixture_contracts()[1]  # 1.1111 / 1.1236 -> rungs 1.11 / 1.12: join the touch
    joined = run_exchange.maker_quote(tight, "YES")
    assert not joined["improved"] and joined["odds"] == 1.11


def test_route_bet_takes_the_larger_pound_ev_and_journals_alternatives() -> None:
    a = fixture_contracts()[0]  # smarkets, 2%, depth 420
    b = dict(a, venue="betfair", contract_id="betfair:77:1",
             back={"prob": 0.30, "odds": 3.3333, "size_gbp": 3000.0},
             lay={"prob": 0.28, "odds": 3.5714, "size_gbp": 2500.0})
    b = exchanges._finish(b)
    chosen, bet, alts = run_exchange.route_bet(0.45, [a, b], 10_000.0, positioned=set())
    # Same price, deeper book: Betfair absorbs the GBP 500 cap despite 6% commission.
    assert chosen["venue"] == "betfair" and bet["venue"] == "betfair"
    assert bet["route"]["chosen"] == "betfair" and len(bet["route"]["alternatives"]) == 2
    assert {x["venue"] for x in alts} == {"smarkets", "betfair"}
    # Make Betfair's price worse than the commission gap: Smarkets wins.
    b2 = exchanges._finish(dict(b, back={"prob": 0.36, "odds": 2.78, "size_gbp": 3000.0}))
    chosen, bet, _ = run_exchange.route_bet(0.45, [a, b2], 10_000.0, positioned=set())
    assert chosen["venue"] == "smarkets" and "route" in bet
    # A single candidate journals no route.
    _, single, _ = run_exchange.route_bet(0.45, [a], 10_000.0, positioned=set())
    assert "route" not in single
    # Already positioned everywhere -> nothing.
    assert run_exchange.route_bet(0.45, [a, b], 10_000.0,
                                  positioned={a["contract_id"], b["contract_id"]})[1] is None


def test_sighted_brief_carries_the_twin_book_and_rules() -> None:
    a = fixture_contracts()[0]
    twin = dict(a, venue="betfair", contract_id="betfair:77:1", rules="Withdrawn = loser.")
    sighted = run_exchange.build_exchange_brief(a, sighted=True, twin=twin)
    assert "The same contract on Betfair" in sighted and "Withdrawn = loser." in sighted
    blind = run_exchange.build_exchange_brief(a, sighted=False, twin=twin)
    assert "Betfair" not in blind and "Withdrawn" not in blind


def test_lock_rows_record_both_rule_texts() -> None:
    a = fixture_contracts()[0]
    b = exchanges._finish(dict(a, venue="betfair", contract_id="betfair:77:1",
                               rules="Betfair rules", close_time="2027-02-01T12:00:00Z",
                               lay={"prob": 0.33, "odds": 3.03, "size_gbp": 200.0}))
    twins = exchanges.match_contracts([a, b])
    rows = run_exchange.lock_rows([a, b], twins, at="2026-09-06T12:00:00Z")
    assert len(rows) == 1 and rows[0]["back_venue"] == "smarkets"
    assert rows[0]["rules_lay"] == "Betfair rules" and rows[0]["pair"] == sorted(
        [a["contract_id"], b["contract_id"]])
    # No twins, or no lock -> empty ledger.
    assert run_exchange.lock_rows([a], {}, at="x") == []


def test_adverse_positions_need_both_touches_to_move() -> None:
    a = fixture_contracts()[0]
    bet = {"outcome": "YES", "stake_gbp": 50.0, "price": 0.30, "contract_id": a["contract_id"],
           "book": {"back": a["back"], "lay": a["lay"], "mid": a["mid"]}}
    row = {"source": {"pair_id": "p1", "question_id": a["contract_id"], "paper_bet": bet,
                      "book": bet["book"]}}
    pulled = exchanges._finish(dict(a, back={"prob": 0.19, "odds": 5.26, "size_gbp": 50.0}))
    assert run_exchange.adverse_positions([row], {a["contract_id"]: pulled}, set()) == []
    repriced = exchanges._finish(dict(a, back={"prob": 0.19, "odds": 5.26, "size_gbp": 50.0},
                                      lay={"prob": 0.17, "odds": 5.88, "size_gbp": 50.0}))
    hits = run_exchange.adverse_positions([row], {a["contract_id"]: repriced}, set())
    assert len(hits) == 1 and hits[0]["pair_id"] == "p1"
    # Settled, or already re-forecast, never triggers again.
    assert run_exchange.adverse_positions([row], {a["contract_id"]: repriced},
                                          {a["contract_id"]}) == []
    done = {"source": {"pair_id": "p2", "reforecast_of": "p1", "question_id": a["contract_id"]}}
    assert run_exchange.adverse_positions([row, done], {a["contract_id"]: repriced}, set()) == []


# --------------------------------------------------------------------------- run loop


def test_run_journals_pair_proxy_paper_bet_and_snapshots(monkeypatch, tmp_path: Path) -> None:
    agent = ScriptedAgent(0.45)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    args = make_args(tmp_path, limit=1)
    assert run_exchange.run(args) == 0
    rows = run_exchange.journal_rows(args.journal)
    assert [r["source"]["mode"] for r in rows] == ["blind", "proxy", "sighted"]
    by_mode = {r["source"]["mode"]: r for r in rows}
    assert by_mode["blind"]["blind"] is True and by_mode["sighted"]["blind"] is False
    assert by_mode["proxy"]["blind"] is True and by_mode["proxy"]["effort"] == "proxy"
    assert all(r["dry_run"] is True for r in rows)
    assert len({r["source"]["pair_id"] for r in rows}) == 1
    # Call order is blind, sighted, proxy. The proxy denied web tools AND the venues; the
    # blind call denied the venues only; the sighted call denied neither.
    blind_cmd, sighted_cmd, proxy_cmd = (
        c["cmd"].split("--disallowed-tools", 1)[1] for c in agent.calls)
    venue_block = "WebFetch(domain:smarkets.com)"  # the exact deny-list token
    assert "WebSearch,WebFetch" in proxy_cmd and venue_block in proxy_cmd
    assert venue_block in blind_cmd and "WebSearch,WebFetch" not in blind_cmd
    assert venue_block not in sighted_cmd and "Market signals" in agent.calls[1]["prompt"]
    sighted = by_mode["sighted"]
    assert sighted["source"]["platform"] == "betfair"  # deepest book selected first
    book = sighted["source"]["book"]
    mid = book["mid"]
    assert mid in (0.895, 0.105)  # either runner of the deepest market; tie-break is float
    assert sighted["crowd"]["value"] == mid and sighted["crowd"]["shown_to_agent"] is True
    assert by_mode["blind"]["crowd"]["shown_to_agent"] is False
    bet = sighted["source"]["paper_bet"]
    assert bet["dry_run"] is True and bet["venue"] == "betfair"
    assert bet["contract_id"] == sighted["source"]["question_id"]
    assert bet["maker"]["prob"] is not None and "ev_gbp" in bet
    if mid > 0.45:
        assert bet["outcome"] == "NO" and bet["price"] == pytest.approx(1 - book["lay"]["prob"])
    else:
        assert bet["outcome"] == "YES" and bet["price"] == pytest.approx(book["back"]["prob"])
    assert "paper_bet" not in by_mode["blind"]["source"]
    # Entry snapshot written for the scorer; no locks in the fixture.
    snaps = run_exchange.journal_rows(args.prices)
    assert len(snaps) == 1 and snaps[0]["contract_id"] == sighted["source"]["question_id"]
    assert snaps[0]["mid"] == mid and snaps[0]["outcome"] is None
    assert run_exchange.journal_rows(args.arbs) == []


def test_second_run_dedupes_and_snapshots_tracked_contracts(monkeypatch, tmp_path: Path) -> None:
    agent = ScriptedAgent(0.45)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    args = make_args(tmp_path, limit=1)
    assert run_exchange.run(args) == 0
    calls_after_first = len(agent.calls)
    assert calls_after_first == 3  # blind, proxy, sighted
    assert run_exchange.run(args) == 0
    rows = run_exchange.journal_rows(args.journal)
    # Second run forecast a DIFFERENT contract (the first is deduped for 3 days)...
    assert len(rows) == 6
    assert rows[0]["source"]["question_id"] != rows[3]["source"]["question_id"]
    assert len(agent.calls) == calls_after_first + 3
    # ...and snapshotted the contract it already tracked before selecting.
    snaps = run_exchange.journal_rows(args.prices)
    tracked = [s for s in snaps if s["contract_id"] == rows[0]["source"]["question_id"]]
    assert len(tracked) == 2


def test_snapshot_only_tick_makes_no_model_call_and_needs_no_auth(
    monkeypatch, tmp_path: Path,
) -> None:
    agent = ScriptedAgent(0.45)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    args = make_args(tmp_path, limit=1)
    assert run_exchange.run(args) == 0
    # A metered gateway variable would fail the forecast preflight; snapshot mode ignores it.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    assert run_exchange.run(make_args(tmp_path, limit=1, provider="openrouter")) == 2
    assert run_exchange.run(make_args(tmp_path, limit=1, snapshot_only=True)) == 0
    assert len(agent.calls) == 3
    assert len(run_exchange.journal_rows(args.prices)) == 2  # entry + one snapshot tick


def test_run_preflight_rejects_metered_provider_and_oversized_budget(tmp_path: Path) -> None:
    assert run_exchange.run(make_args(tmp_path, provider="openrouter")) == 2
    assert run_exchange.run(make_args(tmp_path, budget=99.0)) == 2


def test_run_with_no_eligible_contract_still_snapshots(monkeypatch, tmp_path: Path) -> None:
    agent = ScriptedAgent(0.45)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    args = make_args(tmp_path, limit=1)
    assert run_exchange.run(args) == 0
    thin_only = tmp_path / "thin.json"
    thin_only.write_text(json.dumps([fixture_contracts()[3]]), encoding="utf-8")
    assert run_exchange.run(make_args(tmp_path, limit=1, fixture=str(thin_only))) == 0
    assert len(agent.calls) == 3  # no new forecasts
    assert len(run_exchange.journal_rows(args.journal)) == 3


def test_run_routes_to_the_twin_and_reforecasts_on_adverse_move(
    monkeypatch, tmp_path: Path,
) -> None:
    a = fixture_contracts()[0]
    twin = exchanges._finish(dict(a, venue="betfair", contract_id="betfair:77:1",
                                  close_time="2027-02-01T12:00:00Z", rules="Withdrawn = loser.",
                                  back={"prob": 0.30, "odds": 3.3333, "size_gbp": 3000.0},
                                  lay={"prob": 0.28, "odds": 3.5714, "size_gbp": 2500.0}))
    fx = tmp_path / "pair.json"
    fx.write_text(json.dumps([a, twin]), encoding="utf-8")
    agent = ScriptedAgent(0.45)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    args = make_args(tmp_path, limit=5, fixture=str(fx))
    assert run_exchange.run(args) == 0
    rows = run_exchange.journal_rows(args.journal)
    assert len(rows) == 3  # the twin was NOT forecast separately
    sighted = rows[-1]
    assert "The same contract on Betfair" in agent.calls[1]["prompt"]  # the sighted call
    bet = sighted["source"]["paper_bet"]
    assert bet["venue"] == "betfair" and bet["contract_id"] == "betfair:77:1"
    assert bet["route"]["chosen"] == "betfair"
    assert "book" in bet  # the routed venue's book rides with the bet
    snaps = run_exchange.journal_rows(args.prices)
    assert {s["contract_id"] for s in snaps} == {a["contract_id"], "betfair:77:1"}
    # Next tick: both touches on the routed contract moved 10+ points against the YES.
    moved = exchanges._finish(dict(twin, back={"prob": 0.19, "odds": 5.26, "size_gbp": 300.0},
                                   lay={"prob": 0.17, "odds": 5.88, "size_gbp": 300.0}))
    fx.write_text(json.dumps([a, moved]), encoding="utf-8")
    assert run_exchange.run(args) == 0
    rows = run_exchange.journal_rows(args.journal)
    ref = [r for r in rows if r["source"].get("reforecast_of")]
    assert len(ref) == 2 and ref[0]["source"]["reforecast_of"] == sighted["source"]["pair_id"]
    assert all("paper_bet" not in r["source"] for r in ref)
    # A third tick does not re-forecast the same position again.
    n_calls = len(agent.calls)
    assert run_exchange.run(args) == 0
    assert len(agent.calls) == n_calls


# --------------------------------------------------------------------------- scorer


def _journal_pair(pid: str, cid: str, venue: str, p_sighted: float, p_blind: float, mid: float,
                  bet: dict[str, Any] | None,
                  at: str = "2026-09-01T00:00:00Z") -> list[dict[str, Any]]:
    src = {"platform": venue, "question_id": cid, "pair_id": pid, "book": {"mid": mid}}
    rows = [{"question": "q", "probability": p_blind, "forecast_at": at,
             "source": {**src, "mode": "blind"}},
            {"question": "q", "probability": p_sighted, "forecast_at": at,
             "source": {**src, "mode": "sighted", **({"paper_bet": bet} if bet else {})}}]
    return rows


def _snap(cid: str, at: str, mid: float | None, outcome: bool | None = None,
          status: str = "open") -> dict[str, Any]:
    return {"at": at, "contract_id": cid, "mid": mid, "status": status, "outcome": outcome}


def test_scorer_clv_pnl_and_three_way_brier() -> None:
    bet_yes = {"outcome": "YES", "stake_gbp": 100.0, "price": 0.30,
               "net_odds_b": (1 / 0.3 - 1) * 0.98}
    bet_no = {"outcome": "NO", "stake_gbp": 50.0, "price": 0.72,
              "net_odds_b": (1 / 0.72 - 1) * 0.98}
    journal = (_journal_pair("p1", "c1", "smarkets", 0.40, 0.35, 0.29, bet_yes)
               + _journal_pair("p2", "c2", "smarkets", 0.20, 0.25, 0.29, bet_no))
    prices = [
        _snap("c1", "2026-09-01T00:00:00Z", 0.29),          # entry (ignored: not after entry)
        _snap("c1", "2026-09-10T00:00:00Z", 0.36),          # closing line moved our way
        _snap("c1", "2026-09-12T00:00:00Z", None, True, "closed"),  # settled YES
        _snap("c2", "2026-09-10T00:00:00Z", 0.33),          # moved against our NO
        _snap("c2", "2026-09-12T00:00:00Z", None, True, "closed"),  # settled YES -> NO loses
    ]
    result = score_exchange.score(journal, prices, now=datetime(2026, 9, 13, tzinfo=UTC))
    rows = {r["pair_id"]: r for r in result["rows"]}
    r1, r2 = rows["p1"], rows["p2"]
    assert r1["bet"]["clv"] == pytest.approx(0.36 / 0.30 - 1)
    assert r1["bet"]["pnl_gbp"] == pytest.approx(100.0 * bet_yes["net_odds_b"]) and r1["bet"]["won"]
    assert r1["brier_sighted"] == pytest.approx(0.36) and r1["brier_blind"] == pytest.approx(0.4225)
    assert r1["brier_mid"] == pytest.approx((0.29 - 1) ** 2)
    assert r1["movement_sighted"] == pytest.approx(0.07)
    assert r2["bet"]["clv"] == pytest.approx((1 - 0.33) / 0.72 - 1) and r2["bet"]["clv"] < 0
    assert r2["bet"]["pnl_gbp"] == -50.0
    pooled = result["pooled"]
    assert pooled["n_bets"] == 2 and pooled["n_settled_bets"] == 2
    assert pooled["pnl_gbp"] == pytest.approx(r1["bet"]["pnl_gbp"] - 50.0)
    assert pooled["roi"] == pytest.approx(pooled["pnl_gbp"] / 150.0)
    assert result["verdict"]["status"] == "HOLD"
    assert "smarkets" in result["by_venue"]
    text = score_exchange.render(result)
    assert "VERDICT: HOLD" in text and "POOLED" in text


def _synthetic(n: int, clv_mean: float, brier_delta: float) -> tuple[list[dict], list[dict]]:
    journal: list[dict[str, Any]] = []
    prices: list[dict[str, Any]] = []
    for i in range(n):
        cid = f"c{i}"
        won = i % 5 != 0  # 80% winners at evens: settled ROI is positive
        p_s = 0.6 if won else 0.4
        mid = 0.5
        bet = {"outcome": "YES", "stake_gbp": 10.0, "price": 0.5, "net_odds_b": 0.98}
        journal += _journal_pair(f"p{i}", cid, "smarkets", p_s + (0.0 if won else 0.0),
                                 0.5, mid, bet)
        close_mid = 0.5 * (1 + clv_mean) + (0.01 if i % 3 == 0 else -0.01)
        prices.append(_snap(cid, "2026-09-10T00:00:00Z", close_mid))
        prices.append(_snap(cid, "2026-09-12T00:00:00Z", None, won, "closed"))
    # brier_delta < 0 requires the sighted forecast to beat the mid; p_s already does (0.6 on
    # winners, 0.4 on losers vs a 0.5 mid gives -0.09).
    assert brier_delta <= 0
    return journal, prices


def test_verdict_go_live_kill_and_hold() -> None:
    go_j, go_p = _synthetic(220, 0.05, -0.09)
    go = score_exchange.score(go_j, go_p, now=datetime(2026, 9, 13, tzinfo=UTC))
    assert go["verdict"]["status"] == "GO-LIVE-CANDIDATE", go["verdict"]
    assert go["pooled"]["clv_points_ci90"][0] > 0 and go["pooled"]["roi"] > 0
    kill_j, kill_p = _synthetic(220, -0.03, -0.09)
    kill = score_exchange.score(kill_j, kill_p, now=datetime(2026, 9, 13, tzinfo=UTC))
    assert kill["verdict"]["status"] == "KILL"
    hold_j, hold_p = _synthetic(50, 0.05, -0.09)
    hold = score_exchange.score(hold_j, hold_p, now=datetime(2026, 9, 13, tzinfo=UTC))
    assert hold["verdict"]["status"] == "HOLD"
    assert hold["verdict"]["checks"]["enough_bets"] is False


def test_bootstrap_ci_is_deterministic_and_brackets_the_mean() -> None:
    values = [0.02, -0.01, 0.03, 0.05, -0.02, 0.04, 0.01, 0.0]
    ci = score_exchange.bootstrap_ci(values, draws=2000)
    assert ci == score_exchange.bootstrap_ci(values, draws=2000)
    assert ci[0] <= sum(values) / len(values) <= ci[1]
    assert score_exchange.bootstrap_ci([0.1]) is None


def test_maker_fill_is_a_lower_bound_on_prints_through_the_price() -> None:
    bet = {"outcome": "YES", "stake_gbp": 100.0, "price": 0.30, "net_odds_b": 2.2867,
           "commission": 0.02, "maker": {"prob": 0.29, "price": 0.29, "improved": True,
                                         "ttl_days": 2.0}}
    entry = datetime(2026, 9, 1, tzinfo=UTC)
    touch_cross = [dict(_snap("c", "2026-09-01T06:00:00Z", 0.28), last=0.29,
                        back={"prob": 0.29, "odds": 3.45, "size_gbp": 10.0},
                        lay={"prob": 0.27, "odds": 3.7, "size_gbp": 10.0})]
    # The touch crossed our price and a print AT our price: still not a fill.
    assert score_exchange.maker_fill(bet, touch_cross, entry)["filled"] is False
    printed = [dict(_snap("c", "2026-09-01T12:00:00Z", 0.28), last=0.285)]
    got = score_exchange.maker_fill(bet, printed, entry)
    assert got["filled"] is True and got["filled_at"] == "2026-09-01T12:00:00Z"
    # After the TTL nothing counts.
    late = [dict(_snap("c", "2026-09-05T12:00:00Z", 0.20), last=0.20)]
    assert score_exchange.maker_fill(bet, late, entry)["filled"] is False
    no_side = {"outcome": "NO", "stake_gbp": 50.0, "price": 0.72, "net_odds_b": 0.38,
               "commission": 0.02, "maker": {"prob": 0.29, "price": 0.71, "ttl_days": 2.0}}
    assert score_exchange.maker_fill(no_side, [dict(_snap("c", "2026-09-01T12:00:00Z", 0.3),
                                                    last=0.31)], entry)["filled"] is True


def test_exit_past_fair_value_only_when_the_exit_side_reaches_p_us() -> None:
    bet = {"outcome": "YES", "stake_gbp": 100.0, "price": 0.30, "commission": 0.02}
    entry = datetime(2026, 9, 1, tzinfo=UTC)
    snaps = [dict(_snap("c", "2026-09-03T00:00:00Z", 0.38),
                  back={"prob": 0.39, "odds": 2.56, "size_gbp": 50.0},
                  lay={"prob": 0.37, "odds": 2.7, "size_gbp": 50.0}),
             dict(_snap("c", "2026-09-05T00:00:00Z", 0.42),
                  back={"prob": 0.43, "odds": 2.33, "size_gbp": 50.0},
                  lay={"prob": 0.41, "odds": 2.44, "size_gbp": 50.0})]
    # Fair value 0.40: the first snapshot's lay (0.37) is short of it; the second is past it.
    got = score_exchange.exit_past_fair_value(bet, 0.40, snaps, entry)
    assert got["exited_at"] == "2026-09-05T00:00:00Z"
    assert got["pnl_gbp"] == pytest.approx(100.0 * (0.41 / 0.30 - 1) * 0.98, abs=0.01)
    assert got["capital_days"] == pytest.approx(400.0)
    assert score_exchange.exit_past_fair_value(bet, 0.50, snaps, entry) is None
    no = {"outcome": "NO", "stake_gbp": 50.0, "price": 0.72, "commission": 0.06}
    got = score_exchange.exit_past_fair_value(no, 0.40, [dict(
        _snap("c", "2026-09-03T00:00:00Z", 0.35),
        back={"prob": 0.36, "odds": 2.78, "size_gbp": 50.0},
        lay={"prob": 0.34, "odds": 2.94, "size_gbp": 50.0})], entry)
    assert got["pnl_gbp"] == pytest.approx(50.0 * ((1 - 0.36) / 0.72 - 1), abs=0.01)  # a loss


def test_scorer_scores_routed_bets_on_their_own_contract_and_stop_losses() -> None:
    bet = {"outcome": "YES", "stake_gbp": 100.0, "price": 0.30, "net_odds_b": 2.2867,
           "commission": 0.02, "contract_id": "c_twin", "venue": "betfair",
           "route": {"chosen": "betfair"}}
    journal = _journal_pair("p1", "c1", "smarkets", 0.45, 0.4, 0.29, bet)
    journal += _journal_pair("p2", "c1", "smarkets", 0.25, 0.3, 0.20, None,
                             at="2026-09-05T00:00:00Z")
    journal[2]["source"]["reforecast_of"] = "p1"
    journal[3]["source"]["reforecast_of"] = "p1"
    prices = [
        _snap("c1", "2026-09-10T00:00:00Z", 0.20),   # the forecast contract: irrelevant
        dict(_snap("c_twin", "2026-09-04T00:00:00Z", 0.20),
             back={"prob": 0.21, "odds": 4.76, "size_gbp": 50.0},
             lay={"prob": 0.19, "odds": 5.26, "size_gbp": 50.0}),
        _snap("c_twin", "2026-09-10T00:00:00Z", 0.36),
        _snap("c_twin", "2026-09-12T00:00:00Z", None, True, "closed"),
    ]
    result = score_exchange.score(journal, prices, now=datetime(2026, 9, 13, tzinfo=UTC))
    row = result["rows"][0]
    assert row["bet"]["routed"] and row["bet"]["venue"] == "betfair"
    assert row["bet"]["clv_points"] == pytest.approx(0.36 - 0.30)
    assert row["bet"]["won"] is True
    assert result["pooled"]["n_routed"] == 1 and result["pooled"]["n_reforecast"] == 1
    sl = result["stop_losses"][0]
    # Closing the YES at the 0.19 lay on Sep 4 would have lost; holding won.
    assert sl["stop_loss_pnl_gbp"] == pytest.approx(100.0 * (0.19 / 0.30 - 1), abs=0.01)
    assert sl["hold_pnl_gbp"] > 0 and result["pooled"]["stop_loss_minus_hold_pnl_gbp"] < 0
    # The re-forecast pair is not a primary row.
    assert len(result["rows"]) == 1


def test_lock_summary_counts_persistence_across_ticks() -> None:
    rows = [{"pair": ["a", "b"], "at": "2026-09-06T12:00:00Z", "lock_return_on_capital": 0.01},
            {"pair": ["a", "b"], "at": "2026-09-06T13:00:00Z", "lock_return_on_capital": 0.02},
            {"pair": ["c", "d"], "at": "2026-09-06T13:00:00Z", "lock_return_on_capital": 0.03}]
    got = score_exchange.lock_summary(rows)
    assert got["n_observations"] == 3 and got["n_pairs"] == 2
    assert got["n_persistent_pairs"] == 1 and got["mean_lock_return"] == pytest.approx(0.02)
    assert score_exchange.lock_summary([])["n_pairs"] == 0


def test_refresh_snapshots_uses_the_venue_facade(monkeypatch, tmp_path: Path) -> None:
    j = tmp_path / "exchange.jsonl"
    p = tmp_path / "exchange-prices.jsonl"
    rows = _journal_pair("p1", "smarkets:900:1", "smarkets", 0.4, 0.35, 0.29, None)
    j.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(exchanges, "quote_contracts",
                        lambda ids, **kw: {"smarkets:900:1": fixture_contracts()[0]})
    assert score_exchange.refresh_snapshots(j, p) == 1
    assert run_exchange.journal_rows(p)[0]["mid"] == 0.29


# --------------------------------------------------------------------------- workflow, guard


def test_workflow_is_hourly_snapshot_six_hourly_forecast_capped_and_leak_guarded() -> None:
    wf = (ROOT / ".github" / "workflows" / "exchange-paper.yml").read_text(encoding="utf-8")
    lower = wf.lower()
    assert '"23 * * * *"' in wf and "workflow_dispatch:" in wf
    assert "% 6 ))" in wf and "--snapshot-only" in wf
    assert "--provider subscription" in wf and "--require-subscription-auth" in wf
    budget = re.search(r"--budget (\d+(?:\.\d+)?)", wf)
    assert budget is not None and 0 < float(budget.group(1)) <= 10
    assert "--deadline-minutes 45" in wf and "set -o pipefail" in wf
    assert "claude_code_oauth_token" in lower and "leak_patterns" in lower
    assert "journal_leak_guard.py" in wf and "bot/score_exchange.py" in wf
    for f in ("bot/journal/exchange.jsonl", "bot/journal/exchange-prices.jsonl",
              "bot/journal/exchange-arbs.jsonl"):
        assert wf.count(f) >= 2, f  # committed AND preserved as an artifact
    assert "openrouter" not in lower and "asknews" not in lower and "manifold_api_key" not in lower
    assert wf.count("failure() || cancelled()") == 2 and "upload-artifact@v4" in wf
    assert 'title="Exchange paper bot needs attention"' in wf
    # No venue write credential is ever required: Betfair secrets are optional inputs, and
    # the Claude token is only demanded on forecast ticks.
    assert ': "${BETFAIR' not in wf
    assert wf.index("--snapshot-only") < wf.index(': "${CLAUDE_CODE_OAUTH_TOKEN:?')


def test_leak_guard_treats_exchange_records_as_public_platforms() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import journal_leak_guard as guard
    assert {"smarkets", "betfair"} <= set(guard.PUBLIC_PLATFORMS)


def test_policy_doc_and_scorer_agree_on_the_gate() -> None:
    doc = (ROOT / "docs" / "exchange-paper-policy.md").read_text(encoding="utf-8")
    assert f"n >= {score_exchange.GATE_MIN_BETS}" in doc
    assert f"n >= {score_exchange.GATE_MIN_SETTLED}" in doc
    assert f"<= -{score_exchange.GATE_BRIER_MARGIN}" in doc
    assert "GO-LIVE-CANDIDATE" in doc and "KILL" in doc and "HOLD" in doc


# --------------------------------------------------------------------------- book analytics


def test_match_contracts_pairs_same_contract_across_venues_only() -> None:
    a = fixture_contracts()[0]
    twin = dict(a, venue="betfair", contract_id="betfair:77:1",
                close_time="2027-02-01T12:00:00Z")
    far = dict(twin, contract_id="betfair:77:2", close_time="2027-06-01T12:00:00Z")
    other = dict(twin, contract_id="betfair:77:3", name="Andy Burnham")
    matches = exchanges.match_contracts([a, twin, far, other])
    assert matches["smarkets:900:1"] == ["betfair:77:1"]  # same runner, close within 3 days
    assert matches["betfair:77:1"] == ["smarkets:900:1"]
    assert "betfair:77:3" not in matches  # different runner
    # Same-venue duplicates never match each other.
    assert exchanges.match_contracts([a, dict(a, contract_id="smarkets:900:9")]) == {}


def test_cross_venue_lock_direction_commission_and_size() -> None:
    a = fixture_contracts()[0]  # smarkets back 0.30
    b = dict(a, venue="betfair", contract_id="betfair:x:y",
             lay={"prob": 0.33, "odds": 3.03, "size_gbp": 200.0})
    lock = exchanges.cross_venue_lock(a, b, {"smarkets": 0.02, "betfair": 0.06})
    assert lock["back_venue"] == "smarkets" and lock["lay_venue"] == "betfair"
    # YES: back wins 0.70*0.98 = 0.686, lay loses 0.67 -> +0.016; NO: lay wins 0.33*0.94 =
    # 0.3102, back loses 0.30 -> +0.0102. The lock is the worse case.
    assert lock["lock_per_payout_gbp"] == pytest.approx(0.0102, abs=1e-6)
    assert lock["payout_units_gbp"] == pytest.approx(min(420 / 0.30, 200 / 0.33), abs=0.01)
    # Commission can erase it: at 6% on both legs the NO case goes negative.
    assert exchanges.cross_venue_lock(a, b, {"smarkets": 0.06, "betfair": 0.06}) is None
    # No lock when the lay price sits below the back price.
    assert exchanges.cross_venue_lock(a, dict(b, lay={"prob": 0.29, "odds": 3.45,
                                                      "size_gbp": 200.0}), {}) is None
