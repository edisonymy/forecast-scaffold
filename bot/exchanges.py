"""Read-only clients for the UK betting exchanges the paper-trading bot watches.

Two venues, one normalised shape. Every function here is a *reader*: nothing in this module
can place, cancel, or size a real order, and there is deliberately no code path that could —
the paper bot (bot/run_exchange.py) journals what it WOULD have done at the touch price and
bot/score_exchange.py grades it later against the closing line and the settlement.

Venues
  - Smarkets  api.smarkets.com/v3  — public, unauthenticated reads (events, markets,
    contracts, quotes). Politics + current-affairs event domains. Prices are basis points of
    probability (4386 = 43.86%), quantities in SMARKETS_QUANTITY_SCALE units of GBP.
    In Smarkets' order-book naming a resting *offer* is what you BACK into (someone is
    selling YES at that price) and a resting *bid* is what you LAY into.
  - Betfair   api.betfair.com/exchange/betting/rest/v1.0 — needs an app key (the free
    DELAYED key is fine for paper: prices lag 1-180 s, which is irrelevant at our horizon)
    and a session token from identitysso. Politics eventTypeId 2378961. Odds are decimal;
    ``availableToBack`` is the best odds you can back at, ``availableToLay`` the best you
    can lay at.

Normalised contract (one binary "will this selection win" per runner):
    {
      "venue": "smarkets" | "betfair",
      "contract_id": "<venue>:<market_id>:<selection_id>",   # journal key
      "market_id": str, "selection_id": str,
      "event": str, "market": str, "name": str,               # event, market, runner names
      "runners": [str, ...],                                  # every runner in the market
      "rules": str,                                           # venue settlement text, if any
      "close_time": "YYYY-MM-DDTHH:MM:SSZ" | None,
      "url": str,
      "back": {"prob": float, "odds": float, "size_gbp": float} | None,  # buy YES here
      "lay":  {"prob": float, "odds": float, "size_gbp": float} | None,  # sell YES here
      "mid": float | None, "last": float | None,
      "matched_gbp": float | None,
      "status": "open" | "suspended" | "closed",
      "outcome": True | False | None,                          # settled result when known
    }
``back.prob >= lay.prob`` always (you buy at the offer, sell at the bid); ``mid`` is their
average. A one-sided book leaves the missing side None and ``mid`` None — the selection
filter drops it.

Units that could not be verified offline when this was written (2026-09-06) are the
SMARKETS_*_SCALE constants; ``python bot/exchanges.py --probe`` prints raw and scaled quotes
side by side so the first live run checks them in one glance.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

USER_AGENT = "forecast-scaffold-exchange-paper/0.1 (+https://github.com/edisonymy/forecast-scaffold)"
TIMEOUT = 20.0

# ---- Smarkets -------------------------------------------------------------------------
SMARKETS_API = "https://api.smarkets.com/v3"
SMARKETS_SITE = "https://smarkets.com"
#: Event ``type_domain`` values the paper bot watches. Sports are deliberately absent: the
#: bot's edge is slow judgment on public affairs, not a quant model of a match.
SMARKETS_TYPE_DOMAINS = ("politics", "current_affairs")
SMARKETS_STATES = ("upcoming", "live")
#: Quote ``price`` is basis points of probability: 4386 -> 0.4386.
SMARKETS_PRICE_SCALE = 10_000.0
#: Quote ``quantity`` in units of 1/SMARKETS_QUANTITY_SCALE GBP (10000 -> GBP 1.00). VERIFY
#: with ``--probe`` on first use; if the printed sizes look 100x off, this is the knob.
SMARKETS_QUANTITY_SCALE = 10_000.0
SMARKETS_MAX_IDS_PER_CALL = 20
#: Quotes are rate-limited (50/min documented, ~20/min observed by users); pace calls.
SMARKETS_PACE_SECONDS = 1.5

# ---- Betfair --------------------------------------------------------------------------
BETFAIR_API = "https://api.betfair.com/exchange/betting/rest/v1.0"
BETFAIR_LOGIN = "https://identitysso.betfair.com/api/login"
BETFAIR_CERT_LOGIN = "https://identitysso-cert.betfair.com/api/certlogin"
BETFAIR_KEEPALIVE = "https://identitysso.betfair.com/api/keepAlive"
BETFAIR_SITE = "https://www.betfair.com/exchange/plus/politics/market/"
BETFAIR_POLITICS_EVENT_TYPE = "2378961"
#: listMarketBook with EX_BEST_OFFERS weighs 5 per market against a 200 cap -> 40 markets.
BETFAIR_MAX_BOOKS_PER_CALL = 40
BETFAIR_MAX_CATALOGUE = 200

_TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- HTTP


def _request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
             context: ssl.SSLContext | None = None, timeout: float = TIMEOUT) -> Any:
    request = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(  # noqa: S310 - fixed https hosts
        request, timeout=timeout, context=context,
    ) as resp:
        return json.load(resp)


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    return _request(url, headers=headers)


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
    return _request(url, data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json", **headers})


def _iso(value: Any) -> str | None:
    """Normalise a venue timestamp to ``YYYY-MM-DDTHH:MM:SSZ``; None when unparseable."""
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN guard


def _strip_html(text: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", str(text or "")))).strip()


def _side(prob: float | None, size_gbp: float | None) -> dict[str, float] | None:
    if prob is None or not 0.0 < prob < 1.0:
        return None
    return {"prob": round(prob, 6), "odds": round(1.0 / prob, 4),
            "size_gbp": round(max(size_gbp or 0.0, 0.0), 2)}


def _finish(contract: dict[str, Any]) -> dict[str, Any]:
    back, lay = contract.get("back"), contract.get("lay")
    contract["mid"] = (
        round((back["prob"] + lay["prob"]) / 2.0, 6) if back and lay else None
    )
    return contract


# --------------------------------------------------------------------------- Smarkets


def _smarkets_events(get: Callable[[str], Any], *, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    query = [("state", s) for s in SMARKETS_STATES]
    query += [("type_domain", d) for d in SMARKETS_TYPE_DOMAINS]
    query += [("sort", "id"), ("limit", str(min(limit, 100)))]
    url = f"{SMARKETS_API}/events/?" + urllib.parse.urlencode(query)
    for _ in range(10):  # pagination guard
        page = get(url)
        events.extend(e for e in (page.get("events") or []) if isinstance(e, dict))
        next_page = (page.get("pagination") or {}).get("next_page")
        if not next_page or len(events) >= limit:
            break
        url = next_page if str(next_page).startswith("http") else SMARKETS_API + str(next_page)
    return events[:limit]


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def smarkets_contracts(get: Callable[[str], Any] | None = None, *, limit: int = 100,
                       pace: float = SMARKETS_PACE_SECONDS) -> list[dict[str, Any]]:
    """Every open binary contract in Smarkets politics/current-affairs events, quoted."""
    get = get or _get_json
    events = _smarkets_events(get, limit=limit)
    by_event = {str(e.get("id")): e for e in events if e.get("id") is not None}
    if not by_event:
        return []
    markets: list[dict[str, Any]] = []
    for ids in _chunks(list(by_event), SMARKETS_MAX_IDS_PER_CALL):
        page = get(f"{SMARKETS_API}/events/{','.join(ids)}/markets/")
        markets.extend(m for m in (page.get("markets") or []) if isinstance(m, dict))
        time.sleep(pace)
    by_market = {str(m.get("id")): m for m in markets if m.get("id") is not None}
    out: list[dict[str, Any]] = []
    for ids in _chunks(list(by_market), SMARKETS_MAX_IDS_PER_CALL):
        joined = ",".join(ids)
        contracts = get(f"{SMARKETS_API}/markets/{joined}/contracts/")
        time.sleep(pace)
        quotes = get(f"{SMARKETS_API}/markets/{joined}/quotes/")
        time.sleep(pace)
        runners_by_market: dict[str, list[str]] = {}
        rows = [c for c in (contracts.get("contracts") or []) if isinstance(c, dict)]
        for c in rows:
            runners_by_market.setdefault(str(c.get("market_id")), []).append(str(c.get("name", "")))
        for c in rows:
            out.append(smarkets_normalise(
                c, by_market.get(str(c.get("market_id")), {}),
                by_event.get(str(by_market.get(str(c.get("market_id")), {}).get("event_id")), {}),
                (quotes or {}).get(str(c.get("id"))) or {},
                runners_by_market.get(str(c.get("market_id")), []),
            ))
    return out


def smarkets_normalise(contract: dict[str, Any], market: dict[str, Any],
                       event: dict[str, Any], quote: dict[str, Any],
                       runners: list[str]) -> dict[str, Any]:
    """Pure: one Smarkets contract + its market/event/quote -> the normalised shape."""
    def best(entries: Any, pick: Callable[[list[float]], float]) -> tuple[float | None, float]:
        rows = [(p, q) for e in (entries or []) if isinstance(e, dict)
                if (p := _num(e.get("price"))) is not None
                for q in [_num(e.get("quantity")) or 0.0]]
        if not rows:
            return None, 0.0
        target = pick([p for p, _ in rows])
        size = sum(q for p, q in rows if p == target)
        return target / SMARKETS_PRICE_SCALE, size / SMARKETS_QUANTITY_SCALE

    # Offers are sellers of YES: the LOWEST offer is the cheapest back. Bids are buyers:
    # the HIGHEST bid is the best lay.
    back_prob, back_size = best(quote.get("offers"), min)
    lay_prob, lay_size = best(quote.get("bids"), max)
    last = _num(quote.get("last_executed_price"))
    state = str(contract.get("state") or market.get("state") or "open").lower()
    outcome: bool | None = None
    result = str(contract.get("outcome") or contract.get("result") or "").lower()
    if state in ("settled", "closed", "resolved") or result:
        if result in ("winner", "won", "yes", "true", "1"):
            outcome = True
        elif result in ("loser", "lost", "no", "false", "0"):
            outcome = False
    status = "closed" if state in ("settled", "closed", "resolved", "cancelled", "voided") else (
        "suspended" if state in ("suspended", "halted") else "open")
    slug = str(event.get("full_slug") or "")
    return _finish({
        "venue": "smarkets",
        "contract_id": f"smarkets:{market.get('id')}:{contract.get('id')}",
        "market_id": str(market.get("id", "")),
        "selection_id": str(contract.get("id", "")),
        "event": str(event.get("name", "")),
        "market": str(market.get("name", "")),
        "name": str(contract.get("name", "")),
        "runners": runners,
        "rules": _strip_html(market.get("description") or market.get("rules") or ""),
        "close_time": _iso(market.get("close_time") or event.get("start_datetime")
                           or event.get("start_date")),
        "url": SMARKETS_SITE + slug if slug else "",
        "back": _side(back_prob, back_size),
        "lay": _side(lay_prob, lay_size),
        "last": (last / SMARKETS_PRICE_SCALE) if last else None,
        "matched_gbp": _num(market.get("volume")),
        "status": status,
        "outcome": outcome,
    })


# --------------------------------------------------------------------------- Betfair


def betfair_headers(app_key: str, token: str) -> dict[str, str]:
    return {"X-Application": app_key, "X-Authentication": token}


def betfair_login(app_key: str, username: str, password: str, *,
                  cert_path: str | None = None, key_path: str | None = None,
                  post: Callable[..., Any] | None = None) -> str:
    """Session token via the interactive endpoint, or the certificate endpoint when a client
    cert is configured (required for accounts with 2FA). Never logs the credentials."""
    form = urllib.parse.urlencode({"username": username, "password": password}).encode()
    headers = {"X-Application": app_key, "Content-Type": "application/x-www-form-urlencoded"}
    if post is not None:
        payload = post(BETFAIR_CERT_LOGIN if cert_path else BETFAIR_LOGIN, form, headers)
    elif cert_path:
        context = ssl.create_default_context()
        context.load_cert_chain(cert_path, key_path)
        payload = _request(BETFAIR_CERT_LOGIN, data=form, headers=headers, context=context)
    else:
        payload = _request(BETFAIR_LOGIN, data=form, headers=headers)
    token = payload.get("sessionToken") or payload.get("token")
    status = str(payload.get("loginStatus") or payload.get("status") or "")
    if not token or status.upper() != "SUCCESS":
        raise RuntimeError(f"Betfair login failed: {status or 'no status'}")
    return str(token)


def betfair_session_from_env(post: Callable[..., Any] | None = None) -> tuple[str, str] | None:
    """(app_key, session_token) from the environment, or None when Betfair is not configured.
    A pre-issued BETFAIR_SESSION_TOKEN wins; else username/password (+ optional cert)."""
    app_key = os.environ.get("BETFAIR_APP_KEY", "").strip()
    if not app_key:
        return None
    token = os.environ.get("BETFAIR_SESSION_TOKEN", "").strip()
    if token:
        return app_key, token
    user = os.environ.get("BETFAIR_USERNAME", "").strip()
    password = os.environ.get("BETFAIR_PASSWORD", "")
    if not (user and password):
        return None
    token = betfair_login(app_key, user, password,
                          cert_path=os.environ.get("BETFAIR_CERT_PATH") or None,
                          key_path=os.environ.get("BETFAIR_KEY_PATH") or None, post=post)
    return app_key, token


def betfair_contracts(app_key: str, token: str, post: Callable[..., Any] | None = None,
                      *, max_markets: int = BETFAIR_MAX_CATALOGUE) -> list[dict[str, Any]]:
    """Every runner of every open Betfair politics market, quoted (EX_BEST_OFFERS)."""
    headers = betfair_headers(app_key, token)
    post = post or (lambda url, body: _post_json(url, body, headers))
    catalogue = post(f"{BETFAIR_API}/listMarketCatalogue/", {
        "filter": {"eventTypeIds": [BETFAIR_POLITICS_EVENT_TYPE]},
        "maxResults": str(max_markets),
        "sort": "MAXIMUM_TRADED",
        "marketProjection": ["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION",
                             "MARKET_DESCRIPTION"],
    })
    markets = [m for m in (catalogue or []) if isinstance(m, dict) and m.get("marketId")]
    books: dict[str, dict[str, Any]] = {}
    for ids in _chunks([str(m["marketId"]) for m in markets], BETFAIR_MAX_BOOKS_PER_CALL):
        page = post(f"{BETFAIR_API}/listMarketBook/", {
            "marketIds": ids, "priceProjection": {"priceData": ["EX_BEST_OFFERS"]},
        })
        for book in page or []:
            if isinstance(book, dict) and book.get("marketId"):
                books[str(book["marketId"])] = book
    out: list[dict[str, Any]] = []
    for market in markets:
        out.extend(betfair_normalise(market, books.get(str(market["marketId"]), {})))
    return out


def betfair_normalise(market: dict[str, Any], book: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure: one Betfair catalogue entry + its market book -> one contract per runner."""
    runners_meta = [r for r in (market.get("runners") or []) if isinstance(r, dict)]
    names = {str(r.get("selectionId")): str(r.get("runnerName", "")) for r in runners_meta}
    book_runners = {str(r.get("selectionId")): r for r in (book.get("runners") or [])
                    if isinstance(r, dict)}
    market_status = str(book.get("status") or "OPEN").upper()
    status = {"OPEN": "open", "SUSPENDED": "suspended"}.get(market_status, "closed")
    event = market.get("event") or {}
    rules = _strip_html((market.get("description") or {}).get("rules", ""))
    market_id = str(market.get("marketId"))
    out: list[dict[str, Any]] = []
    for selection_id, name in names.items():
        runner = book_runners.get(selection_id, {})
        ex = runner.get("ex") or {}
        atb = [(p, s) for e in (ex.get("availableToBack") or []) if isinstance(e, dict)
               if (p := _num(e.get("price"))) and p > 1.0 for s in [_num(e.get("size")) or 0.0]]
        atl = [(p, s) for e in (ex.get("availableToLay") or []) if isinstance(e, dict)
               if (p := _num(e.get("price"))) and p > 1.0 for s in [_num(e.get("size")) or 0.0]]
        # Best back = highest odds on offer (cheapest YES); best lay = lowest odds.
        back = max(atb, key=lambda x: x[0]) if atb else None
        lay = min(atl, key=lambda x: x[0]) if atl else None
        runner_status = str(runner.get("status") or "ACTIVE").upper()
        outcome = {"WINNER": True, "LOSER": False}.get(runner_status)
        last = _num(runner.get("lastPriceTraded"))
        out.append(_finish({
            "venue": "betfair",
            "contract_id": f"betfair:{market_id}:{selection_id}",
            "market_id": market_id,
            "selection_id": selection_id,
            "event": str(event.get("name", "")),
            "market": str(market.get("marketName", "")),
            "name": name,
            "runners": list(names.values()),
            "rules": rules,
            "close_time": _iso(market.get("marketStartTime")),
            "url": BETFAIR_SITE + market_id,
            "back": _side(1.0 / back[0], back[1]) if back else None,
            "lay": _side(1.0 / lay[0], lay[1]) if lay else None,
            "last": (1.0 / last) if last and last > 1.0 else None,
            "matched_gbp": _num(book.get("totalMatched", market.get("totalMatched"))),
            "status": status if runner_status in ("ACTIVE", "WINNER", "LOSER") else "closed",
            "outcome": outcome,
        }))
    return out


