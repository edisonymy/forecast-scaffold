"""Resolutions overlay sync (bench/sync_resolutions.py): pure helpers, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import sync_resolutions as sr  # noqa: E402


class TestNormalizeOutcome:
    def test_binary(self) -> None:
        assert sr.normalize_outcome("binary", "yes") == (True, False)
        assert sr.normalize_outcome("binary", "no") == (False, False)
        assert sr.normalize_outcome("binary", "annulled") == (None, True)
        assert sr.normalize_outcome("binary", "ambiguous") == (None, True)

    def test_continuous_keeps_out_of_range_strings(self) -> None:
        assert sr.normalize_outcome("numeric", "993728.0") == (993728.0, False)
        assert sr.normalize_outcome("discrete", "above_upper_bound") == ("above_upper_bound", False)

    def test_multiple_choice_label_and_missing(self) -> None:
        assert sr.normalize_outcome("multiple_choice", "Alaska") == ("Alaska", False)
        assert sr.normalize_outcome("multiple_choice", None) == (None, False)


class TestPit:
    def test_linear_scaling(self) -> None:
        fv = [i / 200 for i in range(201)]
        assert sr.pit_of(fv, 50.0, {"range_min": 0, "range_max": 100}) == 0.5
        assert sr.pit_of(fv, -5.0, {"range_min": 0, "range_max": 100}) == 0.0
        assert sr.pit_of(fv, 500.0, {"range_min": 0, "range_max": 100}) == 1.0

    def test_non_numeric_or_missing_scaling(self) -> None:
        assert sr.pit_of([0.0, 1.0], "above_upper_bound", {"range_min": 0, "range_max": 1}) is None
        assert sr.pit_of([0.0, 1.0], 0.5, None) is None
        assert sr.pit_of(None, 0.5, {"range_min": 0, "range_max": 1}) is None


class TestPostId:
    def test_from_url_or_field(self) -> None:
        url = "https://www.metaculus.com/questions/45289/"
        assert sr.post_id_of({"source": {"url": url}}) == 45289
        assert sr.post_id_of({"source": {"post_id": 7, "url": "x"}}) == 7
        assert sr.post_id_of({"source": {"url": "https://example.com/"}}) is None


class TestLoaders:
    def test_journal_latest_per_question_and_dry_runs_excluded(self, tmp_path: Path) -> None:
        j = tmp_path / "j.jsonl"
        rows = [
            {"id": "a", "forecast_at": "2026-08-01T00:00:00+00:00",
             "source": {"platform": "metaculus", "question_id": 1}},
            {"id": "b", "forecast_at": "2026-08-02T00:00:00+00:00",
             "source": {"platform": "metaculus", "question_id": 1}},
            {"id": "c", "forecast_at": "2026-08-03T00:00:00+00:00", "dry_run": True,
             "source": {"platform": "metaculus", "question_id": 1}},
            {"id": "d", "forecast_at": "2026-08-03T00:00:00+00:00",
             "source": {"platform": "manifold", "question_id": "zz"}},
        ]
        j.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
        latest = sr.load_journal(j)
        assert list(latest) == [1] and latest[1]["id"] == "b"

    def test_overlay_latest_line_wins_and_missing_file(self, tmp_path: Path) -> None:
        o = tmp_path / "r.jsonl"
        assert sr.load_overlay(o) == {}
        lines = [json.dumps({"question_id": 5, "status": "closed"}),
                 json.dumps({"question_id": 5, "status": "resolved"})]
        o.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert sr.load_overlay(o)[5]["status"] == "resolved"
