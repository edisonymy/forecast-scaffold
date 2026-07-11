"""Optional AskNews research-source integration for the RESEARCH run (ships DARK).

AskNews was the first-choice research provider of winning Metaculus bots, and research-source
breadth is a measured winner correlate. This module lets the tournament research run start
from a small, dated, linked set of news articles for the question — but it is strictly
OPTIONAL and OFF by default:

  * PORTABILITY / DARK: with no key present (env ASKNEWS_API_KEY or a keyfile OUTSIDE the repo
    at ~/.asknews/key[.txt]), every entry point here returns "" / [] and the research brief is
    byte-identical to today. The skill/harness never requires it.
  * ADDITIONAL, NOT A REPLACEMENT: the injected section is explicitly framed as STARTING
    material to verify and search BEYOND — never a finished digest that stands in for the run's
    own self-directed search. Handing a model a ready-made digest is a measured agency-reducer,
    so the header says so in as many words.
  * NO LEAN: the section carries only sourced articles (title, summary, date, linked source).
    It never bakes in a preliminary yes/no lean — an anti-pattern in the reference bots we
    deliberately avoid.

Key-usage terms (operator, 2026-07-11): the AskNews key is licensed for the Metaculus
competition and dev/bench use ONLY. This module is therefore wired into the tournament
research run (bot/run_bot.py) and NOT into bot/run_manifold.py.

Stdlib only (urllib); no third-party AskNews SDK, so the scaffold stays portable.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# AskNews REST search endpoint (verified live 2026-07-11). A GET with an
# ``Authorization: Bearer <key>`` header returns ``{"as_dicts": [ {article}, ... ], ...}``.
ASKNEWS_API = "https://api.asknews.app/v1/news/search"

# Key lookup mirrors run_manifold.manifold_api_key EXACTLY: env var first, else a keyfile
# OUTSIDE the repo (never committable, never in chat transcripts). The operator writes it
# themselves: a one-line file holding only the key.
KEYFILE = Path.home() / ".asknews" / "key"

UA = {"User-Agent": "forecast-scaffold-asknews/0.1 "
      "(+https://github.com/edisonymy/forecast-scaffold)"}

# strategy label -> the AskNews ``strategy`` query param (both verified live 2026-07-11):
#   'latest news'    -> recency-biased HOT pass (most recent matching articles)
#   'news knowledge' -> deeper semantic HISTORICAL pass
# The strategy param IS the hot-vs-historical mechanism; no separate time-window param is
# needed. method='nl' (natural language) and return_type='dicts' (so the reply carries the
# structured ``as_dicts`` list) round out the verified call shape.
STRATEGY_PARAM = {
    "latest news": "latest news",
    "news knowledge": "news knowledge",
}

# Section header — announces the content is STARTING material to be verified and searched
# beyond, never a substitute for the run's own research. A module constant so tests (and the
# run_manifold compliance guard) can assert on the exact text.
NEWS_HEADER = (
    "## Recent news (AskNews — starting material; verify key claims and search beyond it)"
)

# Per-article block: the winning-bot convention (title / summary / date / linked source).
ARTICLE_TEMPLATE = (
    "**{title}**\n{summary}\nPublish date: {pub_date}\nSource: [{source_id}]({url})"
)

MAX_SECTION_CHARS = 4000          # the whole section is capped ~here (whole-article boundary)
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})  # one retry on rate-limit / server error
RETRY_WAIT_S = 5.0

HOT_ARTICLES = 6                  # hot pass ('latest news')
HISTORICAL_ARTICLES = 10         # historical pass ('news knowledge')


def asknews_key() -> str:
    """The AskNews key: env ASKNEWS_API_KEY first, else the keyfile OUTSIDE the repo.

    Mirrors run_manifold.manifold_api_key exactly (env -> ~/.asknews/key -> ~/.asknews/key.txt).
    Returns "" when none is found so callers no-op silently — the dark-by-default contract."""
    key = os.environ.get("ASKNEWS_API_KEY", "")
    if key:
        return key.strip()
    # Accept key.txt too: Notepad appends .txt and the operator should not have to care.
    for path in (KEYFILE, KEYFILE.with_suffix(".txt")):
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def _search_once(
    query: str, n_articles: int, strategy: str, timeout: int, key: str
) -> list[dict[str, Any]]:
    """One GET against /v1/news/search. Raises on transport/HTTP error (the caller decides
    whether to retry); returns the ``as_dicts`` list (possibly empty) on success."""
    params = urllib.parse.urlencode({
        "query": query,
        "n_articles": n_articles,
        "method": "nl",
        "strategy": STRATEGY_PARAM.get(strategy, strategy),
        "return_type": "dicts",
    })
    request = urllib.request.Request(
        f"{ASKNEWS_API}?{params}", headers={**UA, "Authorization": f"Bearer {key}"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    articles = data.get("as_dicts") if isinstance(data, dict) else None
    return articles if isinstance(articles, list) else []


def search_news(
    query: str, n_articles: int = 6, strategy: str = "latest news", timeout: int = 20
) -> list[dict[str, Any]]:
    """Up to ``n_articles`` article dicts for ``query`` from AskNews /v1/news/search.

    ``strategy`` selects the search behavior: 'latest news' is the recency-biased HOT pass,
    'news knowledge' the deeper HISTORICAL/semantic pass. Returns [] on ANY failure (no key,
    HTTP error, timeout, malformed reply) and NEVER raises — a research run must never fail
    because this add-on did. Retries ONCE on a 429/5xx after RETRY_WAIT_S seconds.
    """
    key = asknews_key()
    if not key:
        return []
    for attempt in range(2):
        try:
            return _search_once(query, n_articles, strategy, timeout, key)
        except urllib.error.HTTPError as exc:
            if attempt == 0 and exc.code in RETRY_STATUS:
                time.sleep(RETRY_WAIT_S)
                continue
            return []
        except Exception:  # noqa: BLE001 — timeout/URLError/JSON/anything: no-op, never raise
            return []
    return []


def _article_url(article: dict[str, Any]) -> str:
    """The article's canonical link (dedupe key and the Source link). '' when absent."""
    return str(article.get("article_url") or article.get("url") or "").strip()