# --------------------------------------------------------------------------- facade


def load_contracts(*, venues: Iterable[str] = ("smarkets", "betfair"),
                   smarkets_get: Callable[[str], Any] | None = None,
                   betfair_post: Callable[..., Any] | None = None,
                   betfair_session: tuple[str, str] | None = None,
                   fixture: str | None = None) -> list[dict[str, Any]]:
    """All quoted contracts across the configured venues. Fail-open per venue: a venue that
    errors prints one line and contributes nothing, so one outage never blanks the run.
    ``fixture`` (a JSON file of normalised contracts) replaces the network entirely."""
    if fixture:
        with open(fixture, encoding="utf-8") as fh:
            rows = json.load(fh)
        return [_finish(dict(r)) for r in rows if isinstance(r, dict)]
    out: list[dict[str, Any]] = []
    if "smarkets" in venues:
        try:
            out.extend(smarkets_contracts(smarkets_get))
        except Exception as exc:  # noqa: BLE001 — venue outage must not abort the run
            print(f"smarkets: lookup failed ({type(exc).__name__}: {str(exc)[:160]})")
    if "betfair" in venues:
        session = betfair_session
        if session is None:
            try:
                session = betfair_session_from_env()
            except Exception as exc:  # noqa: BLE001
                print(f"betfair: login failed ({type(exc).__name__}: {str(exc)[:160]})")
                session = None
        if session is None:
            print("betfair: not configured (BETFAIR_APP_KEY + credentials) — skipping venue")
        else:
            try:
                out.extend(betfair_contracts(session[0], session[1], betfair_post))
            except Exception as exc:  # noqa: BLE001
                print(f"betfair: lookup failed ({type(exc).__name__}: {str(exc)[:160]})")
    return out


