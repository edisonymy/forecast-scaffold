"""The leak-free pastcast guarantee (v0.4.6): every document a pastcast agent can reach
must carry a machine-verified timestamp at or before the question's as-of instant.

Three layers under test: the TimeVault clients (one choke-point assert; the Wayback
nearest-snapshot redirect trap), the stdio MCP server (cutoff pinned in argv, tool errors
never crash the protocol), and the run_bench wiring (live web + filesystem tools stripped,
one combined disallow belt, per-cutoff MCP configs).
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bot"))

import run_bench  # noqa: E402
import timevault  # noqa: E402
import timevault_mcp  # noqa: E402
from timevault import LeakError, TimeVault, html_to_text, parse_cutoff  # noqa: E402

CUTOFF = datetime(2025, 10, 23, 10, 54, 7, tzinfo=UTC)


class FakeResponse:
    def __init__(self, body: bytes = b"ok", url: str = "https://effective.test/") -> None:
        self.body = body
        self.url = url

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


def vault_with(responses: dict[str, tuple[str, str]]) -> TimeVault:
    """A vault whose transport serves canned (body, effective_url) by URL substring."""
    vault = TimeVault(CUTOFF)

    def fake_http(url: str) -> tuple[str, str]:
        for key, value in responses.items():
            if key in url:
                return value
        raise AssertionError(f"unexpected URL fetched: {url}")

    vault._http = fake_http  # type: ignore[method-assign]
    return vault


class TestTransportRetries:
    def test_timeout_is_retried_without_weakening_the_response(self, monkeypatch) -> None:
        calls = 0
        sleeps: list[float] = []

        def flaky(*_args: object, **_kwargs: object) -> FakeResponse:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise TimeoutError("archive stalled")
            return FakeResponse(b"archived body", "https://archive.test/final")

        monkeypatch.setattr(timevault.urllib.request, "urlopen", flaky)
        monkeypatch.setattr(timevault.time, "sleep", sleeps.append)
        body, effective = TimeVault(CUTOFF)._http("https://archive.test/query")

        assert calls == 3
        assert sleeps == [0.5, 1.0]
        assert body == "archived body"
        assert effective == "https://archive.test/final"

    def test_429_retries_but_nontransient_http_error_does_not(self, monkeypatch) -> None:
        calls = 0

        def rate_limited(*_args: object, **_kwargs: object) -> FakeResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise urllib.error.HTTPError(
                    "https://api.test", 429, "slow down", {"Retry-After": "0"}, None
                )
            return FakeResponse()

        monkeypatch.setattr(timevault.urllib.request, "urlopen", rate_limited)
        monkeypatch.setattr(timevault.time, "sleep", lambda _seconds: None)
        assert TimeVault(CUTOFF)._http("https://api.test")[0] == "ok"
        assert calls == 2

        def not_found(*_args: object, **_kwargs: object) -> FakeResponse:
            raise urllib.error.HTTPError(
                "https://api.test", 404, "missing", {}, None
            )

        monkeypatch.setattr(timevault.urllib.request, "urlopen", not_found)
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            TimeVault(CUTOFF)._http("https://api.test")
        assert excinfo.value.code == 404

    def test_https_transport_downgrade_is_fatal_before_body_read(self, monkeypatch) -> None:
        response = FakeResponse(b"injected future bytes", "http://archive.test/final")
        monkeypatch.setattr(timevault.urllib.request, "urlopen", lambda *_a, **_kw: response)

        with pytest.raises(LeakError, match="transport downgraded"):
            TimeVault(CUTOFF)._http("https://archive.test/query")


class TestParseCutoff:
    def test_bare_date_locks_to_start_of_day(self) -> None:
        assert parse_cutoff("2025-10-23").strftime("%H%M%S") == "000000"

    def test_full_timestamp_kept_exactly(self) -> None:
        got = parse_cutoff("2025-10-23 10:54:07.843152")
        assert got.hour == 10 and got.minute == 54

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_cutoff("next tuesday")


class TestFetchPage:
    def test_serves_last_pre_cutoff_snapshot(self) -> None:
        cdx = json.dumps([["urlkey", "timestamp", "original", "mime", "status", "d", "l"],
                          ["k", "20251020010528", "https://example.com/x", "text/html",
                           "200", "D", "1"]])
        vault = vault_with({
            "cdx/search": (cdx, "cdx"),
            "20251020010528id_": ("<html><body>Old news.</body></html>",
                                  "https://web.archive.org/web/20251020010528id_/"
                                  "https://example.com/x"),
        })
        got = vault.fetch_page("https://example.com/x")
        assert "Old news." in got["text"]
        assert got["archived_at"].startswith("2025-10-20")

    def test_redirect_to_post_cutoff_snapshot_is_fatal(self) -> None:
        # Wayback resolves to the NEAREST capture, which can postdate the request —
        # the effective URL's stamp is authoritative and must be re-verified.
        cdx = json.dumps([["urlkey", "timestamp", "original", "mime", "status", "d", "l"],
                          ["k", "20251023000000", "https://example.com/x", "text/html",
                           "200", "D", "1"]])
        vault = vault_with({
            "cdx/search": (cdx, "cdx"),
            "20251023000000id_": ("<html>tomorrow's paper</html>",
                                  "https://web.archive.org/web/20260101000000id_/"
                                  "https://example.com/x"),
        })
        with pytest.raises(LeakError):
            vault.fetch_page("https://example.com/x")

    def test_redirect_out_of_wayback_replay_is_fatal(self) -> None:
        cdx = json.dumps([["urlkey", "timestamp", "original", "mime", "status", "d", "l"],
                          ["k", "20251020010528", "https://example.com/x", "text/html",
                           "200", "D", "1"]])
        vault = vault_with({
            "cdx/search": (cdx, "cdx"),
            # A captured redirect can escape to today's live origin.  The requested CDX
            # stamp is not provenance for bytes served from this final URL.
            "20251020010528id_": ("<html>live future content</html>",
                                  "https://example.com/x"),
        })
        with pytest.raises(LeakError, match="escaped the exact stamped"):
            vault.fetch_page("https://example.com/x")

    @pytest.mark.parametrize("effective", [
        "https://web.archive.org/foo/web/20251020010528id_/https://example.com/x",
        "https://web.archive.org/web/20251020010528*/https://example.com/x",
        "https://web.archive.org/web/20251020010528/https://example.com/x",
        "http://web.archive.org/web/20251020010528id_/https://example.com/x",
    ])
    def test_calendar_toolbar_and_nonprefix_wayback_pages_are_fatal(
        self, effective: str,
    ) -> None:
        cdx = json.dumps([["urlkey", "timestamp", "original", "mime", "status", "d", "l"],
                          ["k", "20251020010528", "https://example.com/x", "text/html",
                           "200", "D", "1"]])
        vault = vault_with({
            "cdx/search": (cdx, "cdx"),
            "20251020010528id_": ("<html>current Wayback UI</html>", effective),
        })

        with pytest.raises(LeakError, match="exact stamped"):
            vault.fetch_page("https://example.com/x")

    def test_no_pre_cutoff_snapshot_is_information_not_error(self) -> None:
        header_only = json.dumps([["urlkey", "timestamp", "original"]])
        vault = vault_with({"cdx/search": (header_only, "cdx")})
        got = vault.fetch_page("https://example.com/brand-new-page")
        assert got["archived_at"] is None
        assert "unavailable pre-cutoff" in got["text"] or "No archived version" in got["text"]

    def test_exact_cdx_mismatch_is_fatal_for_generic_agent_fetch(self) -> None:
        cdx = json.dumps([["urlkey", "timestamp", "original"], [
            "k", "20211229023618", "https://en.wikipedia.org/wiki/Twitter",
        ]])
        vault = vault_with({"cdx/search": (cdx, "cdx")})

        with pytest.raises(LeakError, match="exact requested historical URL"):
            vault.fetch_page("https://en.wikipedia.org/wiki/X_(social_network)")


class TestSearchNews:
    def test_window_ends_at_cutoff_and_strays_are_dropped(self) -> None:
        captured: list[str] = []
        vault = TimeVault(CUTOFF)

        def fake_http(url: str) -> tuple[str, str]:
            captured.append(url)
            return (json.dumps({"articles": [
                {"title": "ok", "url": "https://a", "domain": "a.com",
                 "seendate": "20251001T120000Z", "language": "English"},
                {"title": "leak", "url": "https://b", "domain": "b.com",
                 "seendate": "20260101T120000Z", "language": "English"},
            ]}), url)

        vault._http = fake_http  # type: ignore[method-assign]
        got = vault.search_news("test query")
        assert "ENDDATETIME=20251023105407" in captured[0]
        assert got["response_valid"] is True
        assert [a["url"] for a in got["articles"]] == ["https://a"]

    def test_rate_limit_text_degrades_to_empty_not_crash(self, monkeypatch) -> None:
        vault = TimeVault(CUTOFF)
        vault._http = lambda url: ("Please limit requests to one every 5 seconds", url)  # type: ignore[method-assign]
        monkeypatch.setattr(timevault.time, "sleep", lambda s: None)
        got = vault.search_news("anything")
        assert got["response_valid"] is False
        assert got["articles"] == [] and "unavailable" in got["note"]


class TestWikipediaAsof:
    def test_exact_title_snapshot_at_cutoff_is_served(self) -> None:
        cdx = json.dumps([["urlkey", "timestamp", "original"], [
            "k", "20251007140954",
            "https://en.wikipedia.org/wiki/Lebanese_Armed_Forces",
        ]])
        vault = vault_with({
            "cdx/search": (cdx, "cdx"),
            "20251007140954id_": (
                "<html><body>The LAF is...</body></html>",
                "https://web.archive.org/web/20251007140954id_/"
                "https://en.wikipedia.org/wiki/Lebanese_Armed_Forces",
            ),
        })
        got = vault.wikipedia_asof("Lebanese Armed Forces")
        assert got["archived_at"].startswith("2025-10-07")
        assert "The LAF is" in got["text"]
        assert got["retrieval"] == "wayback_exact_title"

    def test_post_cutoff_snapshot_redirect_is_fatal(self) -> None:
        cdx = json.dumps([["urlkey", "timestamp", "original"], [
            "k", "20251007140954", "https://en.wikipedia.org/wiki/X",
        ]])
        vault = vault_with({
            "cdx/search": (cdx, "cdx"),
            "20251007140954id_": (
                "<html><body>future</body></html>",
                "https://web.archive.org/web/20260101000000id_/"
                "https://en.wikipedia.org/wiki/X",
            ),
        })
        with pytest.raises(LeakError):
            vault.wikipedia_asof("X")

    def test_missing_pre_cutoff_title_never_uses_live_mediawiki(self) -> None:
        header_only = json.dumps([["urlkey", "timestamp", "original"]])
        calls: list[str] = []
        vault = TimeVault(CUTOFF)

        def exact_archive_only(url: str) -> tuple[str, str]:
            calls.append(url)
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            assert parsed.hostname == "web.archive.org"
            assert params["url"] == [
                "https://en.wikipedia.org/wiki/2026_future-created_article"
            ]
            assert params["matchType"] == ["exact"]
            return header_only, url

        vault._http = exact_archive_only  # type: ignore[method-assign]
        got = vault.wikipedia_asof("2026 future-created article")

        assert got["archived_at"] is None
        assert got["retrieval"] == "wayback_exact_title"
        assert len(calls) == 1

    def test_post_cutoff_x_rename_cannot_reveal_old_twitter_revision(self) -> None:
        """Regression: MediaWiki mapped this post-2023 title to Twitter's old revisions."""
        cutoff = datetime(2022, 1, 1, tzinfo=UTC)
        header_only = json.dumps([["urlkey", "timestamp", "original"]])
        vault = TimeVault(cutoff)
        calls: list[str] = []

        def historical_url_only(url: str) -> tuple[str, str]:
            calls.append(url)
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            assert params["url"] == [
                "https://en.wikipedia.org/wiki/X_(social_network)"
            ]
            assert params["to"] == ["20220101000000"]
            return header_only, url

        vault._http = historical_url_only  # type: ignore[method-assign]
        got = vault.wikipedia_asof("X (social network)")

        assert got["archived_at"] is None
        assert "Twitter" not in got["text"]
        assert len(calls) == 1

    def test_cdx_cannot_alias_future_wikipedia_title_to_old_page(self) -> None:
        cdx = json.dumps([["urlkey", "timestamp", "original"], [
            "k", "20211229023618", "https://en.wikipedia.org/wiki/Twitter",
        ]])
        vault = vault_with({"cdx/search": (cdx, "cdx")})

        with pytest.raises(LeakError, match="exact requested historical URL"):
            vault.wikipedia_asof("X (social network)")


