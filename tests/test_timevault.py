"""The leak-free pastcast guarantee (v0.4.6): every document a pastcast agent can reach
must carry a machine-verified timestamp at or before the question's as-of instant.

Three layers under test: the TimeVault clients (one choke-point assert; the Wayback
nearest-snapshot redirect trap), the stdio MCP server (cutoff pinned in argv, tool errors
never crash the protocol), and the run_bench wiring (live web + filesystem tools stripped,
one combined disallow belt, per-cutoff MCP configs).
"""

from __future__ import annotations

import json
import sys
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
                                  "https://web.archive.org/web/20251020010528id_/x"),
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
                                  "https://web.archive.org/web/20260101000000id_/x"),
        })
        with pytest.raises(LeakError):
            vault.fetch_page("https://example.com/x")

    def test_no_pre_cutoff_snapshot_is_information_not_error(self) -> None:
        header_only = json.dumps([["urlkey", "timestamp", "original"]])
        vault = vault_with({"cdx/search": (header_only, "cdx")})
        got = vault.fetch_page("https://example.com/brand-new-page")
        assert got["archived_at"] is None
        assert "unavailable pre-cutoff" in got["text"] or "No archived version" in got["text"]


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
        assert [a["url"] for a in got["articles"]] == ["https://a"]

    def test_rate_limit_text_degrades_to_empty_not_crash(self, monkeypatch) -> None:
        vault = TimeVault(CUTOFF)
        vault._http = lambda url: ("Please limit requests to one every 5 seconds", url)  # type: ignore[method-assign]
        monkeypatch.setattr(timevault.time, "sleep", lambda s: None)
        got = vault.search_news("anything")
        assert got["articles"] == [] and "unavailable" in got["note"]


class TestWikipediaAsof:
    def test_revision_at_cutoff_is_served(self) -> None:
        body = json.dumps({"query": {"pages": [{
            "title": "Lebanese Armed Forces",
            "revisions": [{"revid": 1, "timestamp": "2025-10-07T14:09:54Z",
                           "slots": {"main": {"content": "The LAF is..."}}}],
        }]}})
        vault = vault_with({"action=query": (body, "wiki")})
        got = vault.wikipedia_asof("Lebanese Armed Forces")
        assert got["revision_at"].startswith("2025-10-07")
        assert "The LAF is" in got["text"]

    def test_post_cutoff_revision_stamp_is_fatal(self) -> None:
        body = json.dumps({"query": {"pages": [{
            "title": "X",
            "revisions": [{"revid": 1, "timestamp": "2026-01-01T00:00:00Z",
                           "slots": {"main": {"content": "future"}}}],
        }]}})
        vault = vault_with({"action=query": (body, "wiki")})
        with pytest.raises(LeakError):
            vault.wikipedia_asof("X")


def test_html_to_text_drops_scripts() -> None:
    text = html_to_text("<html><script>evil()</script><p>kept</p></html>")
    assert "kept" in text and "evil" not in text


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
