"""Tests for bench/freeze_prospective.py — the prospective-freeze preregistration tool.

No network: a stub client returns scripted open-post listings and post details, following the
method-replacement pattern in tests/test_bot_client.py. Covers freeze (schema-complete rows,
deterministic order, non-binary skip, overwrite refusal) and resolve (fills only resolved
questions, sets the annulled flag, leaves open ones untouched, idempotent byte-for-byte).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bot"))

import freeze_prospective  # noqa: E402


def make_question(qid: int, qtype: str = "binary", **extra: Any) -> dict[str, Any]:
    q = {
        "id": qid,
        "type": qtype,
        "title": f"Q{qid}?",
        "resolution_criteria": f"Resolves YES per source {qid}.",
        "fine_print": f"Fine print {qid}.",
        "description": f"Background for question {qid}.",
        "scheduled_resolve_time": "2026-09-01T00:00:00Z",
        "scheduled_close_time": "2026-08-15T00:00:00Z",
        "status": "open",
    }
    q.update(extra)
    return q


def make_post(pid: int, question: dict[str, Any]) -> dict[str, Any]:
    """A single-question post (MetaculusClient.questions_of reads post['question'])."""
    return {"id": pid, "title": f"Post {pid}", "question": question}


class StubClient:
    """Replaces MetaculusClient for freeze/resolve: scripted listings and post details."""

    def __init__(
        self,
        posts_by_slug: dict[str, list[dict[str, Any]]] | None = None,
        details: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self.posts_by_slug = posts_by_slug or {}
        self.details = details or {}
        self.detail_calls: list[int] = []

    def open_posts(self, slug: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.posts_by_slug.get(slug, []))

    def post_detail(self, post_id: int) -> dict[str, Any]:
        self.detail_calls.append(post_id)
        return self.details[post_id]


# -- freeze --------------------------------------------------------------------------------

def test_freeze_writes_schema_complete_rows(tmp_path: Path) -> None:
    # Posts intentionally out of id order to prove the output is sorted deterministically.
    client = StubClient({"season": [make_post(10, make_question(2)),
                                    make_post(11, make_question(1))]})
    out = tmp_path / "prospective.jsonl"
    summary = freeze_prospective.freeze(client, "season", out, limit=100)

    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["metaculus:1", "metaculus:2"]  # sorted by id
    assert summary["n_frozen"] == 2
    for r in rows:
        # schema-complete: every standard key present, plus the freeze additions
        for key in ("id", "source", "url", "question", "background", "criteria",
                    "resolve_by", "crowd", "frozen_at", "resolution"):
            assert key in r, key
        assert r["resolution"] is None          # preregistered blank
        assert r["frozen_at"]                    # full ISO timestamp, present
        assert r["frozen_at"].endswith("Z")
        assert r["crowd"]["value"] is None       # bot token: no human crowd -> null, not a failure
        assert r["crowd"]["at"] == r["frozen_at"]
        assert "## Fine print" in r["criteria"]  # fine print folded into the contract
        assert r["resolve_by"] == "2026-09-01"


def test_freeze_skips_non_binary(tmp_path: Path) -> None:
    client = StubClient({"season": [make_post(10, make_question(1)),
                                    make_post(11, make_question(2, qtype="numeric"))]})
    out = tmp_path / "p.jsonl"
    summary = freeze_prospective.freeze(client, "season", out)

    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["metaculus:1"]  # numeric question dropped
    assert summary["skipped_nonbinary"] == 1


def test_freeze_dedupes_cross_listed_questions(tmp_path: Path) -> None:
    shared = make_question(1)
    client = StubClient({
        "season": [make_post(10, shared)],
        "minibench": [make_post(10, shared)],  # same post cross-listed
    })
    out = tmp_path / "p.jsonl"
    summary = freeze_prospective.freeze(client, "season,minibench", out)
    assert summary["n_frozen"] == 1


def test_freeze_refuses_overwrite_without_force(tmp_path: Path) -> None:
    client = StubClient({"season": [make_post(10, make_question(1))]})
    out = tmp_path / "p.jsonl"
    freeze_prospective.freeze(client, "season", out)
    with pytest.raises(FileExistsError):
        freeze_prospective.freeze(client, "season", out)
    # --force is the explicit escape hatch
    freeze_prospective.freeze(client, "season", out, force=True)


# -- resolve -------------------------------------------------------------------------------

def _freeze_four(tmp_path: Path) -> Path:
    posts = [make_post(100, make_question(1)), make_post(101, make_question(2)),
             make_post(102, make_question(3)), make_post(103, make_question(4))]
    freeze_prospective.freeze(StubClient({"season": posts}), "season", tmp_path / "p.jsonl")
    return tmp_path / "p.jsonl"


def test_resolve_fills_only_resolved_and_flags_annulled(tmp_path: Path) -> None:
    out = _freeze_four(tmp_path)
    details = {
        100: make_post(100, make_question(1, resolution="yes")),
        101: make_post(101, make_question(2, resolution="no")),
        102: make_post(102, make_question(3, resolution=None)),        # still open
        103: make_post(103, make_question(4, resolution="annulled")),
    }
    summary = freeze_prospective.resolve(StubClient(details=details), out)
    assert summary == {"n_frozen": 4, "n_resolved": 2, "n_open": 1,
                       "n_annulled": 1, "n_errors": 0}

    rows = {json.loads(ln)["question_id"]: json.loads(ln)
            for ln in out.read_text(encoding="utf-8").splitlines()}
    assert rows[1]["resolution"] == 1
    assert rows[2]["resolution"] == 0
    assert rows[3]["resolution"] is None and "annulled" not in rows[3]   # open untouched
    assert rows[4]["resolution"] is None and rows[4]["annulled"] is True
    # frozen fields survive resolution
    assert rows[1]["question"] == "Q1?" and rows[1]["frozen_at"]


def test_resolve_is_idempotent(tmp_path: Path) -> None:
    out = _freeze_four(tmp_path)
    details = {
        100: make_post(100, make_question(1, resolution="yes")),
        101: make_post(101, make_question(2, resolution="no")),
        102: make_post(102, make_question(3, resolution=None)),
        103: make_post(103, make_question(4, resolution="annulled")),
    }
    client = StubClient(details=details)
    first = freeze_prospective.resolve(client, out)
    bytes_after_first = out.read_bytes()
    second = freeze_prospective.resolve(client, out)
    assert out.read_bytes() == bytes_after_first  # byte-for-byte stable
    assert second == first


def test_resolve_leaves_all_open_file_unchanged(tmp_path: Path) -> None:
    out = _freeze_four(tmp_path)
    before = out.read_bytes()
    # Nothing has resolved yet: every question still reports resolution null.
    details = {pid: make_post(pid, make_question(qid, resolution=None))
               for pid, qid in ((100, 1), (101, 2), (102, 3), (103, 4))}
    summary = freeze_prospective.resolve(StubClient(details=details), out)
    assert summary["n_open"] == 4 and summary["n_resolved"] == 0
    assert out.read_bytes() == before  # a wholly-open file is preserved verbatim


def test_resolve_fetch_error_leaves_row_untouched(tmp_path: Path) -> None:
    out = _freeze_four(tmp_path)

    class FailingClient(StubClient):
        def post_detail(self, post_id: int) -> dict[str, Any]:
            raise freeze_prospective.MetaculusError("boom")

    summary = freeze_prospective.resolve(FailingClient(), out)
    assert summary["n_errors"] == 4
    # untouched: all still open, resolution null
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
    assert all(r["resolution"] is None for r in rows)