def test_html_to_text_drops_scripts() -> None:
    text = html_to_text("<html><script>evil()</script><p>kept</p></html>")
    assert "kept" in text and "evil" not in text


def test_manual_cli_reconfigures_windows_console_for_unicode(monkeypatch) -> None:
    class FakeVault:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def wikipedia_asof(self, _title: str) -> dict:
            return {"title": "Pogačar", "text": "pre-cutoff ✅"}

    raw = io.BytesIO()
    cp1252_stdout = io.TextIOWrapper(raw, encoding="cp1252", write_through=True)
    original_stdout = sys.stdout
    monkeypatch.setattr(timevault, "TimeVault", FakeVault)
    monkeypatch.setattr(sys, "stdout", cp1252_stdout)
    try:
        assert timevault.main([
            "--cutoff", "2025-10-23", "wikipedia_asof", "Pogačar",
        ]) == 0
        cp1252_stdout.flush()
    finally:
        monkeypatch.setattr(sys, "stdout", original_stdout)

    assert "Pogačar" in raw.getvalue().decode("utf-8")
    assert "✅" in raw.getvalue().decode("utf-8")


class TestMcpServer:
    def setup_method(self) -> None:
        self.vault = TimeVault(CUTOFF)

    def rpc(self, method: str, params: dict | None = None, msg_id: int | None = 1) -> dict | None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if msg_id is not None:
            msg["id"] = msg_id
        if params is not None:
            msg["params"] = params
        return timevault_mcp.handle_message(msg, self.vault)

    def test_initialize_echoes_client_protocol(self) -> None:
        got = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        assert got["result"]["protocolVersion"] == "2025-06-18"
        assert got["result"]["serverInfo"]["name"] == "timevault"

    def test_tools_list_names_the_cutoff_in_every_description(self) -> None:
        got = self.rpc("tools/list")
        tools = got["result"]["tools"]
        assert {t["name"] for t in tools} == {"search_news", "fetch_page", "wikipedia_asof"}
        assert all("2025-10-23" in t["description"] for t in tools)

    def test_notifications_get_no_response(self) -> None:
        assert self.rpc("notifications/initialized", msg_id=None) is None

    def test_tool_call_dispatches_and_wraps_result(self) -> None:
        self.vault.wikipedia_asof = lambda **kw: {"title": kw["title"], "text": "ok"}  # type: ignore[method-assign]
        got = self.rpc("tools/call", {"name": "wikipedia_asof",
                                      "arguments": {"title": "X"}})
        assert got["result"]["isError"] is False
        assert "ok" in got["result"]["content"][0]["text"]

    def test_leak_error_is_tool_output_not_protocol_error(self) -> None:
        def boom(**kw):  # noqa: ANN003
            raise LeakError("stamped after the cutoff")
        self.vault.fetch_page = boom  # type: ignore[method-assign]
        got = self.rpc("tools/call", {"name": "fetch_page",
                                      "arguments": {"url": "https://x"}})
        assert got["result"]["isError"] is True
        assert "time-lock" in got["result"]["content"][0]["text"]
        assert "error" not in got

    def test_unknown_tool_is_invalid_params(self) -> None:
        got = self.rpc("tools/call", {"name": "WebSearch", "arguments": {}})
        assert got["error"]["code"] == -32602


