"""Stdio MCP server exposing TimeVault's time-locked research tools to a headless agent.

The cutoff arrives as a LAUNCH ARGUMENT (--cutoff), never as a tool parameter — the agent
cannot loosen it. Pair with ``--strict-mcp-config`` on the claude CLI so no other MCP
server (with live-web tools) rides along, and strip WebSearch/WebFetch/Read/Glob/Grep from
the allowed tools; then every research path the agent has runs through the vault.

Protocol: newline-delimited JSON-RPC 2.0 (the MCP stdio transport). Only the methods a
client actually sends are implemented: initialize, notifications/*, ping, tools/list,
tools/call. Tool failures return result.isError=true (never a protocol error), so the
agent sees "unavailable pre-cutoff" as information, not a crash.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from timevault import LeakError, TimeVault, parse_cutoff  # noqa: E402

PROTOCOL_FALLBACK = "2024-11-05"


def tool_definitions(cutoff_label: str, with_corpus: bool = False) -> list[dict]:
    """Tool schemas; every description states the cutoff so the agent plans around it.

    The two corpus tools are advertised only when the server was launched with --corpus."""
    locked = (
        f" TIME-LOCKED: only material from on or before {cutoff_label} exists; "
        "later information is not retrievable by any means."
    )
    corpus_caveat = (
        " CORPUS CAVEAT: results are URLs from FutureSearch's frozen scrape manifest "
        "(what the SOTA agent searched), NOT page content — the dataset ships no page "
        "text. Each url's crawl date is cutoff-checked (hits scraped after the as-of are "
        "excluded), but the crawl window (2025-10-13..28) can post-date a question's "
        "as-of by a few days. Retrieve the real pre-cutoff content by passing the url to "
        "fetch_page."
    )
    tools = [
        {
            "name": "search_news",
            "description": "Search worldwide news coverage inside a window ending at the "
                           "as-of date (GDELT). Returns headlines + URLs + dates only — "
                           "fetch content with fetch_page." + locked,
            "inputSchema": {
                "type": "object", "required": ["query"],
                "properties": {
                    "query": {"type": "string",
                              "description": "search terms; quote exact phrases"},
                    "days_back": {"type": "integer",
                                  "description": "window size before the as-of date "
                                                 "(default 120, max 365)"},
                    "max_results": {"type": "integer", "description": "default 10, max 25"},
                },
            },
        },
        {
            "name": "fetch_page",
            "description": "Fetch a URL's content exactly as it existed at the last "
                           "archived capture on or before the as-of date (Wayback "
                           "Machine). Pages never archived pre-cutoff are unavailable."
                           + locked,
            "inputSchema": {
                "type": "object", "required": ["url"],
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "description": "default 8000"},
                },
            },
        },
        {
            "name": "wikipedia_asof",
            "description": "Read a Wikipedia article exactly as it stood at the as-of "
                           "date (revision history)." + locked,
            "inputSchema": {
                "type": "object", "required": ["title"],
                "properties": {
                    "title": {"type": "string", "description": "article title"},
                    "max_chars": {"type": "integer", "description": "default 8000"},
                },
            },
        },
    ]
    if with_corpus:
        tools += [
            {
                "name": "search_corpus",
                "description": "Keyword-search the frozen BTF-2 corpus to DISCOVER the "
                               "source URLs FutureSearch's SOTA agent actually scraped. "
                               "Returns {url, title, date, snippet} only." + corpus_caveat,
                "inputSchema": {
                    "type": "object", "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "description": "keywords to match"},
                        "limit": {"type": "integer", "description": "default 8, max 25"},
                        "include_undated": {"type": "boolean",
                                            "description": "include hits with no parseable "
                                                           "crawl date (default false)"},
                    },
                },
            },
            {
                "name": "fetch_corpus_page",
                "description": "Return the stored manifest record for a corpus url. The "
                               "dataset ships NO page body, so 'text' is URL-derived "
                               "tokens, not article content — use fetch_page for the real "
                               "pre-cutoff content." + corpus_caveat,
                "inputSchema": {
                    "type": "object", "required": ["url"],
                    "properties": {
                        "url": {"type": "string"},
                        "max_chars": {"type": "integer", "description": "default 8000"},
                        "include_undated": {"type": "boolean", "description": "default false"},
                    },
                },
            },
        ]
    return tools


def handle_message(msg: dict, vault: TimeVault) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method = msg.get("method")
    msg_id = msg.get("id")
    if msg_id is None:  # notification — no response permitted
        return None

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def rpc_error(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    cutoff_label = vault.cutoff.strftime("%Y-%m-%d %H:%M UTC")
    if method == "initialize":
        client_proto = str((msg.get("params") or {}).get("protocolVersion")
                           or PROTOCOL_FALLBACK)
        return ok({
            "protocolVersion": client_proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "timevault", "version": "0.1.0"},
        })
    if method == "ping":
        return ok({})
    with_corpus = bool(vault.corpus_db)
    valid = {"search_news", "fetch_page", "wikipedia_asof"}
    if with_corpus:
        valid |= {"search_corpus", "fetch_corpus_page"}
    if method == "tools/list":
        return ok({"tools": tool_definitions(cutoff_label, with_corpus=with_corpus)})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if name not in valid:
            return rpc_error(-32602, f"unknown tool {name!r}")
        try:
            result = getattr(vault, name)(**arguments)
            text = json.dumps(result, ensure_ascii=False, indent=1)
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        except LeakError as exc:
            # The guarantee held: report it as tool output, not a crash.
            return ok({"content": [{"type": "text",
                                    "text": f"[time-lock] {exc}"}], "isError": True})
        except TypeError as exc:  # bad/missing arguments
            return rpc_error(-32602, str(exc)[:200])
        except Exception as exc:  # noqa: BLE001 - network faults etc.: tool-level error
            return ok({"content": [{"type": "text",
                                    "text": f"[{type(exc).__name__}] {str(exc)[:300]}"}],
                       "isError": True})
    return rpc_error(-32601, f"method {method!r} not implemented")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True,
                        help="ISO date/datetime; bare dates lock to 00:00 UTC that day")
    parser.add_argument("--corpus", default=None,
                        help="path to btf2_corpus.sqlite; enables the search_corpus and "
                             "fetch_corpus_page tools (time-locked + scrape-window caveat)")
    args = parser.parse_args(argv)
    vault = TimeVault(parse_cutoff(args.cutoff), corpus_db=args.corpus)

    stdin = sys.stdin
    stdout = sys.stdout
    if hasattr(stdin, "reconfigure"):
        stdin.reconfigure(encoding="utf-8")
        stdout.reconfigure(encoding="utf-8", newline="\n")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "parse error"}}),
                  flush=True)
            continue
        response = handle_message(msg, vault)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
