"""Paired per-question Brier comparison section in bench/report.py.

Imports ``report`` the same way tests/test_providers.py does: bench/ is not a
package, so it's added to sys.path directly rather than imported as
``bench.report``.
"""

from __future__ import annotations

import json
import math
import re
import statistics as st
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import report  # noqa: E402

PAIR_LINE_RE = re.compile(
    r"^- (?P<a>\w+) - (?P<b>\w+): mean (?P<mean>[+-][\d.]+) \xb1(?P<se>[\d.]+) "
    r"\(n=(?P<n>\d+), (?P=a) wins (?P<wins_a>\d+)/\d+, (?P=b) wins (?P<wins_b>\d+)/\d+, "
    r"ties (?P<ties>\d+)\)$"
)


def _write_set(tmp_path: Path, name: str, specs: list[dict]) -> Path:
    set_path = tmp_path / f"{name}.jsonl"
    set_path.write_text("\n".join(json.dumps(s) for s in specs), encoding="utf-8")
    return set_path


def _run_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str,
    specs: list[dict], rows: list[dict],
) -> tuple[Path, int]:
    set_path = _write_set(tmp_path, name, specs)
    results_dir = tmp_path / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / f"{name}.results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )
    monkeypatch.setattr(report, "RESULTS_DIR", results_dir)
    return results_dir, report.main([str(set_path)])


def _pair_lines(text: str) -> list[re.Match]:
    return [m for line in text.splitlines() if (m := PAIR_LINE_RE.match(line))]


class TestPairedBrierComparison:
    def test_hand_computed_mean_diff_and_win_loss_with_exclusions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # q1-q3: resolved, both tiers forecast -> the 3 questions the paired stat is
        # computed over. Brier = (p - outcome)^2:
        #   q1 outcome=1: high (0.9-1)^2=0.01, zero (0.5-1)^2=0.25 -> d=-0.24 (high wins)
        #   q2 outcome=0: high (0.2-0)^2=0.04, zero (0.5-0)^2=0.25 -> d=-0.21 (high wins)
        #   q3 outcome=1: high (0.3-1)^2=0.49, zero (0.5-1)^2=0.25 -> d=+0.24 (zero wins)
        # q4: resolved but ONLY "high" has a row -> must be excluded (no zero to pair against).
        # q5: both tiers have a row but the set carries no "resolution" -> must be excluded.
        specs = [
            {"id": "q1", "resolution": 1},
            {"id": "q2", "resolution": 0},
            {"id": "q3", "resolution": 1},
            {"id": "q4", "resolution": 1},
            {"id": "q5"},  # unresolved
        ]
        rows = [
            {"qid": "q1", "tier": "high", "probability": 0.9},
            {"qid": "q2", "tier": "high", "probability": 0.2},
            {"qid": "q3", "tier": "high", "probability": 0.3},
            {"qid": "q4", "tier": "high", "probability": 0.7},
            {"qid": "q5", "tier": "high", "probability": 0.6},
            {"qid": "q1", "tier": "zero", "probability": 0.5},
            {"qid": "q2", "tier": "zero", "probability": 0.5},
            {"qid": "q3", "tier": "zero", "probability": 0.5},
            # deliberately no q4 row for "zero"
            {"qid": "q5", "tier": "zero", "probability": 0.5},
        ]
        results_dir, rc = _run_report(monkeypatch, tmp_path, "s", specs, rows)
        assert rc == 0
        text = (results_dir / "s.report.md").read_text(encoding="utf-8")

        assert "## paired Brier comparison" in text
        pairs = _pair_lines(text)
        assert len(pairs) == 1
        m = pairs[0]
        assert m["a"] == "high" and m["b"] == "zero"
        assert m["n"] == "3"  # q4 (missing zero) and q5 (unresolved) excluded
        diffs = [-0.24, -0.21, 0.24]
        assert float(m["mean"]) == pytest.approx(st.mean(diffs), abs=1e-4)
        assert float(m["se"]) == pytest.approx(
            st.stdev(diffs) / math.sqrt(len(diffs)), abs=1e-4
        )
        assert m["wins_a"] == "2"  # high strictly lower Brier on q1, q2
        assert m["wins_b"] == "1"  # zero strictly lower Brier on q3
        assert m["ties"] == "0"

    def test_no_overlap_between_tiers_skips_pair_without_crashing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        specs = [
            {"id": "q1", "resolution": 1},
            {"id": "q2", "resolution": 0},
        ]
        rows = [
            {"qid": "q1", "tier": "high", "probability": 0.9},
            {"qid": "q2", "tier": "medium", "probability": 0.4},
        ]
        results_dir, rc = _run_report(monkeypatch, tmp_path, "s", specs, rows)
        assert rc == 0  # no crash on an n=0 pair
        text = (results_dir / "s.report.md").read_text(encoding="utf-8")

        # resolution scoring still runs (each tier has its own scored row)...
        assert "## resolution scoring" in text
        # ...but the paired section is skipped entirely: the only possible pair
        # (high, medium) has zero overlapping resolved qids.
        assert "## paired Brier comparison" not in text
        assert _pair_lines(text) == []

    def test_no_resolutions_at_all_omits_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        specs = [{"id": "q1"}, {"id": "q2"}]  # no resolution field anywhere
        rows = [
            {"qid": "q1", "tier": "high", "probability": 0.9},
            {"qid": "q2", "tier": "zero", "probability": 0.4},
        ]
        results_dir, rc = _run_report(monkeypatch, tmp_path, "s", specs, rows)
        assert rc == 0
        text = (results_dir / "s.report.md").read_text(encoding="utf-8")
        assert "## resolution scoring" not in text
        assert "## paired Brier comparison" not in text