class TestRunBenchWiring:
    BASE = ("claude -p --model claude-sonnet-5 --output-format json "
            "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch")

    def test_timevault_cmd_strips_live_tools_and_pins_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}", encoding="utf-8")
        cmd = run_bench.leakfree_agent_cmd(self.BASE, "timevault", str(cfg))
        assert "WebSearch" not in cmd.split("--disallowed-tools")[0].replace(
            run_bench.TIMEVAULT_TOOLS, "")
        assert run_bench.TIMEVAULT_TOOLS in cmd
        assert "--strict-mcp-config" in cmd
        for banned in ("Read", "Glob", "Grep", "Bash", "WebSearch", "WebFetch"):
            assert banned in cmd.split("--disallowed-tools")[1]

    def test_none_mode_allows_no_research_tools_at_all(self) -> None:
        cmd = run_bench.leakfree_agent_cmd(self.BASE, "none", None)
        assert "--allowed-tools" not in cmd
        assert "--mcp-config" not in cmd
        assert "WebSearch" in cmd.split("--disallowed-tools")[1]

    def test_timevault_without_config_refuses(self) -> None:
        with pytest.raises(ValueError):
            run_bench.leakfree_agent_cmd(self.BASE, "timevault", None)

    def test_as_of_prefers_structured_field(self) -> None:
        spec = {"as_of": "2025-10-23 10:54:07", "background": "AS-OF DATE: 1999-01-01"}
        assert run_bench.spec_as_of(spec) == "2025-10-23 10:54:07"

    def test_as_of_uses_prospective_freeze_after_explicit_field(self) -> None:
        spec = {
            "frozen_at": "2026-07-10T10:49:31Z",
            "background": "AS-OF DATE: 1999-01-01",
        }
        assert run_bench.spec_as_of(spec) == "2026-07-10T10:49:31Z"
        spec["as_of"] = "2026-07-09T00:00:00Z"
        assert run_bench.spec_as_of(spec) == "2026-07-09T00:00:00Z"

    def test_as_of_regex_fallback_reads_existing_btf2_sets(self) -> None:
        spec = {"background": "AS-OF DATE: 2025-10-23 10:54:07.843152 — forecast as if"}
        assert run_bench.spec_as_of(spec) == "2025-10-23 10:54:07.843152"
        assert run_bench.spec_as_of({"background": "no date here"}) is None

    def test_mcp_config_pins_cutoff_in_server_argv(self, monkeypatch) -> None:
        monkeypatch.setattr(run_bench, "_MCP_CONFIG_CACHE", {})
        monkeypatch.setattr(run_bench, "_MCP_CONFIG_DIR", [])
        path = run_bench.mcp_config_for("2025-10-23 10:54:07")
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        server = config["mcpServers"]["timevault"]
        assert server["args"][-2:] == ["--cutoff", "2025-10-23 10:54:07"]
        assert "timevault_mcp.py" in server["args"][0]
        # same cutoff -> same file; the cache never mints duplicates
        assert run_bench.mcp_config_for("2025-10-23 10:54:07") == path