def smarkets_quote_by_market_ids(market_ids: Iterable[str],
                                 get: Callable[[str], Any] | None = None, *,
                                 pace: float = SMARKETS_PACE_SECONDS) -> dict[str, dict[str, Any]]:
    """contract_id -> normalised contract for the given Smarkets MARKET ids, by id (not via
    the events listing, which only returns upcoming/live events and so never shows a
    settlement). Names come from the market/contract objects; event fields are blank."""
    get = get or _get_json
    out: dict[str, dict[str, Any]] = {}
    for ids in _chunks(sorted(set(str(m) for m in market_ids)), SMARKETS_MAX_IDS_PER_CALL):
        joined = ",".join(ids)
        markets = {str(m.get("id")): m for m in (get(f"{SMARKETS_API}/markets/{joined}/")
                                                 .get("markets") or []) if isinstance(m, dict)}
        time.sleep(pace)
        contracts = [c for c in (get(f"{SMARKETS_API}/markets/{joined}/contracts/")
                                 .get("contracts") or []) if isinstance(c, dict)]
        time.sleep(pace)
        quotes = get(f"{SMARKETS_API}/markets/{joined}/quotes/") or {}
        time.sleep(pace)
        runners: dict[str, list[str]] = {}
        for c in contracts:
            runners.setdefault(str(c.get("market_id")), []).append(str(c.get("name", "")))
        for c in contracts:
            mid = str(c.get("market_id"))
            row = smarkets_normalise(c, markets.get(mid, {"id": mid}), {},
                                     quotes.get(str(c.get("id"))) or {}, runners.get(mid, []))
            out[row["contract_id"]] = row
    return out


