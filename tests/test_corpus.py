"""BTF-2 corpus support (v0.4.7): make FutureSearch's frozen scrape manifest searchable
by leak-free research runs, under the SAME cutoff discipline as every other TimeVault
source.

The real corpus ships URLs + crawl dates ONLY (no page text), so this layer is DISCOVERY
of the teacher's source URLs; the ``date_scraped`` crawl timestamp is run through the one
``_assert_pre_cutoff`` choke point. Under test: the FTS search (ranked, cutoff-filtered),
the fetch record (cutoff-respecting), the "no corpus configured" guard, and the MCP server
advertising the corpus tools iff launched with a corpus. No network, no real parquet: a
3-4 page SQLite fixture is built in tmp_path. The offline converter gets a smoke test that
skips unless the real 483 MB parquet is present.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import build_corpus_index as bci  # noqa: E402
import run_bench  # noqa: E402
import timevault_mcp  # noqa: E402
from timevault import LeakError, TimeVault  # noqa: E402

CUTOFF = datetime(2025, 10, 23, 10, 54, 7, tzinfo=UTC)

# (url, date_scraped, question_id) — mirrors the real manifest grain. Two pre-cutoff hits,
# one scraped AFTER the as-of (a leak to exclude), one with no parseable crawl date.
OSCE_URL = "https://www.osce.org/permanent-council/moldova-mission-extension"
VOTE_URL = "https://example.com/eu/moldova-council-vote-result"
LEAK_URL = "https://leak.example.com/moldova-mission-post-cutoff-report"
UNDATED_URL = "https://undated.example.com/moldova-briefing"
FIXTURE_ROWS = [
    (OSCE_URL, "2025-10-20T10:00:00.000000", "q-osce"),
    (VOTE_URL, "2025-10-21T09:30:00.123456", "q-osce"),
    (LEAK_URL, "2025-10-28T00:00:00.000000", "q-leak"),   # after CUTOFF -> excluded
    (UNDATED_URL, "", "q-undated"),                        # no date -> excluded by default
]


def make_corpus(path: Path, rows: list[tuple[str, str, str]]) -> str:
    """Hand-build a corpus SQLite matching what build_corpus_index.py emits (reusing its
    derive + caveat so the fixture schema can't drift from the real builder)."""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE pages(url TEXT PRIMARY KEY, title TEXT, date_scraped TEXT, "
                "question_id TEXT, text TEXT)")
    for url, date_scraped, qid in rows:
        title, text = bci.derive_title_text(url)
        con.execute("INSERT OR IGNORE INTO pages VALUES(?,?,?,?,?)",
                    (url, title, date_scraped, qid, text))
    con.execute("CREATE VIRTUAL TABLE pages_fts USING fts5("
                "title, text, content='pages', content_rowid='rowid')")
    con.execute("INSERT INTO pages_fts(rowid, title, text) "
                "SELECT rowid, title, text FROM pages")
    con.execute("CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO corpus_meta VALUES('caveat', ?)", (bci.CORPUS_CAVEAT,))
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def corpus_db(tmp_path: Path) -> str:
    return make_corpus(tmp_path / "corpus.sqlite", FIXTURE_ROWS)


@pytest.fixture
def vault(corpus_db: str) -> TimeVault:
    return TimeVault(CUTOFF, corpus_db=corpus_db)


class TestDeriveTitleText:
    def test_title_from_slug_text_from_host_and_path(self) -> None:
        title, text = bci.derive_title_text(OSCE_URL)
        assert title == "moldova mission extension"          # www. stripped, seps -> spaces
        assert "osce" in text and "permanent" in text and "moldova" in text
        assert "www" not in text.split()


