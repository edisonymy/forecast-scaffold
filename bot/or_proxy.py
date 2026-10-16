"""Loopback shim between the Claude Code CLI and OpenRouter: restores WebSearch parity.

Why it exists (measured 2026-10-16): the CLI's WebSearch tool makes a side request with
``thinking: {"type": "disabled"}`` and a FORCED ``tool_choice``. OpenRouter rejects both
for Opus 5.5 ("Reasoning is mandatory for this endpoint", "tool_choice type tool/any not
supported"), so every WebSearch on the fallback path returned an error and 156 of 157
OpenRouter research calls ran with zero searches. OpenRouter itself serves Anthropic's
``web_search_20250305`` server tool from the same backend as the subscription whenever
those two fields are absent — so this shim drops them, and nothing else.

Scope, deliberately tiny:
  * Only ``POST .../messages`` JSON bodies are touched: the two fields above, plus a
    one-line "do search" nudge on the WebSearch side request (see SEARCH_NUDGE).
    Everything else (headers, auth, the streamed SSE response) passes through byte for
    byte. The shim holds no credential: the CLI's own Authorization header is forwarded.
  * Binds 127.0.0.1 on an ephemeral port; started lazily once per process, daemon thread.
  * Stdlib only, like the rest of the bot.
"""

from __future__ import annotations

import contextlib
import copy
import http.client
import json
import select
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

UPSTREAM = "https://openrouter.ai/api"
UPSTREAM_CONNECT_TIMEOUT_S = 15
UPSTREAM_TIMEOUT_S = 600  # per read: matches the CLI's own API timeout
# Hop-by-hop headers (RFC 9110 §7.6.1) plus the ones this shim recomputes.
_DROP_REQUEST = frozenset({"host", "content-length", "connection", "keep-alive",
                           "transfer-encoding", "te", "trailer", "upgrade", "expect",
                           "proxy-authorization", "proxy-connection"})
_DROP_RESPONSE = frozenset({"connection", "keep-alive", "transfer-encoding", "te",
                            "trailer", "upgrade", "content-length"})
# With tool_choice relaxed to auto the model MAY answer from memory, and the CLI hands
# that text back as "search results" without complaint (verified against CLI 2.1.285).
# The nudge makes searching the expected move; the counters below measure any misses.
SEARCH_NUDGE = "Call the web_search tool for this query. Do not answer from memory."
_TOOL_USE_MARK = b"server_tool_use"

_STATS_LOCK = threading.Lock()
_STATS = {"search_requests": 0, "search_without_tool_use": 0}


def stats() -> dict[str, int]:
    """Counts since process start: WebSearch side requests, and those whose response
    never invoked the search tool (the model answered from memory)."""
    with _STATS_LOCK:
        return dict(_STATS)


def _count(key: str) -> None:
    with _STATS_LOCK:
        _STATS[key] += 1


def _nudge(body: dict[str, Any]) -> None:
    """Append SEARCH_NUDGE to the last user turn, in place (body's messages are a copy)."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = f"{content}\n\n{SEARCH_NUDGE}"
    elif isinstance(content, list):
        content.append({"type": "text", "text": SEARCH_NUDGE})


def rewrite_messages_body(body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The body OpenRouter accepts, plus a list of what changed (for tests and logs).

    * ``thinking: {"type": "disabled"}`` is removed — the model then thinks as it would by
      default, which is what OpenRouter requires for Opus 5.5 anyway.
    * a forced ``tool_choice`` (``tool`` / ``any``) becomes ``auto``, and the last user turn
      gets SEARCH_NUDGE. The CLI forces a choice only on its WebSearch side request
      (checked in the 2.1.285 bundle), whose single tool is the search.
    The input is never mutated.
    """
    changed: list[str] = []
    out = dict(body)
    thinking = out.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        del out["thinking"]
        changed.append("thinking")
    choice = out.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") in ("tool", "any"):
        out["tool_choice"] = {"type": "auto"}
        if "messages" in out:
            out["messages"] = copy.deepcopy(out["messages"])
            _nudge(out)
        changed.append("tool_choice")
    return out, changed


def _client_gone(sock: socket.socket) -> bool:
    """True when the CLI has closed its end (timeout/kill): stop paying for the stream."""
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        return bool(readable) and sock.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 with chunked responses: streamed SSE reaches the CLI as it arrives.
    protocol_version = "HTTP/1.1"
    upstream = urllib.parse.urlsplit(UPSTREAM)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib name
        return  # quiet: the bot's own logs carry the per-call story

    def _forward(self) -> None:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            # Never sent by the CLI's SDK; refuse rather than desync the connection.
            self.send_error(411, "openrouter shim needs Content-Length")
            self.close_connection = True
            return
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b""
        is_search = False
        if self.command == "POST" and self.path.split("?", 1)[0].endswith("/messages") and data:
            try:
                body = json.loads(data)
            except ValueError:
                body = None
            if isinstance(body, dict):
                body, changed = rewrite_messages_body(body)
                if changed:
                    data = json.dumps(body).encode("utf-8")
                is_search = "tool_choice" in changed
        if is_search:
            _count("search_requests")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _DROP_REQUEST}
        headers["Content-Length"] = str(len(data))
        conn = http.client.HTTPSConnection(self.upstream.hostname, self.upstream.port or 443,
                                           timeout=UPSTREAM_CONNECT_TIMEOUT_S)
        started = False
        saw_tool_use = False
        try:
            conn.connect()
            conn.sock.settimeout(UPSTREAM_TIMEOUT_S)
            conn.request(self.command, self.upstream.path + self.path, body=data or None,
                         headers=headers)
            resp = conn.getresponse()
            # send_response_only: no second Server/Date beside the upstream's own
            self.send_response_only(resp.status, resp.reason)
            started = True
            for key, value in resp.getheaders():
                if key.lower() not in _DROP_RESPONSE:
                    self.send_header(key, value)
            if self.command == "HEAD" or resp.status in (204, 304) or resp.status < 200:
                self.end_headers()  # bodyless: no Transfer-Encoding, no terminator
                return
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            tail = b""
            while chunk := resp.read1(65536):
                if is_search and not saw_tool_use:
                    saw_tool_use = _TOOL_USE_MARK in tail + chunk
                    tail = chunk[-len(_TOOL_USE_MARK):]
                if _client_gone(self.connection):
                    self.close_connection = True  # finally closes upstream: billing stops
                    return
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            if is_search and resp.status == 200 and not saw_tool_use:
                _count("search_without_tool_use")
        except (OSError, http.client.HTTPException) as exc:
            # Before headers went out, a 502 tells the CLI to retry; after, just drop the
            # connection (the CLI treats a truncated stream as a retryable failure).
            if not started:
                with contextlib.suppress(OSError):
                    self.send_error(502, f"openrouter shim upstream error: {type(exc).__name__}")
            self.close_connection = True
        finally:
            conn.close()

    do_POST = _forward
    do_GET = _forward
    do_HEAD = _forward


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # 3 parallel CLIs can burst past the default backlog of 5


_LOCK = threading.Lock()
_SERVER: ThreadingHTTPServer | None = None


def base_url() -> str:
    """The shim's base URL (``http://127.0.0.1:<port>``), starting it on first use."""
    global _SERVER
    with _LOCK:
        if _SERVER is None:
            server = _Server(("127.0.0.1", 0), _Handler)
            threading.Thread(target=server.serve_forever, name="openrouter-shim",
                             daemon=True).start()
            _SERVER = server
        host, port = _SERVER.server_address[:2]
    return f"http://{host}:{port}"
