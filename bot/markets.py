"""Harness-side lookup of matching prediction markets, handed to research runs as data.

Why (operator, 2026-09-03): the sighted research prompt already REQUIRES a search for human
markets, but a prompt requirement is something a model can skip — Manifold trades were
found that would not have been placed had the bot actually read Polymarket/Kalshi on the
same event. "Checks similar questions/markets" was also the strongest research correlate
in the Spring 2026 bot-maker survey (r=+0.34). So the harness does the lookup itself, before
any model call, and puts the candidates in the brief as RECORD-ONLY facts: venue, title,
price, volume, close date, URL. Contract equivalence stays the forecaster's judgment — a
near-miss contract is evidence, not an anchor — and nothing here says which way to move.

Venues (public, unauthenticated, fail-open, 8 s timeouts):
  - Polymarket  gamma-api.polymarket.com/public-search   (events -> markets, Yes price)
  - Manifold    api.manifold.markets/v0/search-markets   (open binary markets, probability)
  - Kalshi has no keyword search on its public API; a candidate cannot be found cheaply
    without scanning thousands of markets, so it is left to the model's own search.
Matching: title tokens (dates, numbers, stopwords stripped — bot/priors.normalize_title)
ranked by Jaccard; candidates below MIN_SIMILARITY are dropped; at most MAX_CANDIDATES
survive across venues. A false candidate costs a few prompt tokens; the model is told they
are candidates. Blind runs never receive this section (the caller decides).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

import priors

USER_AGENT = "forecast-scaffold-bot/0.1 (+https://github.com/edisonymy/forecast-scaffold)"
TIMEOUT = 8.0
MIN_SIMILARITY = 0.2
MAX_CANDIDATES = 6
MAX_TERMS = 8


def _get_json(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:  # noqa: S310 - fixed https hosts
        return json.load(resp)


def _stem(token: str) -> str:
    """Crude suffix stemmer so 'etfs'/'etf', 'inflows'/'inflow', 'exceeding'/'exceed' agree."""
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def tokens_of(title: str) -> frozenset[str]:
    return frozenset(_stem(t) for t in priors.normalize_title(title))


def search_terms(title: str, max_terms: int = MAX_TERMS) -> str:
    """Original-order content words minus dates/numbers/stopwords/possessives, capped — what
    a search box wants (a bag of tokens loses relevance ordering)."""
    keep = priors.normalize_title(title)
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", str(title or ""))
    out: list[str] = []
    for w in words:
        w_norm = priors.normalize_title(w)
        if not w_norm:
            continue
        token = next(iter(w_norm))
        if token in keep and token not in (x.lower() for x in out):
            out.append(token)
        if len(out) >= max_terms:
            break
    return " ".join(out)


def distinctive_terms(title: str, max_terms: int = 3) -> str:
    """The proper-noun-ish words first (capitalised inside the sentence), then the rest —
    venue search boxes behave like AND queries, so two or three distinctive words find a
    market that eight words never will."""
    keep = priors.normalize_title(title)
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", str(title or ""))
    proper, common = [], []
    for i, w in enumerate(words):
        w_norm = priors.normalize_title(w)
        if not w_norm:
            continue
        token = next(iter(w_norm))
        if token not in keep or token in proper or token in common:
            continue
        (proper if (i > 0 and w[0].isupper()) else common).append(token)
    return " ".join((proper + common)[:max_terms])


def _iso_from_ms(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def search_polymarket(query: str, limit: int = 8) -> list[dict[str, Any]]:
    url = "https://gamma-api.polymarket.com/public-search?" + urllib.parse.urlencode(
        {"q": query, "limit_per_type": limit})
    data = _get_json(url)
    out: list[dict[str, Any]] = []
    for event in (data or {}).get("events") or []:
        for m in event.get("markets") or []:
            if m.get("closed") or m.get("active") is False:
                continue
            try:
                prices = json.loads(m.get("outcomePrices") or "[]")
                outcomes = json.loads(m.get("outcomes") or "[]")
            except (TypeError, ValueError):
                prices, outcomes = [], []
            yes = None
            if outcomes and prices and str(outcomes[0]).lower() == "yes":
                try:
                    yes = float(prices[0])
                except (TypeError, ValueError):
                    yes = None
            slug = m.get("slug") or event.get("slug") or ""
            out.append({
                "venue": "Polymarket",
                "title": str(m.get("question") or event.get("title") or ""),
                "price": yes,
                "volume": _num(m.get("volume")),
                "close": str(m.get("endDate") or "")[:10],
                "url": f"https://polymarket.com/market/{slug}" if slug else "",
            })
    return out


def search_manifold(query: str, limit: int = 8) -> list[dict[str, Any]]:
    url = "https://api.manifold.markets/v0/search-markets?" + urllib.parse.urlencode(
        {"term": query, "limit": limit, "filter": "open"})
    data = _get_json(url)
    out: list[dict[str, Any]] = []
    for m in data or []:
        if m.get("isResolved") or m.get("outcomeType") not in (None, "BINARY"):
            continue
        out.append({
            "venue": "Manifold",
            "title": str(m.get("question") or ""),
            "price": _num(m.get("probability")),
            "volume": _num(m.get("volume")),
            "close": _iso_from_ms(m.get("closeTime")),
            "url": str(m.get("url") or ""),
        })
    return out


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


VENUES = (search_polymarket, search_manifold)


def candidate_markets(
    title: str,
    *,
    searchers: tuple = VENUES,
    min_similarity: float = MIN_SIMILARITY,
    max_candidates: int = MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Ranked candidates across venues; every failure is swallowed (a market lookup must
    never block a forecast)."""
    tokens = tokens_of(title)
    if not tokens:
        return []
    # Venue search boxes behave like AND queries: try the 3 most distinctive words, then 2,
    # then the plain first-4 content words; stop at the first query that returns anything.
    queries: list[str] = []
    for q in (distinctive_terms(title, 3), distinctive_terms(title, 2), search_terms(title, 4)):
        if q and q not in queries:
            queries.append(q)
    found: list[dict[str, Any]] = []
    for search in searchers:
        try:
            for q in queries:
                hits = search(q)
                if hits:
                    found.extend(hits)
                    break
        except Exception as exc:  # noqa: BLE001 - fail open, one venue at a time
            print(f"  market lookup ({getattr(search, '__name__', 'venue')}) unavailable: {exc}")
    scored = []
    seen: set[str] = set()
    for m in found:
        key = (m.get("url") or m.get("title") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        sim = priors.jaccard(tokens, tokens_of(m.get("title") or ""))
        if sim >= min_similarity:
            m = dict(m, similarity=round(sim, 2))
            scored.append(m)
    scored.sort(key=lambda m: (-m["similarity"], -(m.get("volume") or 0.0)))
    return scored[:max_candidates]


def format_market_facts(candidates: list[dict[str, Any]]) -> str:
    """Record-only section for the sighted brief. Always non-empty for sighted runs: a
    'none found' line is itself the fact the run must not skip."""
    lines = ["## Market candidates found by the harness (record-only data)", ""]
    if not candidates:
        lines.append("The harness title search on Polymarket and Manifold found no candidate "
                     "market. Run your own market check anyway (Kalshi and bookmakers are not "
                     "searched here) and record what you find, including 'none found'.")
        return "\n".join(lines) + "\n"
    lines.append("Candidates by title similarity. Whether any contract actually matches this "
                 "question's resolution terms (threshold, deadline, source, fine print) is your "
                 "judgment to make and state; a near-miss contract is evidence, not an anchor. "
                 "Record the ones you checked.")
    for m in candidates:
        price = "n/a" if m.get("price") is None else f"{float(m['price']):.2f}"
        vol = "" if m.get("volume") is None else f", volume {float(m['volume']):,.0f}"
        close = f", closes {m['close']}" if m.get("close") else ""
        lines.append(f"- {m['venue']}: \"{m['title'][:110]}\" — Yes price {price}{vol}{close}"
                     f"{' — ' + m['url'] if m.get('url') else ''}")
    return "\n".join(lines) + "\n"


def market_facts_section(title: str) -> str:
    """Convenience: lookup + format, never raises."""
    try:
        return format_market_facts(candidate_markets(title))
    except Exception as exc:  # noqa: BLE001
        print(f"  market lookup failed ({exc})")
        return format_market_facts([])