class TestSearchCorpus:
    def test_returns_ranked_hits_with_the_documented_shape(self, vault: TimeVault) -> None:
        hits = vault.search_corpus("OSCE permanent council Moldova mission extension")
        assert hits, "expected pre-cutoff matches"
        for h in hits:
            assert set(h) >= {"url", "title", "date", "snippet"}
        # the OSCE page matches the most (and rarest) query terms -> ranked first
        assert hits[0]["url"] == OSCE_URL
        # dates are rendered YYYY-MM-DD
        assert hits[0]["date"] == "2025-10-20"

    def test_post_cutoff_hit_is_excluded(self, vault: TimeVault) -> None:
        hits = vault.search_corpus("moldova mission report", limit=25)
        assert LEAK_URL not in {h["url"] for h in hits}
        # the pre-cutoff moldova pages still come through
        assert OSCE_URL in {h["url"] for h in hits}

    def test_undated_hit_is_always_excluded(self, vault: TimeVault) -> None:
        hits = {h["url"] for h in vault.search_corpus("moldova briefing", limit=25)}
        assert UNDATED_URL not in hits

    def test_every_hit_carries_the_scrape_window_caveat(self, vault: TimeVault) -> None:
        hits = vault.search_corpus("moldova")
        assert hits and all("manifest" in h["caveat"].lower() for h in hits)

    def test_blank_query_returns_nothing_no_crash(self, vault: TimeVault) -> None:
        assert vault.search_corpus("   ***   ") == []


class TestFetchCorpusPage:
    def test_returns_stored_text_and_metadata_pre_cutoff(self, vault: TimeVault) -> None:
        got = vault.fetch_corpus_page(OSCE_URL)
        assert got["found"] is True
        assert got["date"] == "2025-10-20"
        assert "moldova" in got["text"]
        assert "manifest" in got["caveat"].lower()

    def test_post_cutoff_scrape_raises_leak_error(self, vault: TimeVault) -> None:
        with pytest.raises(LeakError):
            vault.fetch_corpus_page(LEAK_URL)

    def test_undated_raises_fail_closed(self, vault: TimeVault) -> None:
        with pytest.raises(LeakError):
            vault.fetch_corpus_page(UNDATED_URL)

    def test_unknown_url_is_information_not_error(self, vault: TimeVault) -> None:
        got = vault.fetch_corpus_page("https://not.in/the/manifest")
        assert got["found"] is False and "not in the" in got["text"].lower()

    def test_text_is_truncated_to_the_clamped_max_chars(self, tmp_path: Path) -> None:
        # max_chars clamps to [500, 30000] like the other fetchers, so truncation needs a
        # page whose derived text exceeds the floor.
        long_url = "https://example.com/" + "-".join(f"word{i}" for i in range(400))
        db = make_corpus(tmp_path / "long.sqlite",
                         [(long_url, "2025-10-20T10:00:00.000000", "q")])
        got = TimeVault(CUTOFF, corpus_db=db).fetch_corpus_page(long_url, max_chars=5)
        assert len(got["text"]) == 500 and got["truncated"] is True


class TestNoCorpusConfigured:
    def test_search_without_corpus_db_raises_clear_error(self) -> None:
        with pytest.raises(RuntimeError, match="no corpus configured"):
            TimeVault(CUTOFF).search_corpus("anything")

    def test_fetch_without_corpus_db_raises_clear_error(self) -> None:
        with pytest.raises(RuntimeError, match="no corpus configured"):
            TimeVault(CUTOFF).fetch_corpus_page("https://x")