def betfair_books_by_market_ids(market_ids: Iterable[str], app_key: str, token: str,
                                post: Callable[..., Any] | None = None,
                                ) -> dict[str, dict[str, Any]]:
    """contract_id -> normalised contract from listMarketBook by MARKET id. Unlike the
    catalogue, the book endpoint still answers for CLOSED markets (for a window after
    settlement) with runner status WINNER/LOSER — this is where outcomes come from."""
    headers = betfair_headers(app_key, token)
    post = post or (lambda url, body: _post_json(url, body, headers))
    out: dict[str, dict[str, Any]] = {}
    for ids in _chunks(sorted(set(str(m) for m in market_ids)), BETFAIR_MAX_BOOKS_PER_CALL):
        page = post(f"{BETFAIR_API}/listMarketBook/", {
            "marketIds": ids, "priceProjection": {"priceData": ["EX_BEST_OFFERS"]},
        })
        for book in page or []:
            if not (isinstance(book, dict) and book.get("marketId")):
                continue
            # Book-only catalogue stub: runner names are unknown here (the journal already
            # holds them); selection ids are what the contract_id is built from.
            stub = {"marketId": book["marketId"],
                    "runners": [{"selectionId": r.get("selectionId"), "runnerName": ""}
                                for r in (book.get("runners") or []) if isinstance(r, dict)]}
            for row in betfair_normalise(stub, book):
                out[row["contract_id"]] = row
    return out


