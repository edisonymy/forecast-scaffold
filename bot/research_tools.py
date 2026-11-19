"""Stdio MCP server: raw page reads and dated news headlines for the research agent.

The CLI's WebFetch hands every page to a helper model and returns a summary, so exact
figures, tables and data files never reach the forecaster. ``fetch_raw`` returns the text
itself; ``news_search`` lists recent dated headlines (GDELT DOC 2.0).

Safety (the agent reads untrusted pages and may be prompt-injected): http(s) only, no URL
credentials, no cookies; every resolved address must be public and the connection goes to
that vetted IP (TLS still verifies the hostname); redirects are followed by hand and
re-checked on every hop. The domain deny-list is a LAUNCH argument, never a tool parameter.

Protocol: newline-delimited JSON-RPC 2.0, as bench/timevault_mcp.py. Tool failures return
result.isError=true, never a protocol error.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import time
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, urlencode, urljoin, urlsplit

DEFAULT_UA = "Mozilla/5.0 (compatible; research-fetch/1.0)"
TIMEOUT, MAX_BYTES, MAX_REDIRECTS, HARD_CAP = 15.0, 3 * 1024 * 1024, 5, 60_000
WINDOW, MAX_MATCHES = 400, 8
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_SPACING, GDELT_BUDGET = 5.5, 25.0  # GDELT allows one request per 5 s per IP
GDELT_LANGS = {"en": "english", "es": "spanish", "fr": "french", "de": "german",
               "it": "italian", "pt": "portuguese", "ru": "russian", "zh": "chinese",
               "ja": "japanese", "ar": "arabic"}
GDELT_LOCK = Path(tempfile.gettempdir()) / "research_tools_gdelt.lock"
PROTOCOL_FALLBACK = "2024-11-05"
TEXT_TYPES = {"application/json", "application/xml", "application/javascript",
              "application/x-javascript", "application/csv", "application/x-ndjson"}
SAFE_PATH = "/%:@!$&'()*+,;=-._~"

Resolver = Callable[[str, int], list[str]]
# (scheme, vetted_ip, port, hostname, request_target, headers) -> (status, headers, body)
Sender = Callable[[str, str, int, str, str, dict[str, str]], tuple[int, dict[str, str], bytes]]


class ToolError(Exception):
    """A tool-level failure: reported to the agent as isError, never as a crash."""


class _PinnedHTTPS(http.client.HTTPConnection):
    """HTTPS to an already-vetted IP, verifying the certificate against the hostname."""

    default_port = 443

    def __init__(self, ip: str, port: int, hostname: str) -> None:
        super().__init__(ip, port, timeout=TIMEOUT)
        self._hostname = hostname

    def connect(self) -> None:
        raw = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self._hostname)


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]).split("%")[0] for info in infos))


def _send(scheme: str, ip: str, port: int, host: str, target: str,
          headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    conn = (_PinnedHTTPS(ip, port, host) if scheme == "https"
            else http.client.HTTPConnection(ip, port, timeout=TIMEOUT))
    try:
        conn.request("GET", target, headers=headers)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read(MAX_BYTES + 1)
    finally:
        conn.close()


class Net(NamedTuple):
    """Everything that touches the outside world, injectable for tests."""

    resolve: Resolver = _resolve
    send: Sender = _send
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    user_agent: str = DEFAULT_UA
    gdelt_lock: Path | None = GDELT_LOCK


DEFAULT_NET = Net()


class Response(NamedTuple):
    url: str
    status: int
    ctype: str
    charset: str
    body: bytes
    clipped: bool


def host_denied(host: str, deny: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in deny)


def check_address(addr: str) -> None:
    """Refuse any address that is not plainly public (loopback, private, metadata...)."""
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(addr)
    except ValueError as exc:
        raise ToolError(f"refused: unparseable address {addr!r}") from exc
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or not ip.is_global):
        raise ToolError(f"refused: {addr} is not a public address")


def check_url(url: str, deny: tuple[str, ...]) -> tuple[str, str, int, str]:
    """Validate one hop's URL; return (scheme, host, port, request target)."""
    try:
        parts = urlsplit(str(url).strip())
        port = parts.port
    except ValueError as exc:
        raise ToolError("refused: malformed URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ToolError(f"refused: only http and https URLs are allowed, not {scheme or '?'!r}")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ToolError("refused: credentials in URLs are not allowed")
    try:
        host = (parts.hostname or "").encode("idna").decode("ascii").rstrip(".")
    except UnicodeError as exc:
        raise ToolError("refused: invalid hostname") from exc
    if not host:
        raise ToolError("refused: URL has no host")
    if host_denied(host, deny):
        raise ToolError(f"denied: {host} is on this run's deny-list")
    target = quote(parts.path or "/", safe=SAFE_PATH)
    if parts.query:
        target += "?" + quote(parts.query, safe=SAFE_PATH + "?")
    return scheme, host, port or (443 if scheme == "https" else 80), target


def safe_get(url: str, deny: tuple[str, ...], net: Net) -> Response:
    """GET with every safety check applied to each redirect hop."""
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, target = check_url(url, deny)
        try:
            addrs = net.resolve(host, port)
        except OSError as exc:
            raise ToolError(f"DNS lookup failed for {host}") from exc
        if not addrs:
            raise ToolError(f"DNS lookup returned nothing for {host}")
        for addr in addrs:
            check_address(addr)
        bracketed = f"[{host}]" if ":" in host else host
        headers = {"Host": bracketed if port in (80, 443) else f"{bracketed}:{port}",
                   "User-Agent": net.user_agent, "Accept": "*/*",
                   "Accept-Encoding": "identity", "Connection": "close"}
        try:
            status, hdrs, body = net.send(scheme, addrs[0], port, host, target, headers)
        except (OSError, http.client.HTTPException) as exc:
            raise ToolError(f"request to {host} failed: {type(exc).__name__}") from exc
        location = hdrs.get("location")
        if status in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        raw_type = hdrs.get("content-type", "")
        m = re.search(r"charset=\"?([\w.:-]+)", raw_type, re.I)
        return Response(url, status, raw_type.split(";")[0].strip().lower(),
                        m.group(1) if m else "", body[:MAX_BYTES], len(body) > MAX_BYTES)
    raise ToolError(f"refused: more than {MAX_REDIRECTS} redirects")


def decode(body: bytes, charset: str) -> str:
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def is_text_type(ctype: str, body: bytes) -> bool:
    if not ctype:
        return b"\x00" not in body[:1024]
    return (ctype.startswith("text/") or ctype in TEXT_TYPES
            or ctype.endswith(("+json", "+xml")))


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template"}
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "header", "footer", "ul", "ol", "table", "blockquote", "pre", "hr", "dt", "dd",
             "form", "nav", "main", "aside", "figure", "figcaption", "caption"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title: list[str] = []
        self.skip: dict[str, int] = {}
        self.in_title, self.title_done, self.cell, self.in_cell = False, False, 0, False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and not self.title_done and not set(self.skip) - {"head"}:
            self.in_title = True
        elif tag == "body":
            self.skip.pop("head", None)
        elif tag in self.SKIP:
            self.skip[tag] = self.skip.get(tag, 0) + 1
        elif self.skip:
            return
        elif tag == "tr":
            self.out.append("\n")
            self.cell, self.in_cell = 0, False
        elif tag in ("td", "th"):
            self.out.append("\t" if self.cell else "")
            self.cell, self.in_cell = self.cell + 1, True
        elif tag in self.BLOCK or tag == "li":  # inside a cell, keep the row on one line
            self.out.append(" " if self.in_cell else "\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self.in_title:
            self.in_title, self.title_done = False, True
        elif tag in self.skip:
            self.skip[tag] -= 1
            if self.skip[tag] <= 0:
                del self.skip[tag]
        elif tag in ("td", "th"):
            self.in_cell = False
        elif not self.skip and tag in self.BLOCK:
            self.out.append(" " if self.in_cell else "\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        elif not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))


def html_to_text(markup: str) -> str:
    """Visible text; <title> as the first line; table cells tab-separated, rows per line."""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    lines = [re.sub(r" *\t *", "\t", re.sub(r" {2,}", " ", line)).strip(" ")
             for line in "".join(parser.out).split("\n")]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    title = " ".join("".join(parser.title).split())
    return f"{title}\n\n{body}" if title else body


def find_windows(text: str, needle: str) -> str:
    """Windows of ~WINDOW chars around the first MAX_MATCHES matches, merged on overlap."""
    hits = list(re.finditer(re.escape(needle), text, re.I))
    spans: list[list[int]] = []
    for m in hits[:MAX_MATCHES]:
        start, end = max(0, m.start() - WINDOW), min(len(text), m.end() + WINDOW)
        if spans and start <= spans[-1][1]:
            spans[-1][1] = end
        else:
            spans.append([start, end])
    if not hits:
        return f"[find {needle!r}: no match]"
    head = f"[find {needle!r}: {len(hits)} match(es), windows around the first {MAX_MATCHES}]"
    return "\n".join([head] + [f"\n[@{s}] {text[s:e]}" for s, e in spans])


def fetch_raw(url: str, find: str = "", max_chars: int = 20000, *,
              deny: tuple[str, ...] = (), net: Net = DEFAULT_NET) -> str:
    limit = max(1, min(int(max_chars), HARD_CAP))
    resp = safe_get(url, deny, net)
    if not is_text_type(resp.ctype, resp.body):
        raise ToolError(f"refused: content type {resp.ctype or 'unknown'} is not text "
                        "(images, video, audio, archives and PDF are not supported)")
    text = decode(resp.body, resp.charset)
    if resp.ctype in ("text/html", "application/xhtml+xml"):
        text = html_to_text(text)
    header = (f"{resp.url} | HTTP {resp.status} | {resp.ctype or 'unknown'} | "
              f"full text {len(text)} chars"
              + (" (download stopped at 3 MB)" if resp.clipped else ""))
    body = find_windows(text, str(find)) if find else text
    if len(body) > limit:
        body = (body[:limit] + f"\n[truncated: showing {limit} of {len(body)} chars; "
                "raise max_chars (cap 60000) or use find]")
    if resp.status >= 400:
        raise ToolError(f"{header}\n{body[:1500]}")
    return f"{header}\n{body}"


class Headline(NamedTuple):
    date: str
    source: str
    title: str
    url: str


def _clean(value: object) -> str:
    return " ".join(str(value).replace("|", "/").split())


def parse_gdelt(raw: str) -> list[Headline]:
    """GDELT artlist JSON -> headlines. Raises ToolError carrying any non-JSON message."""
    try:
        data = json.loads(raw, strict=False)
    except ValueError as exc:
        raise ToolError(f"GDELT said: {_clean(raw)[:200] or 'empty response'}") from exc
    articles = data.get("articles", []) if isinstance(data, dict) else None
    if not isinstance(articles, list):
        raise ToolError("GDELT returned an unexpected JSON shape")
    out = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        url, title = art.get("url"), art.get("title")
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))
                and isinstance(title, str) and title.strip()):
            continue
        m = re.match(r"(\d{4})(\d{2})(\d{2})", str(art.get("seendate", "")))
        source = art.get("domain") or urlsplit(url).hostname or "unknown"
        out.append(Headline("-".join(m.groups()) if m else "unknown", _clean(source),
                            _clean(title), url.strip()))
    return out


