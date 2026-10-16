"""Loopback shim between the Claude Code CLI and OpenRouter: restores WebSearch parity.

Why it exists (measured 2026-10-16): the CLI's WebSearch tool makes a side request with
``thinking: {"type": "disabled"}`` and a FORCED ``tool_choice``. OpenRouter rejects both
for Opus 5.5 ("Reasoning is mandatory for this endpoint", "tool_choice type tool/any not
supported"), so every WebSearch on the fallback path returned an error and 156 of 157
OpenRouter research calls ran with zero searches. OpenRouter itself serves Anthropic's
``web_search_20250305`` server tool from the same backend as the subscription whenever
those two fields are absent — so this shim drops them, and nothing else.

Scope, deliberately tiny:
  * Only ``POST .../messages`` JSON bodies are touched, and only the two fields above.
    Everything else (headers, auth, the streamed SSE response) passes through byte for
    byte. The shim holds no credential: the CLI's own Authorization header is forwarded.
  * Binds 127.0.0.1 on an ephemeral port; started lazily once per process, daemon thread.
  * Stdlib only, like the rest of the bot.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

UPSTREAM = "https://openrouter.ai/api"
UPSTREAM_TIMEOUT_S = 600
# Hop-by-hop headers (RFC 9110 §7.6.1) plus the ones this shim recomputes.
_DROP_REQUEST = frozenset({"host", "content-length", "connection", "keep-alive",
                           "transfer-encoding", "te", "trailer", "upgrade",
                           "proxy-authorization", "proxy-connection"})
_DROP_RESPONSE = frozenset({"connection", "keep-alive", "transfer-encoding", "te",
                            "trailer", "upgrade", "content-length"})


def rewrite_messages_body(body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The body OpenRouter accepts, plus a list of what changed (for tests and logs).

    * ``thinking: {"type": "disabled"}`` is removed — the model then thinks as it would by
      default, which is what OpenRouter requires for Opus 5.5 anyway.
    * a forced ``tool_choice`` (``tool`` / ``any``) becomes ``auto``. The CLI only forces a
      choice on its single-tool side requests (WebSearch), whose prompt asks for exactly
      that tool, so the model still calls it.
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
        changed.append("tool_choice")
    return out, changed


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 with chunked responses: streamed SSE reaches the CLI as it arrives.
    protocol_version = "HTTP/1.1"
    upstream = urllib.parse.urlsplit(UPSTREAM)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib name
        return  # quiet: the bot's own logs carry the per-call story

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b""
        if self.command == "POST" and self.path.split("?", 1)[0].endswith("/messages") and data:
            try:
                body = json.loads(data)
            except ValueError:
                body = None
            if isinstance(body, dict):
                body, changed = rewrite_messages_body(body)
                if changed:
                    data = json.dumps(body).encode("utf-8")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _DROP_REQUEST}
        headers["Content-Length"] = str(len(data))
        conn = http.client.HTTPSConnection(self.upstream.hostname, self.upstream.port or 443,
                                           timeout=UPSTREAM_TIMEOUT_S)
        started = False
        try:
            conn.request(self.command, self.upstream.path + self.path, body=data or None,
                         headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            started = True
            for key, value in resp.getheaders():
                if key.lower() not in _DROP_RESPONSE:
                    self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while chunk := resp.read1(65536):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
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


_LOCK = threading.Lock()
_SERVER: ThreadingHTTPServer | None = None


def base_url() -> str:
    """The shim's base URL (``http://127.0.0.1:<port>``), starting it on first use."""
    global _SERVER
    with _LOCK:
        if _SERVER is None:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, name="openrouter-shim",
                             daemon=True).start()
            _SERVER = server
        host, port = _SERVER.server_address[:2]
    return f"http://{host}:{port}"