def quote_contracts(contract_ids: Iterable[str], *,
                    smarkets_get: Callable[[str], Any] | None = None,
                    betfair_post: Callable[..., Any] | None = None,
                    betfair_session: tuple[str, str] | None = None,
                    fixture: str | None = None) -> dict[str, dict[str, Any]]:
    """Current state of the given contract ids, quoted BY ID per venue so settled and
    closed markets still answer. Fail-open per venue. ``fixture`` (normalised contracts)
    replaces the network. Ids whose venue is unavailable are simply absent."""
    wanted = set(contract_ids)
    if fixture:
        return {c["contract_id"]: c for c in load_contracts(fixture=fixture)
                if c["contract_id"] in wanted}
    by_venue: dict[str, set[str]] = {}
    for cid in wanted:
        parts = cid.split(":")
        if len(parts) >= 3:
            by_venue.setdefault(parts[0], set()).add(parts[1])
    out: dict[str, dict[str, Any]] = {}
    if by_venue.get("smarkets"):
        try:
            out.update(smarkets_quote_by_market_ids(by_venue["smarkets"], smarkets_get))
        except Exception as exc:  # noqa: BLE001
            print(f"smarkets: quote-by-id failed ({type(exc).__name__}: {str(exc)[:160]})")
    if by_venue.get("betfair"):
        session = betfair_session
        if session is None:
            try:
                session = betfair_session_from_env()
            except Exception as exc:  # noqa: BLE001
                print(f"betfair: login failed ({type(exc).__name__}: {str(exc)[:160]})")
        if session is not None:
            try:
                out.update(betfair_books_by_market_ids(by_venue["betfair"], session[0],
                                                       session[1], betfair_post))
            except Exception as exc:  # noqa: BLE001
                print(f"betfair: quote-by-id failed ({type(exc).__name__}: {str(exc)[:160]})")
    return {cid: c for cid, c in out.items() if cid in wanted}


