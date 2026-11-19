"""bot/research_tools.py: raw fetch, GDELT headlines and the SSRF guards. No network."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
import research_tools as rt  # noqa: E402

PUBLIC = "93.184.215.14"
Reply = tuple[int, dict[str, str], bytes]


class FakeNet:
    """Scripted resolver/sender plus a fake clock; records every request sent."""

    def __init__(self, routes: dict[str, Reply | list[Reply]],
                 dns: dict[str, list[str]] | None = None) -> None:
        self.routes = routes
        self.dns = dns or {}
        self.sent: list[tuple[str, str, int, str, str, dict[str, str]]] = []
        self.now = 1_000_000.0
        self.slept: list[float] = []

    def resolve(self, host: str, port: int) -> list[str]:
        return self.dns.get(host, [PUBLIC])

    def send(self, scheme: str, ip: str, port: int, host: str, target: str,
             headers: dict[str, str]) -> Reply:
        self.sent.append((scheme, ip, port, host, target, headers))
        reply = self.routes[f"{scheme}://{host}{target}".split("?")[0]]
        return reply.pop(0) if isinstance(reply, list) else reply

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def net(self, lock: Path | None = None, **kw: Any) -> rt.Net:
        return rt.Net(self.resolve, self.send, lambda: self.now, self.sleep,
                      gdelt_lock=lock, **kw)


def ok(body: str | bytes, ctype: str = "text/html; charset=utf-8") -> Reply:
    data = body.encode() if isinstance(body, str) else body
    return 200, {"content-type": ctype}, data


def redirect(location: str, status: int = 302) -> Reply:
    return status, {"location": location}, b""


PAGE = """<html><head><title> GDP  Report </title><style>p{color:red}</style>
<script>var secret = 1;</script></head><body><nav>Menu</nav>
<h1>Quarterly GDP</h1><p>Growth was <b>2.4%</b>.</p><p></p><p></p><p></p>
<table><tr><th>Quarter</th><th>Growth<br>(%)</th></tr>
<tr><td>Q1</td><td>1.9</td></tr><tr><td>Q2</td><td>2.4</td></tr></table>
<noscript>enable js</noscript><svg><title>icon</title><text>chart</text></svg>
<ul><li>one</li><li>two &amp; three</li></ul></body></html>"""


class TestHtmlToText:
    def test_title_first_scripts_dropped_table_readable(self) -> None:
        text = rt.html_to_text(PAGE)
        assert text.splitlines()[0] == "GDP Report"
        assert "Quarter\tGrowth (%)\nQ1\t1.9\nQ2\t2.4" in text
        assert "Growth was 2.4%." in text
        for gone in ("secret", "color:red", "enable js", "icon", "chart"):
            assert gone not in text
        assert "- two & three" in text
        assert "\n\n\n" not in text

    def test_page_without_title_or_closing_head(self) -> None:
        assert rt.html_to_text("<head><meta x><body><p>Hi</p>") == "Hi"


class TestFind:
    def test_windows_merge_and_carry_offsets(self) -> None:
        text = "a" * 1000 + "Target" + "b" * 100 + "TARGET" + "c" * 2000 + "target" + "d" * 50
        out = rt.find_windows(text, "target")
        assert "3 match(es)" in out
        assert out.count("[@") == 2  # first two merged, third separate
        assert "[@600]" in out and f"[@{1000 + 6 + 100 + 6 + 2000 - 400}]" in out

    def test_no_match_and_cap(self) -> None:
        assert "no match" in rt.find_windows("abc", "zzz")
        many = ("x" * 1000 + "hit") * 20
        out = rt.find_windows(many, "hit")
        assert "20 match(es)" in out and out.count("[@") == rt.MAX_MATCHES


class TestFetchRaw:
    def test_html_with_header_and_truncation_note(self) -> None:
        fake = FakeNet({"https://example.org/": ok(PAGE)})
        out = rt.fetch_raw("https://example.org/", max_chars=30, net=fake.net())
        first = out.splitlines()[0]
        assert first.startswith("https://example.org/ | HTTP 200 | text/html | full text ")
        assert "[truncated: showing 30 of " in out

    def test_json_verbatim_and_find(self) -> None:
        body = json.dumps({"series": [{"date": "2026-10", "value": 4.1}]})
        fake = FakeNet({"https://api.example.org/v1": ok(body, "application/json")})
        out = rt.fetch_raw("https://api.example.org/v1?id=X", net=fake.net())
        assert out.splitlines()[1] == body
        assert fake.sent[0][4] == "/v1?id=X"
        assert "4.1" in rt.fetch_raw("https://api.example.org/v1", find="VALUE",
                                     net=fake.net())

    def test_declared_charset_and_bad_charset(self) -> None:
        fake = FakeNet({"https://e.org/a": ok("café".encode("latin-1"),
                                               "text/plain; charset=iso-8859-1"),
                        "https://e.org/b": ok(b"ok\xff", "text/plain; charset=bogus")})
        assert rt.fetch_raw("https://e.org/a", net=fake.net()).endswith("café")
        assert rt.fetch_raw("https://e.org/b", net=fake.net()).endswith("ok�")

    @pytest.mark.parametrize("ctype", ["image/png", "application/pdf", "application/zip",
                                       "video/mp4", "application/octet-stream"])
    def test_non_text_refused(self, ctype: str) -> None:
        fake = FakeNet({"https://e.org/f": ok(b"\x89PNG\x00", ctype)})
        with pytest.raises(rt.ToolError, match="not text"):
            rt.fetch_raw("https://e.org/f", net=fake.net())

    def test_http_error_is_tool_error(self) -> None:
        fake = FakeNet({"https://e.org/x": (404, {"content-type": "text/plain"}, b"nope")})
        with pytest.raises(rt.ToolError, match="HTTP 404"):
            rt.fetch_raw("https://e.org/x", net=fake.net())

    def test_connects_to_vetted_ip_with_generic_headers(self) -> None:
        fake = FakeNet({"https://e.org/": ok("x", "text/plain")})
        rt.fetch_raw("https://e.org/", net=fake.net())
        scheme, ip, port, host, _, headers = fake.sent[0]
        assert (scheme, ip, port, host) == ("https", PUBLIC, 443, "e.org")
        assert headers["Host"] == "e.org"
        assert headers["User-Agent"] == "Mozilla/5.0 (compatible; research-fetch/1.0)"
        assert not {"Cookie", "Authorization", "Referer"} & set(headers)
        rt.fetch_raw("https://e.org/", net=fake.net(user_agent="bot/1 (ops@x.test)"))
        assert fake.sent[1][5]["User-Agent"] == "bot/1 (ops@x.test)"


class TestAddressAndUrlChecks:
    @pytest.mark.parametrize("addr", [
        "127.0.0.1", "127.8.9.10", "10.1.2.3", "172.16.0.1", "192.168.1.1",
        "169.254.169.254", "0.0.0.0", "100.64.0.1", "224.0.0.1", "240.0.0.1",
        "::1", "::", "fe80::1", "fc00::1", "ff02::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
        "not-an-ip",
    ])
    def test_non_public_refused(self, addr: str) -> None:
        with pytest.raises(rt.ToolError, match="refused"):
            rt.check_address(addr)

    @pytest.mark.parametrize("addr", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
    def test_public_allowed(self, addr: str) -> None:
        rt.check_address(addr)

    def test_host_resolving_to_any_private_address_refused(self) -> None:
        fake = FakeNet({}, dns={"sneaky.example": [PUBLIC, "10.0.0.5"]})
        with pytest.raises(rt.ToolError, match="10.0.0.5 is not a public"):
            rt.fetch_raw("https://sneaky.example/", net=fake.net())
        assert fake.sent == []

    @pytest.mark.parametrize("url", ["http://127.0.0.1:8080/v1", "http://[::1]/",
                                     "http://localhost/", "http://2130706433/"])
    def test_loopback_forms_refused(self, url: str) -> None:
        dns = {"127.0.0.1": ["127.0.0.1"], "::1": ["::1"], "localhost": ["127.0.0.1", "::1"],
               "2130706433": ["127.0.0.1"]}
        fake = FakeNet({}, dns=dns)
        with pytest.raises(rt.ToolError, match="refused"):
            rt.fetch_raw(url, net=fake.net())
        assert fake.sent == []

    def test_redirect_to_private_address_refused(self) -> None:
        fake = FakeNet({"https://e.org/r": redirect("http://metadata.internal/latest")},
                       dns={"metadata.internal": ["169.254.169.254"]})
        with pytest.raises(rt.ToolError, match="169.254.169.254"):
            rt.fetch_raw("https://e.org/r", net=fake.net())
        assert len(fake.sent) == 1

    def test_redirect_followed_and_rechecked(self) -> None:
        fake = FakeNet({"https://e.org/old": redirect("/new", 301),
                        "https://e.org/new": redirect("https://www.e.org/final"),
                        "https://www.e.org/final": ok("done", "text/plain")})
        out = rt.fetch_raw("https://e.org/old", net=fake.net())
        assert out.startswith("https://www.e.org/final | HTTP 200") and out.endswith("done")
        with pytest.raises(rt.ToolError, match="deny-list"):
            rt.fetch_raw("https://e.org/old", deny=("www.e.org",), net=fake.net())

    def test_too_many_redirects(self) -> None:
        fake = FakeNet({"https://e.org/loop": redirect("/loop")})
        with pytest.raises(rt.ToolError, match="redirects"):
            rt.fetch_raw("https://e.org/loop", net=fake.net())
        assert len(fake.sent) == rt.MAX_REDIRECTS + 1

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://e.org/x", "javascript:alert(1)",
                                     "gopher://e.org/", "e.org/no-scheme"])
    def test_scheme_refused(self, url: str) -> None:
        with pytest.raises(rt.ToolError, match="only http and https"):
            rt.check_url(url, ())

    @pytest.mark.parametrize("url", ["https://user:pw@e.org/", "https://user@e.org/",
                                     "https://e.org@evil.example/"])
    def test_credentials_refused(self, url: str) -> None:
        with pytest.raises(rt.ToolError, match="credentials"):
            rt.check_url(url, ())

    def test_deny_list_suffix_match(self) -> None:
        deny = ("polymarket.com", "metaculus.com")
        assert rt.host_denied("polymarket.com", deny)
        assert rt.host_denied("gamma-api.polymarket.com", deny)
        assert rt.host_denied("WWW.Metaculus.com.", deny)
        assert not rt.host_denied("notpolymarket.com", deny)
        assert not rt.host_denied("polymarket.com.evil.example", deny)
        with pytest.raises(rt.ToolError, match="denied"):
            rt.check_url("https://x.polymarket.com/markets", deny)


GDELT_JSON = json.dumps({"articles": [
    {"url": "https://news.example/a", "title": "Fed holds rates", "seendate": "20261117T101500Z",
     "domain": "news.example"},
    {"url": "https://wire.example/b", "title": "Fed HOLDS rates", "seendate": "20261116T090000Z",
     "domain": "wire.example"},
    {"url": "https://wire.example/c", "title": "Markets | rally\nagain",
     "seendate": "20261118T000000Z"},
    {"url": "https://polymarket.com/n", "title": "Odds shift", "seendate": "20261118T000000Z"},
    {"url": "javascript:x", "title": "bad url"}, {"title": "no url"}, "junk",
    {"url": "https://x.example/d", "title": "No date"},
]})


class TestGdeltParsing:
    def test_parse_and_format(self) -> None:
        items = rt.parse_gdelt(GDELT_JSON)
        assert len(items) == 5
        lines = rt.format_headlines(items, 10, ("polymarket.com",))
        assert lines == [
            "2026-11-18 | wire.example | Markets / rally again | https://wire.example/c",
            "2026-11-17 | news.example | Fed holds rates | https://news.example/a",
            "unknown | x.example | No date | https://x.example/d",
        ]
        assert len(rt.format_headlines(items, 1, ())) == 1

    @pytest.mark.parametrize("raw", ['{"articles": {}}', "[1, 2]", '{"articles": "x"}'])
    def test_unexpected_shapes(self, raw: str) -> None:
        with pytest.raises(rt.ToolError, match="unexpected"):
            rt.parse_gdelt(raw)

    def test_empty_object_means_no_results(self) -> None:
        assert rt.parse_gdelt("{}") == []

    def test_plain_text_message_passed_through(self) -> None:
        with pytest.raises(rt.ToolError, match="limit requests to one every 5 seconds"):
            rt.parse_gdelt("Please limit requests to one every 5 seconds.")


GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


class TestNewsSearch:
    def test_success_query_params_and_lang(self, tmp_path: Path) -> None:
        fake = FakeNet({GDELT: ok(GDELT_JSON, "application/json")})
        out = rt.news_search("fed rates", days=7, max_results=2, net=fake.net(tmp_path / "l"))
        lines = out.splitlines()
        assert lines[0].startswith("news_search via GDELT: 2 result(s)")
        assert len(lines) == 3
        target = fake.sent[0][4]
        for part in ("query=fed%20rates%20sourcelang%3Aenglish", "mode=artlist",
                     "format=json", "timespan=7d", "sort=datedesc", "maxrecords=4"):
            assert part in target
        rt.news_search("fed", lang="any", net=fake.net(tmp_path / "l"))
        assert "sourcelang" not in fake.sent[1][4]

    def test_cross_process_spacing(self, tmp_path: Path) -> None:
        lock = tmp_path / "gdelt.lock"
        fake = FakeNet({GDELT: ok("{}", "application/json")})
        rt.news_search("a", net=fake.net(lock))
        assert fake.slept == []
        fake.now += 2.0
        rt.news_search("b", net=fake.net(lock))
        assert fake.slept == [pytest.approx(3.5)]
        assert float(lock.read_text()) == pytest.approx(fake.now)

    def test_429_waits_and_retries_once(self, tmp_path: Path) -> None:
        busy: Reply = (429, {"content-type": "text/plain"}, b"Please limit requests")
        fake = FakeNet({GDELT: [busy, ok(GDELT_JSON, "application/json")]})
        out = rt.news_search("fed", net=fake.net(tmp_path / "l"))
        assert "result(s)" in out and 6 in fake.slept

        fake = FakeNet({GDELT: [busy, busy]})
        with pytest.raises(rt.ToolError, match="use WebSearch instead"):
            rt.news_search("fed", net=fake.net(tmp_path / "l2"))
        assert len(fake.sent) == 2 and sum(fake.slept) < rt.GDELT_BUDGET

    def test_200_with_text_body_is_error(self, tmp_path: Path) -> None:
        fake = FakeNet({GDELT: ok("Invalid query: too short", "text/html")})
        with pytest.raises(rt.ToolError, match="Invalid query.*use WebSearch"):
            rt.news_search("x", net=fake.net(tmp_path / "l"))

    def test_empty_query(self) -> None:
        with pytest.raises(rt.ToolError, match="empty"):
            rt.news_search("   ")


class TestJsonRpc:
    def server(self, fake: FakeNet, log: Path | None = None) -> rt.Server:
        return rt.Server(("polymarket.com",), log, fake.net())

    def test_handshake(self) -> None:
        srv = self.server(FakeNet({}))
        init = rt.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18"}}, srv)
        assert init is not None
        assert init["result"]["protocolVersion"] == "2025-06-18"
        assert init["result"]["capabilities"] == {"tools": {}}
        assert rt.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                 srv) is None
        assert rt.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"},
                                 srv) == {"jsonrpc": "2.0", "id": 2, "result": {}}
        tools = rt.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, srv)
        assert tools is not None
        assert [t["name"] for t in tools["result"]["tools"]] == ["fetch_raw", "news_search"]
        bad = rt.handle_message({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}, srv)
        assert bad is not None and bad["error"]["code"] == -32601

    def test_descriptions_name_only_fred(self) -> None:
        text = json.dumps(rt.TOOL_DEFS).lower()
        assert "fredgraph.csv" in text
        for banned in ("wikipedia", "yahoo", "google"):
            assert banned not in text

    def call(self, srv: rt.Server, name: str, args: object) -> dict[str, Any]:
        resp = rt.handle_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                  "params": {"name": name, "arguments": args}}, srv)
        assert resp is not None
        return resp

    def test_tools_call_round_trip_and_errors(self) -> None:
        srv = self.server(FakeNet({"https://e.org/": ok("hello", "text/plain")}))
        res = self.call(srv, "fetch_raw", {"url": "https://e.org/"})["result"]
        assert res["isError"] is False and res["content"][0]["text"].endswith("hello")
        res = self.call(srv, "fetch_raw", {"url": "https://polymarket.com/"})["result"]
        assert res["isError"] is True and res["content"][0]["text"].startswith("denied")
        res = self.call(srv, "fetch_raw", {"url": "https://e.org/", "deny": []})["result"]
        assert res["isError"] is True and "unknown argument" in res["content"][0]["text"]
        res = self.call(srv, "fetch_raw", {"url": "https://e.org/", "max_chars": "lots"})
        assert res["result"]["isError"] is True
        assert self.call(srv, "fetch_raw", {})["result"]["isError"] is True
        assert self.call(srv, "nope", {})["error"]["code"] == -32602
        assert self.call(srv, "fetch_raw", [1])["error"]["code"] == -32602

    def test_telemetry_allow_list(self, tmp_path: Path) -> None:
        log = tmp_path / "calls.jsonl"
        fake = FakeNet({"https://e.org/p": ok("SECRET BODY", "text/plain"),
                        GDELT: ok(GDELT_JSON, "application/json")})
        srv = rt.Server((), log, rt.Net(fake.resolve, fake.send, lambda: fake.now,
                                        fake.sleep, gdelt_lock=None))
        self.call(srv, "fetch_raw", {"url": "https://e.org/p?token=abc", "find": "SECRET"})
        self.call(srv, "news_search", {"query": "fed rates"})
        self.call(srv, "fetch_raw", {"url": "file:///x"})
        events = [json.loads(line) for line in log.read_text().splitlines()]
        assert [set(e) for e in events] == [{"tool", "ok", "chars", "host"},
                                            {"tool", "ok", "chars", "query"},
                                            {"tool", "ok", "chars", "host"}]
        assert events[0]["host"] == "e.org" and events[0]["ok"] is True
        assert events[1]["query"] == "fed rates" and events[2]["ok"] is False
        assert "SECRET" not in log.read_text() and "token" not in log.read_text()


def test_stdio_loop() -> None:
    script = Path(__file__).resolve().parents[1] / "bot" / "research_tools.py"
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"}]
    stdin = "\n".join(json.dumps(m) for m in lines) + "\nnot json\n"
    proc = subprocess.run([sys.executable, str(script), "--deny-domains", "a.com"],
                          input=stdin, capture_output=True, text=True, timeout=30)
    out = [json.loads(line) for line in proc.stdout.splitlines()]
    assert out[0]["result"]["serverInfo"]["name"] == "research-tools"
    assert out[1]["error"]["code"] == -32700 and len(out) == 2


def test_default_sender_speaks_http_to_the_given_ip() -> None:
    """The real transport (bypassing the address check, which would refuse loopback)."""
    seen: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            seen.update(self.headers.items())
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"x" * 100)

        def log_message(self, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        status, headers, body = rt._send("http", "127.0.0.1", port, "e.org", "/p",
                                         {"Host": "e.org", "User-Agent": rt.DEFAULT_UA})
    finally:
        httpd.shutdown()
    assert (status, headers["content-type"], body) == (200, "text/plain", b"x" * 100)
    assert seen["Host"] == "e.org" and "Cookie" not in seen


def test_default_sender_gives_up_on_a_response_that_drips(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Each socket read is quick, so the per-read timeout never fires: the whole response
    has its own limit, or one tarpit page would hold the server until the run is killed."""
    monkeypatch.setattr(rt, "TOTAL_TIMEOUT", 0.2)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            with contextlib.suppress(OSError):
                for _ in range(200):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.02)

        def log_message(self, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            rt._send("http", "127.0.0.1", httpd.server_address[1], "e.org", "/",
                     {"Host": "e.org"})
        assert time.monotonic() - started < 2.0
    finally:
        httpd.shutdown()


def test_initialize_with_malformed_params_still_answers() -> None:
    srv = rt.Server((), None, rt.DEFAULT_NET)
    reply = rt.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": ["not", "an", "object"]}, srv)
    assert reply is not None and reply["result"]["protocolVersion"] == rt.PROTOCOL_FALLBACK
