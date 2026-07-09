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
- **Wikipedia** (``wikipedia_asof``): the revision API returns the article exactly as it
  stood at the cutoff (``rvstart=cutoff, rvdir=older``); the revision timestamp is
  verified. Strong guarantee; note the title *search* fallback ranks by today's index.
- **GDELT DOC 2.0** (``search_news``): full-text news discovery inside a window that ends
  at the cutoff (server-side ``ENDDATETIME``), date-sorted (not relevance-sorted, to avoid
  ranking informed by later importance); each hit's ``seendate`` is re-checked client-side.
  Discovery only — for article CONTENT route the URL through ``fetch_page`` so the text
  comes from a pre-cutoff snapshot, not today's live page.

Residual leak surfaces, documented not hidden: the model's own weights (choose questions
that RESOLVE after the model's training cutoff — a set-selection duty, not a tool duty),
and Wikipedia title-search ranking. There is no live-web path in this module at all.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser

USER_AGENT = "forecast-scaffold-timevault/0.1 (leak-free pastcast research)"
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_SNAP = "https://web.archive.org/web"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
WIKI_API = "https://en.wikipedia.org/w/api.php"

_SNAP_TS = re.compile(r"/web/(\d{14})")
_GDELT_RATELIMIT = "limit requests"


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

    def __init__(self, cutoff: datetime | str, timeout: int = 45) -> None:
        self.cutoff = cutoff if isinstance(cutoff, datetime) else parse_cutoff(cutoff)
        if self.cutoff.tzinfo is None:
            self.cutoff = self.cutoff.replace(tzinfo=UTC)
        self.timeout = timeout

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
        never auto-decompresses)."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                with contextlib.suppress(OSError, EOFError):
                    raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace"), str(resp.geturl())

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
                    "note": f"GDELT unavailable ({body.strip()[:120]!r}); "
                            "try fetch_page or wikipedia_asof instead"}
        articles = []
        for art in data.get("articles") or []:
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
                "articles": articles}

    def fetch_page(self, url: str, max_chars: int = 8000) -> dict:
        """The page as it existed at the last Wayback capture at or before the cutoff."""
        max_chars = max(500, min(int(max_chars), 30000))
        params = urllib.parse.urlencode({
            "url": url, "to": _ts14(self.cutoff), "limit": "-1", "output": "json",
            "filter": "statuscode:200",
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
        snap_body, effective = self._http(f"{WAYBACK_SNAP}/{stamp}id_/{original}")
        # Wayback redirects resolve to the NEAREST capture, which can postdate the request
        # — the effective URL's embedded stamp is the authoritative one. Verify it.
        match = _SNAP_TS.search(effective)
        final_stamp = match.group(1) if match else stamp
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
        """The Wikipedia article exactly as it stood at the cutoff (revision history)."""
        max_chars = max(500, min(int(max_chars), 30000))
        params = urllib.parse.urlencode({
            "action": "query", "prop": "revisions", "titles": title, "redirects": "1",
            "rvlimit": "1", "rvdir": "older", "rvslots": "main",
            "rvstart": self.cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rvprop": "timestamp|ids|content",
            "format": "json", "formatversion": "2",
        })
        body, _ = self._http(f"{WIKI_API}?{params}")
        data = json.loads(body)
        pages = (data.get("query") or {}).get("pages") or []
        if not pages or pages[0].get("missing") or not pages[0].get("revisions"):
            suggest_params = urllib.parse.urlencode({
                "action": "opensearch", "search": title, "limit": "5", "format": "json",
            })
            sbody, _ = self._http(f"{WIKI_API}?{suggest_params}")
            try:
                suggestions = json.loads(sbody)[1]
            except (json.JSONDecodeError, IndexError, TypeError):
                suggestions = []
            return {"title": title, "revision_at": None,
                    "text": f"No article (or no pre-cutoff revision) for {title!r}.",
                    "did_you_mean": suggestions}
        page = pages[0]
        rev = page["revisions"][0]
        rev_dt = self._assert_pre_cutoff(str(rev["timestamp"]), f"revision of {title!r}")
        content = str(((rev.get("slots") or {}).get("main") or {}).get("content") or "")
        truncated = len(content) > max_chars
        return {
            "title": page.get("title", title),
            "revision_at": rev_dt.isoformat(),
            "truncated": truncated,
            "text": content[:max_chars],
        }


def main(argv: list[str] | None = None) -> int:
    """Manual smoke: python bench/timevault.py --cutoff 2025-10-23 <tool> <arg>."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("tool", choices=("search_news", "fetch_page", "wikipedia_asof"))
    parser.add_argument("arg")
    args = parser.parse_args(argv)
    vault = TimeVault(parse_cutoff(args.cutoff))
    result = getattr(vault, args.tool)(args.arg)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
