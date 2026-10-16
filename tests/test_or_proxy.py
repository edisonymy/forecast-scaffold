"""The OpenRouter loopback shim: body rewrite rules and byte-faithful streaming."""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
import or_proxy  # noqa: E402


class TestRewrite:
    @pytest.mark.parametrize(("model", "slug"), [
        ("claude-haiku-5-5", "anthropic/claude-haiku-5.5"),
        ("claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5"),
        ("claude-sonnet-5", "anthropic/claude-sonnet-5"),
        ("anthropic/claude-opus-5.5", "anthropic/claude-opus-5.5"),
    ])
    def test_model_ids_mapped_to_openrouter_slugs(self, model: str, slug: str) -> None:
        out, changed = or_proxy.rewrite_messages_body({"model": model})
        assert out["model"] == slug
        assert ("model" in changed) == (model != slug)

    def test_websearch_side_request_is_made_acceptable(self) -> None:
        body = {"model": "anthropic/claude-opus-5.5", "thinking": {"type": "disabled"},
                "tool_choice": {"type": "tool", "name": "web_search"},
                "tools": [{"type": "web_search_20250305", "name": "web_search"}]}
        out, changed = or_proxy.rewrite_messages_body(body)
        assert changed == ["thinking", "tool_choice"]
        assert "thinking" not in out
        assert out["tool_choice"] == {"type": "auto"}
        assert out["tools"] == body["tools"]
        assert body["thinking"] == {"type": "disabled"}  # input not mutated

    def test_search_request_gets_nudge_without_mutating_input(self) -> None:
        body = {"tool_choice": {"type": "tool", "name": "web_search"},
                "messages": [{"role": "user", "content": [{"type": "text", "text": "q"}]}]}
        out, _ = or_proxy.rewrite_messages_body(body)
        assert out["messages"][-1]["content"][-1]["text"] == or_proxy.SEARCH_NUDGE
        assert len(body["messages"][0]["content"]) == 1
        out, _ = or_proxy.rewrite_messages_body(
            {"tool_choice": {"type": "any"}, "messages": [{"role": "user", "content": "q"}]})
        assert out["messages"][0]["content"].endswith(or_proxy.SEARCH_NUDGE)

    def test_any_choice_relaxed(self) -> None:
        out, changed = or_proxy.rewrite_messages_body({"tool_choice": {"type": "any"}})
        assert out["tool_choice"] == {"type": "auto"} and changed == ["tool_choice"]

    @pytest.mark.parametrize("body", [
        {"thinking": {"type": "adaptive"}, "tool_choice": {"type": "auto"}},
        {"thinking": {"type": "enabled", "budget_tokens": 1024}},
        {"messages": []},
    ])
    def test_main_loop_requests_untouched(self, body: dict) -> None:
        out, changed = or_proxy.rewrite_messages_body(body)
        assert out == body and changed == []


class _Upstream(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict, str]] = []

    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        data = self.rfile.read(int(self.headers["Content-Length"]))
        _Upstream.seen.append((self.path, json.loads(data), self.headers.get("Authorization", "")))
        payload = b"event: message_start\ndata: {}\n\n" * 50
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_shim_forwards_rewritten_body_and_streams_back(monkeypatch: pytest.MonkeyPatch) -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        # plain-HTTP fake upstream: swap the TLS connection class for the test only
        http_client = or_proxy.http.client
        monkeypatch.setattr(http_client, "HTTPSConnection", http_client.HTTPConnection)
        monkeypatch.setattr(or_proxy._Handler, "upstream", or_proxy.urllib.parse.urlsplit(
            f"http://127.0.0.1:{upstream.server_address[1]}/api"))
        body = json.dumps({"thinking": {"type": "disabled"},
                           "tool_choice": {"type": "tool", "name": "web_search"}}).encode()
        req = urllib.request.Request(or_proxy.base_url() + "/v1/messages?beta=true", data=body,
                                     headers={"Authorization": "Bearer sk-test",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            got = resp.read()
            assert resp.headers["Content-Type"] == "text/event-stream"
        assert got == b"event: message_start\ndata: {}\n\n" * 50
        path, forwarded, auth = _Upstream.seen[-1]
        assert path == "/api/v1/messages?beta=true"
        assert forwarded == {"tool_choice": {"type": "auto"}}
        assert auth == "Bearer sk-test"  # the CLI's own credential, passed through
    finally:
        upstream.shutdown()


def test_search_without_tool_use_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A WebSearch side request answered from memory (no server_tool_use) is counted."""
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        http_client = or_proxy.http.client
        monkeypatch.setattr(http_client, "HTTPSConnection", http_client.HTTPConnection)
        monkeypatch.setattr(or_proxy._Handler, "upstream", or_proxy.urllib.parse.urlsplit(
            f"http://127.0.0.1:{upstream.server_address[1]}/api"))
        before = or_proxy.stats()
        body = json.dumps({"tool_choice": {"type": "tool", "name": "web_search"},
                           "messages": [{"role": "user", "content": "q"}]}).encode()
        req = urllib.request.Request(or_proxy.base_url() + "/v1/messages", data=body)
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        after = or_proxy.stats()
        assert after["search_requests"] == before["search_requests"] + 1
        assert after["search_without_tool_use"] == before["search_without_tool_use"] + 1
    finally:
        upstream.shutdown()
        upstream.server_close()