def format_headlines(items: list[Headline], limit: int, deny: tuple[str, ...]) -> list[str]:
    """Newest first, deduped by title, denied domains dropped."""
    seen: set[str] = set()
    lines: list[str] = []
    for h in sorted(items, key=lambda h: (h.date != "unknown", h.date), reverse=True):
        key = h.title.casefold()
        if key in seen or host_denied(urlsplit(h.url).hostname or "", deny):
            continue
        seen.add(key)
        lines.append(f"{h.date} | {h.source} | {h.title} | {h.url}")
        if len(lines) >= limit:
            break
    return lines


def _gdelt_slot(net: Net, deadline: float) -> None:
    """Cross-process spacing: the bot runs several copies of this server at once."""
    if net.gdelt_lock is None or os.name != "posix":
        return
    import fcntl
    with open(net.gdelt_lock, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            try:
                last = float(fh.read().strip() or 0)
            except ValueError:
                last = 0.0
            wait = last + GDELT_SPACING - net.clock()
            if net.clock() + max(wait, 0) > deadline:
                raise ToolError("GDELT is rate-limited right now; use WebSearch instead")
            if wait > 0:
                net.sleep(wait)
            fh.seek(0)
            fh.truncate()
            fh.write(repr(net.clock()))
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def gdelt_fetch(query: str, days: int, records: int, net: Net) -> list[Headline]:
    params = {"query": query, "mode": "artlist", "format": "json",
              "maxrecords": str(records), "timespan": f"{days}d", "sort": "datedesc"}
    url = f"{GDELT_URL}?{urlencode(params, quote_via=quote)}"
    deadline = net.clock() + GDELT_BUDGET
    for attempt in range(2):
        _gdelt_slot(net, deadline)
        resp = safe_get(url, (), net)
        if resp.status == 429 and attempt == 0 and net.clock() + 6 < deadline:
            net.sleep(6)
            continue
        if resp.status != 200:
            raise ToolError(f"GDELT returned HTTP {resp.status}")
        return parse_gdelt(decode(resp.body, resp.charset))
    raise ToolError("GDELT returned HTTP 429 twice")


def news_search(query: str, days: int = 14, max_results: int = 20, lang: str = "en", *,
                deny: tuple[str, ...] = (), net: Net = DEFAULT_NET) -> str:
    query = " ".join(str(query).split())
    if not query:
        raise ToolError("empty query")
    days, limit = max(1, min(int(days), 90)), max(1, min(int(max_results), 50))
    language = GDELT_LANGS.get(str(lang).lower())
    try:
        items = gdelt_fetch(f"{query} sourcelang:{language}" if language else query,
                            days, min(250, limit * 2), net)
    except ToolError as exc:
        raise ToolError(f"news_search unavailable ({exc}); use WebSearch instead") from exc
    lines = format_headlines(items, limit, deny)
    head = f"news_search via GDELT: {len(lines)} result(s) for {query!r}, last {days} days"
    return "\n".join([head] + lines)


TOOLS: dict[str, Callable[..., str]] = {"fetch_raw": fetch_raw, "news_search": news_search}
TOOL_DEFS: list[dict[str, Any]] = [
    {"name": "fetch_raw",
     "description": (
         "GET a public http(s) URL and return its text UNSUMMARISED: JSON, CSV, XML and "
         "plain text verbatim; HTML reduced to visible text with the title first and table "
         "cells tab-separated. First line: final URL, HTTP status, content type, full length. "
         "Prefer it over WebFetch when you need exact figures, tables or data files: official "
         "statistics pages, CSV/JSON downloads, long tables. Use `find` to pull one number "
         "out of a long page (returns only text around each match, with offsets). Example "
         "data endpoint: FRED CSV https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>. "
         "Text only (no PDF/images)."),
     "inputSchema": {"type": "object", "required": ["url"], "properties": {
         "url": {"type": "string"},
         "find": {"type": "string",
                  "description": "case-insensitive substring; return only ~800-char windows "
                                 "around up to 8 matches"},
         "max_chars": {"type": "integer", "description": "default 20000, max 60000"}}}},
    {"name": "news_search",
     "description": (
         "Recent worldwide news headlines (GDELT), newest first, one per line: "
         "`YYYY-MM-DD | source | title | url`, with direct article URLs to read via fetch_raw. "
         "Use it to check what happened lately and when; dates are when GDELT saw the story. "
         "Headlines only, no article text. Use short keyword queries; quote exact phrases."),
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"},
         "days": {"type": "integer", "description": "look-back window, default 14, max 90"},
         "max_results": {"type": "integer", "description": "default 20, max 50"},
         "lang": {"type": "string", "description": "source language: en (default), es, fr, "
                  "de, it, pt, ru, zh, ja, ar, or 'any'"}}}},
]
ALLOWED_ARGS = {d["name"]: set(d["inputSchema"]["properties"]) for d in TOOL_DEFS}