def _format_date(raw: Any) -> str:
    """The publish date as YYYY-MM-DD. ``pub_date`` is ISO 8601 (e.g. '2026-07-10T14:09:55Z');
    fall back to the raw string, then 'unknown', so a malformed date never breaks a section."""
    text = str(raw or "").strip()
    if not text:
        return "unknown"
    return text.split("T", 1)[0]


def _format_article(article: dict[str, Any]) -> str | None:
    """One article rendered per ARTICLE_TEMPLATE, or None when it lacks a title or a link
    (an unlinkable/untitled item is not citable starting material)."""
    title = str(article.get("title") or article.get("eng_title") or "").strip()
    url = _article_url(article)
    if not title or not url:
        return None
    summary = " ".join(str(article.get("summary") or "").split())
    source_id = str(article.get("source_id") or "").strip() or "source"
    return ARTICLE_TEMPLATE.format(
        title=title, summary=summary, pub_date=_format_date(article.get("pub_date")),
        source_id=source_id, url=url,
    )


def news_section(question_title: str) -> str:
    """Formatted AskNews markdown block for the research brief, or "" on any failure/no-key.

    Fetches a HOT pass ('latest news', 6 articles) and a HISTORICAL pass ('news knowledge',
    10 articles), dedupes by article URL (hot wins), formats each per the winning-bot
    template, and caps the whole section at ~MAX_SECTION_CHARS on a whole-article boundary.

    Returns "" when the kill switch ASKNEWS_DISABLE=1 is set (cost/quota control), when no key
    is present (dark by default -> byte-identical behavior), or on ANY error. The section is
    ADDITIONAL starting evidence, never a replacement for the run's own self-directed search,
    and carries NO preliminary yes/no lean.
    """
    if os.environ.get("ASKNEWS_DISABLE") == "1":  # cost/quota kill switch
        return ""
    if not asknews_key():
        return ""
    try:
        hot = search_news(question_title, n_articles=HOT_ARTICLES, strategy="latest news")
        historical = search_news(
            question_title, n_articles=HISTORICAL_ARTICLES, strategy="news knowledge"
        )
        header = f"\n\n{NEWS_HEADER}\n\n"
        section = header
        added = 0
        seen: set[str] = set()
        for article in [*hot, *historical]:
            url = _article_url(article)
            if not url or url in seen:
                continue
            block = _format_article(article)
            if block is None:
                continue
            candidate = section + ("\n\n" if added else "") + block
            if len(candidate) > MAX_SECTION_CHARS:
                break  # cap on a whole-article boundary rather than truncating mid-article
            section = candidate
            seen.add(url)
            added += 1
        return section if added else ""
    except Exception:  # noqa: BLE001 — a formatting bug must never break a research run
        return ""
