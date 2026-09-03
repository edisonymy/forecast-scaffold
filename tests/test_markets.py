"""Harness-side market lookup (bot/markets.py): parsing, ranking, record-only wording."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

import markets  # noqa: E402

TITLE = ("Will U.S. spot Bitcoin ETFs record a single-day net inflow exceeding $500 million "
         "between August 31 and September 7, 2026?")

POLY = {"events": [{"title": "Bitcoin ETF flows", "slug": "btc-etf", "markets": [
    {"question": "Will spot Bitcoin ETFs see a net inflow day above $500M in September?",
     "outcomePrices": json.dumps(["0.31", "0.69"]), "outcomes": json.dumps(["Yes", "No"]),
     "volume": "123456.7", "endDate": "2026-09-30T00:00:00Z", "slug": "btc-etf-500m-sept",
     "closed": False, "active": True},
    {"question": "Will BlackRock say Digital Asset during earnings call?",
     "outcomePrices": json.dumps(["1", "0"]), "outcomes": json.dumps(["Yes", "No"]),
     "volume": "6394", "endDate": "2026-01-16T00:00:00Z", "slug": "blk-earnings", "closed": True},
]}]}
MANI = [
    {"question": "Will Bitcoin ETF net inflows exceed $500 million on any day this week?",
     "probability": 0.27, "volume": 900.5, "closeTime": 1788000000000, "outcomeType": "BINARY",
     "isResolved": False, "url": "https://manifold.markets/x/btc-etf-week"},
    {"question": "Which price will Bitcoin hit in September 2026?", "probability": None,
     "volume": 2400.1, "closeTime": 1790812740000, "outcomeType": "MULTIPLE_CHOICE",
     "isResolved": False, "url": "https://manifold.markets/x/mc"},
]


class TestSearchTerms:
    def test_strips_dates_numbers_and_stopwords_keeps_order(self) -> None:
        terms = markets.search_terms(TITLE)
        assert terms.startswith("spot bitcoin etfs")
        assert "2026" not in terms and "september" not in terms and "will" not in terms
        assert len(terms.split()) <= markets.MAX_TERMS
        assert markets.search_terms(TITLE, max_terms=4) == "spot bitcoin etfs record"
        assert markets.distinctive_terms(TITLE, 3) == "bitcoin etfs spot"
        assert markets.distinctive_terms(
            "Will North Korea launch at least one ballistic missile between September 3 "
            "and September 5, 2026?", 3) == "north korea launch"
        assert markets.tokens_of("Bitcoin ETF inflows exceeding") == {"bitcoin", "etf", "inflow",
                                                                     "exceed"}


class TestVenueParsers:
    def test_polymarket_parses_yes_price_and_skips_closed(self, monkeypatch) -> None:
        monkeypatch.setattr(markets, "_get_json", lambda url: POLY)
        got = markets.search_polymarket("bitcoin etf")
        assert len(got) == 1
        assert got[0]["venue"] == "Polymarket" and got[0]["price"] == 0.31
        assert got[0]["url"].endswith("btc-etf-500m-sept") and got[0]["close"] == "2026-09-30"

    def test_manifold_keeps_open_binary_only(self, monkeypatch) -> None:
        monkeypatch.setattr(markets, "_get_json", lambda url: MANI)
        got = markets.search_manifold("bitcoin etf")
        assert len(got) == 1 and got[0]["price"] == 0.27 and got[0]["close"].startswith("2026")


class TestCandidates:
    def test_ranks_by_similarity_and_drops_unrelated(self, monkeypatch) -> None:
        searchers = (lambda q: markets.search_polymarket(q), lambda q: markets.search_manifold(q))
        monkeypatch.setattr(markets, "_get_json",
                            lambda url: POLY if "polymarket" in url else MANI)
        got = markets.candidate_markets(TITLE, searchers=searchers)
        assert {m["venue"] for m in got} == {"Polymarket", "Manifold"}
        assert all(m["similarity"] >= markets.MIN_SIMILARITY for m in got)
        assert all("BlackRock" not in m["title"] for m in got)
        assert got == sorted(got, key=lambda m: -m["similarity"])

    def test_venue_failure_is_swallowed(self) -> None:
        def boom(q: str) -> list:
            raise RuntimeError("down")
        assert markets.candidate_markets(TITLE, searchers=(boom,)) == []

    def test_empty_title_no_lookup(self) -> None:
        assert markets.candidate_markets("", searchers=()) == []


class TestWording:
    def test_section_is_record_only_and_never_empty(self) -> None:
        empty = markets.format_market_facts([])
        assert "no candidate market" in empty and "none found" in empty
        full = markets.format_market_facts([{"venue": "Polymarket", "title": "T", "price": 0.31,
                                             "volume": 1000.0, "close": "2026-09-30",
                                             "url": "https://polymarket.com/market/t"}])
        assert "Yes price 0.31" in full and "volume 1,000" in full
        banned = ("should", "likely", "unlikely", "increase", "decrease", "raise", "lower ",
                  "shade", "treat as", "anchor on", "blend")
        for word in banned:
            assert word not in full.lower(), word