class Server(NamedTuple):
    deny: tuple[str, ...] = ()
    log: Path | None = None
    net: Net = DEFAULT_NET


def _log(server: Server, name: str, args: dict[str, Any], ok: bool, chars: int) -> None:
    if server.log is None:
        return
    event: dict[str, Any] = {"tool": name, "ok": ok, "chars": chars}
    if name == "fetch_raw":
        try:
            event["host"] = urlsplit(str(args.get("url", ""))).hostname or ""
        except ValueError:
            event["host"] = ""
    else:
        event["query"] = str(args.get("query", ""))[:300]
    with contextlib.suppress(OSError), server.log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def call_tool(name: str, args: dict[str, Any], server: Server) -> tuple[str, bool]:
    unknown = set(args) - ALLOWED_ARGS[name]
    try:
        if unknown:
            raise ToolError(f"unknown argument(s): {', '.join(sorted(unknown))}")
        text, ok = TOOLS[name](**args, deny=server.deny, net=server.net), True
    except ToolError as exc:
        text, ok = str(exc), False
    except (TypeError, ValueError) as exc:
        text, ok = f"invalid arguments: {str(exc)[:200]}", False
    except Exception as exc:  # noqa: BLE001 - any fault is a tool-level error
        text, ok = f"[{type(exc).__name__}] {str(exc)[:300]}", False
    _log(server, name, args, ok, len(text))
    return text, ok