class TestMcpCorpusTools:
    LABEL = "2025-10-23 10:54 UTC"

    def test_tool_definitions_include_corpus_only_with_corpus(self) -> None:
        base = {t["name"] for t in timevault_mcp.tool_definitions(self.LABEL)}
        assert base == {"search_news", "fetch_page", "wikipedia_asof"}
        withc = {t["name"] for t in
                 timevault_mcp.tool_definitions(self.LABEL, with_corpus=True)}
        assert withc == base | {"search_corpus", "fetch_corpus_page"}

    def test_corpus_tool_descriptions_state_cutoff_and_caveat(self) -> None:
        tools = {t["name"]: t for t in
                 timevault_mcp.tool_definitions(self.LABEL, with_corpus=True)}
        for name in ("search_corpus", "fetch_corpus_page"):
            desc = tools[name]["description"]
            assert "2025-10-13..28" in desc          # the scrape-window caveat
            assert "fetch_page" in desc               # points at the content path
            assert "include_undated" not in tools[name]["inputSchema"]["properties"]

    def test_tools_list_reflects_corpus_configuration(self, corpus_db: str) -> None:
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        with_names = {t["name"] for t in timevault_mcp.handle_message(
            msg, TimeVault(CUTOFF, corpus_db=corpus_db))["result"]["tools"]}
        assert {"search_corpus", "fetch_corpus_page"} <= with_names
        without = {t["name"] for t in timevault_mcp.handle_message(
            msg, TimeVault(CUTOFF))["result"]["tools"]}
        assert not ({"search_corpus", "fetch_corpus_page"} & without)

    def test_tools_call_dispatches_corpus_search_when_configured(self, corpus_db: str) -> None:
        msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": "search_corpus", "arguments": {"query": "moldova osce"}}}
        got = timevault_mcp.handle_message(msg, TimeVault(CUTOFF, corpus_db=corpus_db))
        assert got["result"]["isError"] is False
        assert OSCE_URL in got["result"]["content"][0]["text"]

    def test_agent_cannot_opt_into_undated_corpus_rows(self, corpus_db: str) -> None:
        msg = {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "search_corpus",
                "arguments": {"query": "moldova briefing", "include_undated": True},
            },
        }
        got = timevault_mcp.handle_message(msg, TimeVault(CUTOFF, corpus_db=corpus_db))
        assert got["error"]["code"] == -32602

    def test_corpus_tool_is_unknown_without_corpus(self) -> None:
        msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "search_corpus", "arguments": {"query": "x"}}}
        got = timevault_mcp.handle_message(msg, TimeVault(CUTOFF))
        assert got["error"]["code"] == -32602


class TestRunBenchCorpusWiring:
    BASE = ("claude -p --model claude-sonnet-5 --output-format json "
            "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch")

    def test_mcp_config_wires_corpus_into_server_argv(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(run_bench, "_MCP_CONFIG_CACHE", {})
        monkeypatch.setattr(run_bench, "_MCP_CONFIG_DIR", [])
        corpus = tmp_path / "c.sqlite"
        corpus.write_text("x", encoding="utf-8")
        import json
        path = run_bench.mcp_config_for("2025-10-23 10:54:07", str(corpus))
        args = json.loads(Path(path).read_text(encoding="utf-8"))["mcpServers"]["timevault"]["args"]
        assert "--corpus" in args and str(corpus.resolve()) in args
        # distinct (cutoff, corpus) keys never collide; no-corpus config omits the flag
        plain = run_bench.mcp_config_for("2025-10-23 10:54:07")
        assert plain != path
        assert "--corpus" not in json.loads(
            Path(plain).read_text(encoding="utf-8"))["mcpServers"]["timevault"]["args"]

    def test_allowed_tools_include_corpus_only_when_requested(self, tmp_path) -> None:
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}", encoding="utf-8")
        with_c = run_bench.leakfree_agent_cmd(self.BASE, "timevault", str(cfg),
                                              with_corpus=True)
        without = run_bench.leakfree_agent_cmd(self.BASE, "timevault", str(cfg))
        assert run_bench.CORPUS_TOOLS in with_c
        assert run_bench.CORPUS_TOOLS not in without

    def test_corpus_rejected_without_timevault(self) -> None:
        with pytest.raises(SystemExit):
            run_bench.main(["dummy.jsonl", "--leakfree", "none", "--corpus", __file__])

    def test_corpus_missing_path_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            run_bench.main(["dummy.jsonl", "--leakfree", "timevault",
                            "--corpus", "no/such/corpus.sqlite"])


REAL_PARQUET = ROOT / "bench" / "corpus" / "scraped_pages.parquet"


@pytest.mark.skipif(not REAL_PARQUET.exists(),
                    reason="real BTF-2 scraped_pages.parquet not present")
def test_build_corpus_index_smoke(tmp_path) -> None:
    """End-to-end on ONE row group of the real parquet (skipped when absent)."""
    pytest.importorskip("pyarrow")
    db = tmp_path / "smoke.sqlite"
    assert bci.build(REAL_PARQUET, db, force=True, limit_row_groups=1) == 0
    assert db.exists()
    con = sqlite3.connect(str(db))
    n = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    con.close()
    assert n > 0
    # the index is queryable and cutoff-gated through the normal TimeVault path
    v = TimeVault(datetime(2025, 10, 30, tzinfo=UTC), corpus_db=str(db))
    assert isinstance(v.search_corpus("report", limit=3), list)