# --------------------------------------------------------------------------- odds ladder

#: Betfair's price ladder: (upper bound of the band, tick in decimal odds). Smarkets uses a
#: similar ladder; the paper maker arm applies this one to both, which is conservative on
#: Smarkets (its finer ticks would let a maker order improve by less).
ODDS_LADDER = ((2.0, 0.01), (3.0, 0.02), (4.0, 0.05), (6.0, 0.1), (10.0, 0.2), (20.0, 0.5),
               (30.0, 1.0), (50.0, 2.0), (100.0, 5.0), (1000.0, 10.0))


def odds_tick(odds: float) -> float:
    for upper, tick in ODDS_LADDER:
        if odds < upper + 1e-9:
            return tick
    return 10.0


def snap_odds(odds: float, direction: str) -> float:
    """Snap decimal odds to the ladder: ``direction`` "down" (shorter odds = higher
    probability) or "up" (longer odds)."""
    tick = odds_tick(odds)
    steps = odds / tick
    snapped = (math.floor(steps + 1e-9) if direction == "down" else math.ceil(steps - 1e-9)) * tick
    return round(max(1.01, snapped), 4)


def snap_nearest(odds: float) -> float:
    """Nearest ladder price. Odds derived as 1/probability (1.1111 for 0.90) are rarely on
    the ladder; the venue's actual touch is the nearest rung."""
    tick = odds_tick(odds)
    return round(max(1.01, round(odds / tick) * tick), 4)


