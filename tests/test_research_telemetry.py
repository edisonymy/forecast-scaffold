"""Research-mechanics telemetry for leak-free benchmark forecasts.

All fixtures are synthetic: no benchmark results, network, provider, or live bot prompt is
read.  The tests cover the content-free MCP event contract and the bench's per-row isolation,
aggregation, bounds, and backward-compatible fallbacks.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bot"))

import run_bench  # noqa: E402
import timevault_mcp  # noqa: E402
from timevault import TimeVault  # noqa: E402

CUTOFF = datetime(2025, 10, 23, 10, 54, 7, tzinfo=UTC)
SPEC = {
    "id": "btf2:telemetry",
    "source": "btf2",
    "question": "Will X happen?",
    "criteria": "Resolves YES if X happens.",
    "resolve_by": "2026-12-31",
    "as_of": "2025-10-23 10:54:07",
    "background": "Frozen context.",
    "crowd": {"value": 0.5},
}


def fenced(probability: float, **extra: Any) -> str:
    payload = {"probability": probability, "reasoning": "researched", "sources": []}
    payload.update(extra)
    return f"```json\n{json.dumps(payload)}\n```"


def bench_args(**overrides: Any) -> argparse.Namespace:
    defaults = dict(
        provider="subscription",
        agent_cmd=(
            "claude -p --model claude-opus-4-6 --output-format json "
            "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch"
        ),
        timeout=60,
        tier_config=None,
        spine_text=None,
        spine_arm=None,
        spine_sha=None,
        leakfree="timevault",
        corpus=None,
        angle_list=None,
        auto_mode="router",
        budget=0.0,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def telemetry_paths_from_cmd(cmd: str) -> tuple[Path, Path]:
    tokens = shlex.split(cmd)
    config_path = Path(tokens[tokens.index("--mcp-config") + 1])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    server_args = config["mcpServers"]["timevault"]["args"]
    telemetry_path = Path(server_args[server_args.index("--telemetry") + 1])
    return config_path, telemetry_path


def append_events(path: Path, events: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def event(
    tool: str,
    arguments: dict[str, str],
    *,
    success: bool = True,
    error: str | None = None,
) -> dict:
    return {"tool": tool, "arguments": arguments, "success": success, "error": error}


def test_mcp_events_are_one_per_call_and_never_contain_page_content(tmp_path: Path) -> None:
    telemetry = tmp_path / "events.jsonl"
    vault = TimeVault(CUTOFF)
    secret = "SECRET_PAGE_BODY_NEVER_LOG"
    vault.search_news = lambda **_kw: {"articles": [{"text": secret}]}  # type: ignore[method-assign]

    def fail_read(**_kw: Any) -> dict:
        raise RuntimeError(f"network response included {secret}")

    vault.fetch_page = fail_read  # type: ignore[method-assign]
    calls = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "search_news",
                "arguments": {"query": 'exact "quoted" query', "days_back": 7},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "fetch_page",
                "arguments": {"url": "https://example.test/a", "page_content": secret},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "WebFetch", "arguments": {"page_content": secret}},
        },
    ]

    responses = [timevault_mcp.handle_message(call, vault, telemetry) for call in calls]
    assert responses[0]["result"]["isError"] is False
    assert responses[1]["result"]["isError"] is True
    assert responses[2]["error"]["code"] == -32602

    raw_log = telemetry.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw_log.splitlines()]
    assert len(events) == len(calls)
    assert events[0] == {
        "tool": "search_news",
        "arguments": {"query": 'exact "quoted" query'},
        "success": True,
        "error": None,
    }
    assert events[1] == {
        "tool": "fetch_page",
        "arguments": {"url": "https://example.test/a"},
        "success": False,
        "error": "RuntimeError",
    }
    assert events[2]["arguments"] == {}
    assert secret not in raw_log

    # Attempts count as mechanics even when the read failed; unknown tools do not.
    fields = run_bench._telemetry_fields(telemetry)
    assert fields == {
        "n_searches": 1,
        "n_full_reads": 1,
        "queries": ['exact "quoted" query'],
    }


def test_query_list_is_bounded_without_changing_exact_strings(tmp_path: Path) -> None:
    telemetry = tmp_path / "events.jsonl"
    total = run_bench.MAX_TELEMETRY_QUERIES + 3
    expected = [f"  exact query {i}  " for i in range(total)]
    append_events(
        telemetry,
        [event("search_news", {"query": query}) for query in expected],
    )

    fields = run_bench._telemetry_fields(telemetry)
    assert fields["n_searches"] == total
    assert fields["queries"] == expected[: run_bench.MAX_TELEMETRY_QUERIES]


def test_parallel_forecasts_get_unique_configs_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(2)
    seen: list[tuple[Path, Path]] = []
    seen_lock = threading.Lock()

    def agent(
        cmd: str,
        _prompt: str,
        system: str | None,
        _timeout: int,
        _provider: str = "subscription",
    ) -> tuple[str, float, str]:
        paths = telemetry_paths_from_cmd(cmd)
        with seen_lock:
            seen.append(paths)
        assert system == run_bench.PLAIN_SYSTEM
        barrier.wait(timeout=5)
        return fenced(0.4, source_classes=["Official"]), 0.01, "claude-opus-4-6"

    monkeypatch.setattr(run_bench, "run_agent", agent)
    args = bench_args()
    specs = [dict(SPEC, id=f"btf2:telemetry-{i}") for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda spec: run_bench.forecast_one(spec, "plain", args), specs))

    assert all(row is not None for row in rows)
    assert {row["n_searches"] for row in rows if row is not None} == {0}
    assert {tuple(row["queries"]) for row in rows if row is not None} == {()}
    assert {tuple(row["source_classes"]) for row in rows if row is not None} == {
        ("official",)
    }
    assert len({config for config, _telemetry in seen}) == 2
    assert len({telemetry for _config, telemetry in seen}) == 2
    assert all(not config.exists() and not telemetry.exists() for config, telemetry in seen)


def test_angle_subruns_aggregate_calls_queries_and_source_classes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus.sqlite"
    corpus.write_text("synthetic", encoding="utf-8")
    scripted = [
        (
            [
                event("search_news", {"query": 'exact F query "quoted"'}),
                event("fetch_page", {"url": "https://example.test/f"}),
            ],
            fenced(0.3, source_classes=[" Official data ", "NEWS", 7]),
        ),
        (
            [
                event(
                    "search_corpus",
                    {"query": "exact D query"},
                    success=False,
                    error="RuntimeError",
                ),
                # Manifest metadata is not a full-page read.
                event("fetch_corpus_page", {"url": "https://example.test/d"}),
            ],
            fenced(0.5, source_classes=["news", "Academic / paper"]),
        ),
        (
            [
                event(
                    "wikipedia_asof",
                    {"title": "Synthetic topic"},
                    success=False,
                    error="leak_error",
                )
            ],
            # Legacy payload: missing source_classes remains valid.
            fenced(0.4),
        ),
    ]
    systems: list[str] = []
    paths: list[tuple[Path, Path]] = []

    def agent(
        cmd: str,
        _prompt: str,
        system: str | None,
        _timeout: int,
        _provider: str = "subscription",
    ) -> tuple[str, float, str]:
        assert system is not None
        systems.append(system)
        config_path, telemetry_path = telemetry_paths_from_cmd(cmd)
        paths.append((config_path, telemetry_path))
        events, output = scripted.pop(0)
        append_events(telemetry_path, events)
        return output, 0.01, "claude-opus-4-6"

    monkeypatch.setattr(run_bench, "run_agent", agent)
    row = run_bench.forecast_one(
        SPEC,
        "angles",
        bench_args(corpus=str(corpus), angle_list=["F", "D", "A"]),
    )

    assert row is not None
    assert row["n_searches"] == 2
    assert row["n_full_reads"] == 2
    assert row["queries"] == ['exact F query "quoted"', "exact D query"]
    assert row["source_classes"] == [
        "news", "web", "corpus", "official-data", "academic-paper"
    ]
    assert len(systems) == 3
    sections = run_bench.load_angle_sections()
    base_system = run_bench.build_system("high", blind=True, config=None, multi_run=True)
    assert systems == [
        base_system + run_bench.angle_brief_section(letter, sections[letter])
        for letter in ("F", "D", "A")
    ]
    # Every angle shares this forecast's sink/config, and both disappear on return.
    assert len({config for config, _telemetry in paths}) == 1
    assert len({telemetry for _config, telemetry in paths}) == 1
    assert all(not config.exists() and not telemetry.exists() for config, telemetry in paths)


def test_non_timevault_rows_do_not_invent_tool_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def agent(
        _cmd: str,
        _prompt: str,
        system: str | None,
        _timeout: int,
        _provider: str = "subscription",
    ) -> tuple[str, float, str]:
        assert system == run_bench.build_system("high", blind=True, config=None)
        return (
            fenced(
                0.6,
                sources=[
                    "https://www.gov.uk/official-report",
                    "https://arxiv.org/abs/1234.5678",
                    "ONS statistics dataset",
                ],
                source_classes=[" Official / Government ", "NEWS", {"unsafe": "nested"}],
            ),
            0.01,
            "claude-opus-4-6",
        )

    monkeypatch.setattr(run_bench, "run_agent", agent)
    row = run_bench.forecast_one(SPEC, "high", bench_args(leakfree="off"))

    assert row is not None
    assert row["n_searches"] is None
    assert row["n_full_reads"] is None
    assert row["queries"] is None
    assert row["source_classes"] == [
        "official", "academic", "dataset", "official-government", "news"
    ]