def handle_message(msg: dict[str, Any], server: Server) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method, msg_id = msg.get("method"), msg.get("id")
    if msg_id is None:
        return None

    def ok(result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def rpc_error(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        proto = str((msg.get("params") or {}).get("protocolVersion") or PROTOCOL_FALLBACK)
        return ok({"protocolVersion": proto, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "research-tools", "version": "1.0.0"}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOL_DEFS})
    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return rpc_error(-32602, "tools/call params must be an object")
        name, args = str(params.get("name") or ""), params.get("arguments") or {}
        if name not in TOOLS:
            return rpc_error(-32602, f"unknown tool {name!r}")
        if not isinstance(args, dict):
            return rpc_error(-32602, "tool arguments must be an object")
        text, success = call_tool(name, args, server)
        return ok({"content": [{"type": "text", "text": text}], "isError": not success})
    return rpc_error(-32601, f"method {method!r} not implemented")


def selftest(server: Server) -> int:
    """One live call per backend and named endpoint; only the plain-page fetch is fatal."""
    checks: list[tuple[str, Callable[[], str], str, bool]] = [
        ("fetch_raw plain page", lambda: fetch_raw("https://example.com/", net=server.net),
         "Example Domain", True),
        ("fetch_raw FRED CSV", lambda: fetch_raw(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE", max_chars=300,
            net=server.net), "UNRATE", False),
        ("news_search GDELT", lambda: news_search("economy", days=3, max_results=3,
                                                  net=server.net), " | ", False),
    ]
    fatal = False
    for label, run, expect, required in checks:
        try:
            out = run()
            good = expect in out
            print(f"{'OK' if good else 'FAIL'} {label}"
                  + ("" if good else f": expected {expect!r} in output"))
        except ToolError as exc:
            good = False
            print(f"FAIL {label}: {str(exc).splitlines()[0][:200]}")
        fatal |= required and not good
    return 1 if fatal else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deny-domains", default="",
                        help="comma-separated domains; subdomains are denied too")
    parser.add_argument("--log", default=None, help="append one JSON line per tool call")
    parser.add_argument("--user-agent", default=DEFAULT_UA,
                        help="User-Agent sent to every host (default is generic)")
    parser.add_argument("--selftest", action="store_true", help="live smoke test, then exit")
    args = parser.parse_args(argv)
    deny = tuple(d.strip().lower().removeprefix("*.").strip(".")
                 for d in args.deny_domains.split(",") if d.strip())
    server = Server(deny, Path(args.log) if args.log else None, Net(user_agent=args.user_agent))
    if args.selftest:
        return selftest(server)
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            msg = None
        if not isinstance(msg, dict):
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "parse error"}}), flush=True)
            continue
        response = handle_message(msg, server)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
