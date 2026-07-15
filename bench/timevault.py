"""Time-locked research clients for leak-free pastcasting.

Every document any client returns carries a machine-verifiable timestamp, and a single
choke point (``TimeVault._assert_pre_cutoff``) rejects anything after the vault's cutoff.
The cutoff is pinned at construction — nothing the caller (an agent) passes into a query
can loosen it.

Sources and their guarantee strength:

- **Wayback Machine** (``fetch_page``): the CDX index picks the LAST capture at or before
  the cutoff (server-side ``to=`` bound), the raw snapshot is fetched in ``id_`` mode, and
  because Wayback's own redirects resolve to the NEAREST capture — which can be after the
  requested instant — the effective URL's embedded timestamp is re-verified post-redirect.
  Content is byte-identical to what was live at capture time. Strongest guarantee.
- **Wikipedia** (``wikipedia_asof``): the exact requested title is converted directly to
  an ``en.wikipedia.org/wiki/...`` URL and retrieved only through ``fetch_page``'s
  cutoff-bounded Wayback path. The live MediaWiki API is never queried: even an apparently
  historical revision query first resolves today's title to today's page ID, leaking
  future page moves. A missing pre-cutoff capture is unavailable. Strong guarantee,
  archive coverage permitting.
- **GDELT DOC 2.0** (``search_news``): full-text news discovery inside a window that ends
  at the cutoff (server-side ``ENDDATETIME``), date-sorted (not relevance-sorted, to avoid
  ranking informed by later importance); each hit's ``seendate`` is re-checked client-side.
  Discovery only — for article CONTENT route the URL through ``fetch_page`` so the text
  comes from a pre-cutoff snapshot, not today's live page.
- **BTF-2 corpus** (``search_corpus`` / ``fetch_corpus_page``, optional): a local SQLite
  FTS index over FutureSearch's frozen scrape manifest (build it with
  ``bench/build_corpus_index.py``). The manifest ships URLs + crawl dates ONLY — no page
  text — so this source is DISCOVERY of the exact sources the SOTA teacher searched; the
  ``date_scraped`` crawl timestamp is run through the same ``_assert_pre_cutoff`` choke
  point (hits scraped after the as-of instant are excluded), and every result carries the
  documented scrape-window caveat. Route discovered URLs through ``fetch_page`` for
  pre-cutoff content. Absent a configured corpus, both methods raise a clear error.

Residual leak surface, documented not hidden: the model's own weights (choose questions
that RESOLVE after the model's training cutoff — a set-selection duty, not a tool duty).
There is no live-web path in this module at all.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "forecast-scaffold-timevault/0.1 (leak-free pastcast research)"
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_SNAP = "https://web.archive.org/web"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"

_SNAP_REPLAY = re.compile(r"^/web/(\d{14})id_/(https?://.+)$")
_GDELT_RATELIMIT = "limit requests"
_TRANSIENT_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_HTTP_ATTEMPTS = 3
_MAX_RETRY_AFTER_S = 8.0


class LeakError(RuntimeError):
    """A document from after the cutoff almost reached the agent. Always fatal."""


def parse_cutoff(raw: str) -> datetime:
    """ISO date or datetime -> aware UTC cutoff.

    A bare date is interpreted CONSERVATIVELY as that day's 00:00:00 UTC — 'as of day D'
    grants no day-D information unless the caller supplies the exact instant."""
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"unparseable cutoff {raw!r} (want ISO date/datetime)") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _ts14(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def _parse_stamp(raw: str) -> datetime:
    """Accept the stamp formats the three sources emit."""
    text = str(raw).strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    # Fallback: full ISO 8601 — e.g. the corpus 'date_scraped' carries microseconds and
    # no 'Z' ("2025-10-22T21:30:11.912205"), which the fixed formats above don't cover.
    with contextlib.suppress(ValueError):
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    raise ValueError(f"unrecognized timestamp {raw!r}")


class _TextExtractor(HTMLParser):
    """HTML -> readable text; script/style/noscript dropped, whitespace collapsed."""

    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        return re.sub(r"[ \t]+", " ", "\n".join(self._chunks))


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    # Salvage whatever parsed before any malformed-markup explosion.
    with contextlib.suppress(Exception):
        parser.feed(html)
    return parser.text()


class TimeVault:
    """Time-locked research over Wayback + Wikipedia + GDELT. One cutoff, enforced once."""

    # Fallback caveat if the corpus db predates the corpus_meta table (build writes one).
    CORPUS_CAVEAT_FALLBACK = (
        "BTF-2 scrape manifest (discovery only): hits are URLs the SOTA agent scraped, "
        "NOT page content; 'date' is a crawl timestamp that may post-date the as-of by a "
        "few days (same skew the teacher had). Retrieve real pre-cutoff content via "
        "fetch_page."
    )

    def __init__(self, cutoff: datetime | str, timeout: int = 45,
                 corpus_db: str | None = None) -> None:
        self.cutoff = cutoff if isinstance(cutoff, datetime) else parse_cutoff(cutoff)
        if self.cutoff.tzinfo is None:
            self.cutoff = self.cutoff.replace(tzinfo=UTC)
        self.timeout = timeout
        self.corpus_db = str(corpus_db) if corpus_db else None
        self._corpus_conn: sqlite3.Connection | None = None
        self._corpus_caveat: str | None = None

    # -- the choke point ---------------------------------------------------------------
    def _assert_pre_cutoff(self, stamp: str, what: str) -> datetime:
        dt = _parse_stamp(stamp)
        if dt > self.cutoff:
            raise LeakError(
                f"{what} is stamped {dt.isoformat()} — after the cutoff "
                f"{self.cutoff.isoformat()}; refusing to return it"
            )
        return dt

    # -- transport (single seam; tests replace this) ------------------------------------
    def _http(self, url: str) -> tuple[str, str]:
        """GET url -> (body_text, effective_url_after_redirects).

        Wayback's ``id_`` mode returns the EXACT bytes captured — for many snapshots
        that is the origin server's gzip stream, so decompress on magic bytes (urllib
        never auto-decompresses).

        The three public archive services occasionally return 429/5xx responses or
        stall a read.  A single such blip used to turn an otherwise discoverable source
        into an empty research run.  Retry only transport-level transient failures;
        timestamp validation remains downstream and is never retried or weakened.
        """
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(_HTTP_ATTEMPTS):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    effective = str(resp.geturl())
                    if (
                        urllib.parse.urlparse(url).scheme == "https"
                        and urllib.parse.urlparse(effective).scheme != "https"
                    ):
                        raise LeakError(
                            "HTTPS research transport downgraded after redirect; "
                            f"refusing effective URL {effective!r}"
                        )
                    raw = resp.read()
                    if raw[:2] == b"\x1f\x8b":
                        with contextlib.suppress(OSError, EOFError):
                            raw = gzip.decompress(raw)
                    return raw.decode("utf-8", errors="replace"), effective
            except urllib.error.HTTPError as exc:
                if exc.code not in _TRANSIENT_HTTP_STATUS or attempt + 1 >= _HTTP_ATTEMPTS:
                    raise
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = min(float(retry_after), _MAX_RETRY_AFTER_S)
                except (TypeError, ValueError):
                    # GDELT documents a five-second request interval; use that for 429,
                    # and a short exponential delay for archive-server 5xx responses.
                    delay = 6.0 if exc.code == 429 else 0.5 * (2**attempt)
                time.sleep(max(0.0, delay))
            except (TimeoutError, ConnectionError, urllib.error.URLError):
                if attempt + 1 >= _HTTP_ATTEMPTS:
                    raise
                time.sleep(0.5 * (2**attempt))
        raise AssertionError("unreachable transport retry loop")

    # -- tools --------------------------------------------------------------------------
    def search_news(self, query: str, days_back: int = 120, max_results: int = 10) -> dict:
        """Date-bounded news discovery via GDELT. Returns metadata only — fetch content
        through fetch_page so it comes from a pre-cutoff snapshot."""
        days_back = max(1, min(int(days_back), 365))
        max_results = max(1, min(int(max_results), 25))
        start = self.cutoff.timestamp() - days_back * 86400
        start_dt = datetime.fromtimestamp(start, UTC)
        params = urllib.parse.urlencode({
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "STARTDATETIME": _ts14(start_dt),
            "ENDDATETIME": _ts14(self.cutoff),
            "maxrecords": str(max_results),
            "sort": "DateDesc",  # deterministic within-window order, not today's relevance
        })
        body = ""
        for attempt in range(3):
            if attempt:
                time.sleep(6)  # GDELT allows one request per 5s
            body, _ = self._http(f"{GDELT_DOC}?{params}")
            if _GDELT_RATELIMIT not in body[:200]:
                break
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return {"query": query, "articles": [],
                    "response_valid": False,
                    "note": f"GDELT unavailable ({body.strip()[:120]!r}); "
                            "try fetch_page or wikipedia_asof instead"}
        raw_articles = data.get("articles") if isinstance(data, dict) else None
        if not isinstance(raw_articles, list):
            return {"query": query, "articles": [], "response_valid": False,
                    "note": "GDELT returned no valid article-list payload; "
                            "try fetch_page or wikipedia_asof instead"}
        articles = []
        for art in raw_articles:
            seen = str(art.get("seendate") or "")
            try:
                seen_dt = self._assert_pre_cutoff(seen, f"article {art.get('url')}")
            except (LeakError, ValueError):
                continue  # belt over the server-side window: silently drop strays
            articles.append({
                "title": art.get("title"),
                "url": art.get("url"),
                "domain": art.get("domain"),
                "published": seen_dt.strftime("%Y-%m-%d"),
                "language": art.get("language"),
            })
        return {"query": query,
                "window": f"{start_dt.date()} -> {self.cutoff.date()}",
                "response_valid": True,
                "articles": articles}

    def fetch_page(self, url: str, max_chars: int = 8000) -> dict:
        """The page as it existed at the last Wayback capture at or before the cutoff."""
        max_chars = max(500, min(int(max_chars), 30000))
        params = urllib.parse.urlencode({
            "url": url, "to": _ts14(self.cutoff), "limit": "-1", "output": "json",
            "filter": "statuscode:200", "matchType": "exact",
        })
        body, _ = self._http(f"{WAYBACK_CDX}?{params}")
        try:
            rows = json.loads(body or "[]")
        except json.JSONDecodeError:
            rows = []
        if len(rows) < 2:  # row 0 is the header
            return {"url": url, "archived_at": None,
                    "text": f"No archived version of {url} exists at or before "
                            f"{self.cutoff.date()} — this page is unavailable pre-cutoff."}
        stamp, original = str(rows[-1][1]), str(rows[-1][2])
        self._assert_pre_cutoff(stamp, f"CDX capture of {url}")
        if original != url:
            raise LeakError(
                "CDX did not preserve the exact requested historical URL; "
                f"refusing {original!r} for {url!r}"
            )
        snap_body, effective = self._http(f"{WAYBACK_SNAP}/{stamp}id_/{original}")
        # Wayback redirects resolve to the NEAREST capture, which can postdate the request
        # — or can escape replay entirely to the live origin.  The effective URL is the
        # authoritative provenance: it must still be the exact ``id_`` replay shape, not
        # a current Wayback calendar/timemap/toolbar page that merely contains a stamp.
        # Falling back to the requested stamp when the final URL had no stamp admitted
        # live bytes.
        parsed_effective = urllib.parse.urlparse(effective)
        match = _SNAP_REPLAY.fullmatch(parsed_effective.path)
        if (
            parsed_effective.scheme != "https"
            or parsed_effective.hostname != "web.archive.org"
            or match is None
        ):
            raise LeakError(
                "served snapshot escaped the exact stamped web.archive.org id_ replay; "
                f"refusing effective URL {effective!r}"
            )
        final_stamp = match.group(1)
        final_dt = self._assert_pre_cutoff(final_stamp, f"served snapshot of {url}")
        text = html_to_text(snap_body)
        truncated = len(text) > max_chars
        return {
            "url": original,
            "archived_at": final_dt.isoformat(),
            "truncated": truncated,
            "text": text[:max_chars],
        }

    def wikipedia_asof(self, title: str, max_chars: int = 8000) -> dict:
        """The exact Wikipedia title URL's last archived pre-cutoff snapshot.

        MediaWiki's revision API is intentionally not used: ``titles=`` is resolved
        against today's title->page mapping before ``rvstart`` filters revisions, so a
        post-cutoff move can reveal a future title while returning old content.
        """
        clean_title = str(title).strip()
        if not clean_title:
            raise ValueError("Wikipedia title must not be blank")
        encoded_title = urllib.parse.quote(
            clean_title.replace(" ", "_"), safe="()_-/"
        )
        page = self.fetch_page(
            f"https://en.wikipedia.org/wiki/{encoded_title}",
            max_chars=max_chars,
        )
        return {
            "title": clean_title,
            "archived_at": page.get("archived_at"),
            "truncated": page.get("truncated", False),
            "text": page.get("text", ""),
            "retrieval": "wayback_exact_title",
        }

    # -- BTF-2 corpus (optional; the SOTA teacher's frozen scrape manifest) -------------
    def _corpus(self) -> sqlite3.Connection:
        """Open (once, read-only) the corpus index, or fail with a clear message."""
        if not self.corpus_db:
            raise RuntimeError(
                "no corpus configured — search_corpus / fetch_corpus_page need a corpus "
                "db; build it with bench/build_corpus_index.py and pass its path as "
                "corpus_db (or --corpus to the MCP server)"
            )
        if self._corpus_conn is None:
            if not Path(self.corpus_db).exists():
                raise RuntimeError(f"corpus db not found: {self.corpus_db}")
            con = sqlite3.connect(f"file:{self.corpus_db}?mode=ro", uri=True)
            with contextlib.suppress(sqlite3.Error):
                meta = dict(con.execute("SELECT key, value FROM corpus_meta").fetchall())
                self._corpus_caveat = meta.get("caveat")
            self._corpus_conn = con
        return self._corpus_conn

    @property
    def _caveat(self) -> str:
        return self._corpus_caveat or self.CORPUS_CAVEAT_FALLBACK

    @staticmethod
    def _fts_match(query: str) -> str:
        """User text -> a safe FTS5 MATCH string: alnum tokens, each phrase-quoted (so
        reserved words / punctuation can't inject operators), OR-joined for recall over
        short URL-derived docs (bm25 still ranks multi-term, rarer-term hits first)."""
        toks = [t for t in re.split(r"[^0-9A-Za-z]+", str(query).lower()) if len(t) > 1]
        return " OR ".join(f'"{t}"' for t in toks)

    def _corpus_date_gate(self, raw: str, what: str) -> tuple[bool, str | None]:
        """Same choke point as every other source. -> (keep?, YYYY-MM-DD or None).

        Post-cutoff and unparseable/empty scrape dates are both dropped fail-closed. The
        agent cannot opt an undated row back into a time-locked result set."""
        try:
            dt = self._assert_pre_cutoff(str(raw or ""), what)
            return True, dt.strftime("%Y-%m-%d")
        except LeakError:
            return False, None
        except ValueError:
            return False, None

    def search_corpus(self, query: str, limit: int = 8) -> list[dict]:
        """Keyword-search the teacher's scrape manifest. Returns discovery metadata only —
        {url, title, date, snippet} — never page content (route urls through fetch_page).
        Every returned url was crawled on or before the as-of instant; the caveat field
        states the scrape-window skew and that the text is URL-derived, not page body."""
        con = self._corpus()
        limit = max(1, min(int(limit), 25))
        match = self._fts_match(query)
        if not match:
            return []
        over = max(limit * 8, 40)  # over-fetch: the cutoff gate drops post-scrape hits
        try:
            rows = con.execute(
                "SELECT p.url, p.title, p.date_scraped, "
                "snippet(pages_fts, 1, '[', ']', ' … ', 12) "
                "FROM pages_fts JOIN pages p ON p.rowid = pages_fts.rowid "
                "WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, over),
            ).fetchall()
        except sqlite3.OperationalError:  # FTS4 fallback build: no external content / bm25
            rows = con.execute(
                "SELECT url, title, date_scraped, text FROM pages_fts "
                "WHERE pages_fts MATCH ? LIMIT ?", (match, over),
            ).fetchall()
        out: list[dict] = []
        for url, title, date_scraped, snip in rows:
            keep, date = self._corpus_date_gate(date_scraped, f"corpus hit {url}")
            if not keep:
                continue
            out.append({"url": url, "title": title, "date": date,
                        "snippet": (snip or "")[:400], "caveat": self._caveat})
            if len(out) >= limit:
                break
        return out

    def fetch_corpus_page(self, url: str, max_chars: int = 8000) -> dict:
        """The stored manifest record for a url. NOTE: the dataset ships no page body, so
        'text' is URL-derived tokens, not article content — fetch_page (time-locked
        Wayback) is the path to the real pre-cutoff content. Respects the cutoff: a url
        scraped after the as-of instant raises LeakError."""
        con = self._corpus()
        max_chars = max(500, min(int(max_chars), 30000))  # match the other fetchers' cap
        row = con.execute(
            "SELECT title, date_scraped, question_id, text FROM pages WHERE url = ?",
            (url,),
        ).fetchone()
        if row is None:
            return {"url": url, "found": False, "date": None,
                    "text": "This URL is not in the BTF-2 scrape manifest.",
                    "caveat": self._caveat}
        title, date_scraped, qid, text = row
        try:
            date = self._assert_pre_cutoff(str(date_scraped or ""),
                                           f"corpus page {url}").strftime("%Y-%m-%d")
        except ValueError:
            raise LeakError(
                f"corpus page {url} has no parseable scrape date; excluded fail-closed"
            ) from None
        text = text or ""
        truncated = len(text) > max_chars
        return {"url": url, "found": True, "title": title, "date": date,
                "question_id": qid, "truncated": truncated,
                "text": text[:max_chars], "caveat": self._caveat}


def main(argv: list[str] | None = None) -> int:
    """Manual smoke: python bench/timevault.py --cutoff 2025-10-23 <tool> <arg>."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--corpus", default=None,
                        help="path to btf2_corpus.sqlite (for search_corpus/fetch_corpus_page)")
    parser.add_argument("tool", choices=("search_news", "fetch_page", "wikipedia_asof",
                                         "search_corpus", "fetch_corpus_page"))
    parser.add_argument("arg")
    args = parser.parse_args(argv)
    vault = TimeVault(parse_cutoff(args.cutoff), corpus_db=args.corpus)
    result = getattr(vault, args.tool)(args.arg)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