def ticks_between(odds_lo: float, odds_hi: float) -> int:
    """Whole ladder ticks between two prices, each snapped to its nearest rung first
    (0 when they coincide or cross)."""
    lo, hi = snap_nearest(odds_lo), snap_nearest(odds_hi)
    if hi <= lo:
        return 0
    n, x = 0, lo
    while x < hi - 1e-9 and n < 10_000:
        x = round(x + odds_tick(x), 6)
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true",
                        help="pull every venue once and print raw + scaled quotes so the "
                             "unit constants can be checked by eye")
    parser.add_argument("--venue", action="append", choices=["smarkets", "betfair"],
                        help="restrict to a venue (repeatable; default both)")
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args(argv)
    contracts = load_contracts(venues=tuple(args.venue or ("smarkets", "betfair")))
    print(f"{len(contracts)} contract(s)")
    for c in contracts[: args.limit]:
        print(json.dumps({k: c[k] for k in ("venue", "contract_id", "event", "market", "name",
                                            "close_time", "back", "lay", "mid", "last",
                                            "matched_gbp", "status")}, ensure_ascii=False))
    if args.probe:
        print("\nCheck by eye: a liquid political favourite should show back.size_gbp in the "
              "tens-to-thousands of pounds and back.prob a few points above lay.prob. If "
              "Smarkets sizes look 100x off, adjust SMARKETS_QUANTITY_SCALE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------- book analytics
# Pure functions over normalised contracts. Nothing here trades; these feed the paper
# runner's selection and its arbitrage LEDGER (a record of locks that existed, never an order).

#: Same contract on two venues: event+market+runner tokens this similar, closes this close.
MATCH_MIN_SIMILARITY = 0.6
MATCH_MAX_CLOSE_GAP_DAYS = 3.0


def _match_tokens(contract: dict[str, Any]) -> frozenset[str]:
    import priors  # sibling module (bot/priors.py); keep exchanges importable on its own
    text = " ".join(str(contract.get(k, "")) for k in ("event", "market", "name"))
    return frozenset(priors.normalize_title(text))


def _close_gap_days(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    ta, tb = a.get("close_time"), b.get("close_time")
    if not (ta and tb):
        return None
    try:
        da = datetime.fromisoformat(str(ta).replace("Z", "+00:00"))
        db = datetime.fromisoformat(str(tb).replace("Z", "+00:00"))
    except ValueError:
        return None
    return abs((da - db).total_seconds()) / 86_400.0


def match_contracts(contracts: list[dict[str, Any]], *,
                    min_similarity: float = MATCH_MIN_SIMILARITY,
                    max_close_gap_days: float = MATCH_MAX_CLOSE_GAP_DAYS,
                    ) -> dict[str, list[str]]:
    """contract_id -> ids of the same contract on OTHER venues (best match per venue).

    Matching is title-token Jaccard over event + market + runner plus a close-time gap
    bound; it is a CANDIDATE relation, not proof of identical settlement terms — the
    sighted forecast still adjudicates the fine print, and the arbitrage ledger records
    both venues' rules so a human can check before any live use."""
    import priors
    by_venue: dict[str, list[dict[str, Any]]] = {}
    for c in contracts:
        by_venue.setdefault(str(c.get("venue")), []).append(c)
    tokens = {c["contract_id"]: _match_tokens(c) for c in contracts}
    out: dict[str, list[str]] = {}
    for c in contracts:
        mine = tokens[c["contract_id"]]
        if not mine:
            continue
        for venue, others in by_venue.items():
            if venue == c.get("venue"):
                continue
            best_id, best_sim = None, 0.0
            for o in others:
                sim = priors.jaccard(mine, tokens[o["contract_id"]])
                if sim < min_similarity or sim <= best_sim:
                    continue
                gap = _close_gap_days(c, o)
                if gap is not None and gap > max_close_gap_days:
                    continue
                best_id, best_sim = o["contract_id"], sim
            if best_id:
                out.setdefault(c["contract_id"], []).append(best_id)
    return out


def cross_venue_lock(a: dict[str, Any], b: dict[str, Any],
                     commission: dict[str, float]) -> dict[str, Any] | None:
    """Back on one venue, lay the same contract on the other: a lock exists when the back
    price (prob) is below the lay price after both commissions. Returns the better of the
    two directions or None. Sizes are the binding leg at the touch."""
    best = None
    for back_side, lay_side in ((a, b), (b, a)):
        back, lay = back_side.get("back"), lay_side.get("lay")
        if not (back and lay):
            continue
        cb = commission.get(str(back_side.get("venue")), 0.0)
        cl = commission.get(str(lay_side.get("venue")), 0.0)
        q_back, q_lay = back["prob"], lay["prob"]
        if not (0.0 < q_back < 1.0 and 0.0 < q_lay < 1.0):
            continue
        # Back GBP 1 at q_back (wins (1/q_back - 1)(1-cb)); lay to receive a backer's stake
        # of L = q_lay/q_back... simplest exact form: per GBP 1 of YES-payout hedged both
        # ways, profit if YES = (1-q_back)(1-cb) - (1-q_lay); if NO = q_lay(1-cl) - q_back.
        pnl_yes = (1.0 - q_back) * (1.0 - cb) - (1.0 - q_lay)
        pnl_no = q_lay * (1.0 - cl) - q_back
        lock = min(pnl_yes, pnl_no)
        if lock <= 0:
            continue
        # Capital per GBP of payout: the back stake q_back plus the lay liability (1-q_lay).
        capital = q_back + (1.0 - q_lay)
        # Payout units each leg can absorb at the touch: a back stake B buys B/q_back units;
        # a resting backer stake L on the lay side hedges L/q_lay units.
        size = min(back["size_gbp"] / q_back, lay["size_gbp"] / q_lay)
        cand = {"back_venue": back_side.get("venue"), "back_id": back_side.get("contract_id"),
                "lay_venue": lay_side.get("venue"), "lay_id": lay_side.get("contract_id"),
                "q_back": q_back, "q_lay": q_lay, "lock_per_payout_gbp": round(lock, 6),
                "lock_return_on_capital": round(lock / capital, 6),
                "payout_units_gbp": round(size, 2)}
        if best is None or cand["lock_return_on_capital"] > best["lock_return_on_capital"]:
            best = cand
    return best
