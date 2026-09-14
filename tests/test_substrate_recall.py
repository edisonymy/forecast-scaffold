"""Synthetic guards for the diagnostic substrate-recall proxy; no real results/network."""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

# bench/ is not a package (same pattern as tests/test_probe.py): CI's bare `pytest`
# does not put the repo root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench" / "analysis"))

import substrate_recall as audit  # noqa: E402


def make_corpus(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE pages(url TEXT PRIMARY KEY, title TEXT, date_scraped TEXT, "
        "question_id TEXT, text TEXT)"
    )
    for url, stamp, qid in rows:
        title, text = audit.derive_title_text(url)
        con.execute("INSERT INTO pages VALUES(?,?,?,?,?)", (url, title, stamp, qid, text))
    con.execute(
        "CREATE VIRTUAL TABLE pages_fts USING fts5("
        "title, text, content='pages', content_rowid='rowid')"
    )
    con.execute(
        "INSERT INTO pages_fts(rowid,title,text) SELECT rowid,title,text FROM pages"
    )
    con.commit()
    con.close()
    return path


def test_validate_gold_pins_file_order_and_query_cap() -> None:
    specs = [{"id": "btf2:q1"}, {"id": "btf2:q2"}]
    gold = [
        {"qid": "btf2:q1", "proxy_kind": "question_source_set", "queries": ["a"]},
        {"qid": "btf2:q2", "proxy_kind": "question_source_set", "queries": ["b"]},
    ]
    audit.validate_gold(gold, specs, max_queries=1, expected_first=2)

    with pytest.raises(ValueError, match="file order"):
        audit.validate_gold(list(reversed(gold)), specs, max_queries=1, expected_first=2)
    gold[0]["queries"] = ["a", "extra"]
    with pytest.raises(ValueError, match="1..1"):
        audit.validate_gold(gold, specs, max_queries=1, expected_first=2)


def test_scoped_search_preserves_qid_and_cutoff() -> None:
    sources = {
        "btf2:q1": {
            "https://official.example/alpha-status": "2025-10-20T00:00:00",
            "https://official.example/alpha-after": "2025-10-25T00:00:00",
        },
        "btf2:q2": {
            "https://other.example/alpha-unrelated": "2025-10-20T00:00:00",
        },
    }
    con = audit.build_scoped_index(sources)
    try:
        hits = audit.scoped_search(
            con, "btf2:q1", "alpha", datetime(2025, 10, 23, tzinfo=UTC), 25
        )
    finally:
        con.close()

    assert hits == ["https://official.example/alpha-status"]


def test_audit_one_separates_discovery_from_cutoff(tmp_path: Path) -> None:
    relevant = "https://official.example/alpha-status"
    corpus = make_corpus(tmp_path / "corpus.sqlite", [
        (relevant, "2025-10-20T00:00:00", "q1"),
        ("https://other.example/beta", "2025-10-20T00:00:00", "q2"),
    ])
    sources = {relevant: "2025-10-20T00:00:00"}
    scoped = audit.build_scoped_index({"btf2:q1": sources})
    try:
        detail = audit.audit_one(
            {
                "qid": "btf2:q1",
                "proxy_kind": "question_source_set",
                "load_bearing_claim": "alpha status",
                "queries": ["alpha status"],
            },
            {"id": "btf2:q1", "as_of": "2025-10-23T00:00:00+00:00"},
            sources,
            {relevant: "2025-10-20T00:00:00"},
            scoped,
            corpus,
            top_k=25,
            secondary_k=8,
            fetch_readability=False,
        )
    finally:
        scoped.close()

    assert detail["production_retention_rate"] == 1.0
    assert detail["production_cutoff_eligible_rate"] == 1.0
    assert detail["global_discoverable_top25"] is True
    assert detail["qid_scoped_discoverable_top25"] is True
    assert detail["failure_reason"] is None


def test_unavailable_archive_explanation_is_not_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    relevant = "https://official.example/alpha-status"
    stamp = "2025-10-20T00:00:00"
    corpus = make_corpus(tmp_path / "corpus.sqlite", [(relevant, stamp, "q1")])
    sources = {relevant: stamp}
    scoped = audit.build_scoped_index({"btf2:q1": sources})
    monkeypatch.setattr(
        audit.TimeVault,
        "fetch_page",
        lambda *_args, **_kwargs: {
            "archived_at": None,
            "text": "No archived version exists; unavailable pre-cutoff.",
        },
    )
    try:
        detail = audit.audit_one(
            {
                "qid": "btf2:q1",
                "proxy_kind": "question_source_set",
                "load_bearing_claim": "alpha status",
                "queries": ["alpha status"],
            },
            {"id": "btf2:q1", "as_of": "2025-10-23T00:00:00+00:00"},
            sources,
            {relevant: stamp},
            scoped,
            corpus,
            top_k=25,
            secondary_k=8,
            fetch_readability=True,
        )
    finally:
        scoped.close()

    assert detail["global_discoverable_top25"] is True
    assert detail["wayback_readable"] is False
    assert detail["wayback_error"] is None


def test_wilson_interval_contains_observed_rate() -> None:
    lo, hi = audit.wilson(18, 20)
    assert lo < 0.9 < hi
