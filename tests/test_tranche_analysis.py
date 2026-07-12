"""Guards for tranche memory screening and run-0-only analysis.

Every fixture is synthetic and every JSONL file lives under pytest's temporary directory;
these tests never open the live tranche artifact.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from io import StringIO
from pathlib import Path

import pytest

# bench/ is not a package (same pattern as tests/test_probe.py): CI's bare `pytest`
# does not put the repo root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench" / "analysis"))

import memory_screen  # noqa: E402
import readout_tranche1 as readout  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


class TestMemoryScreen:
    def test_run_filter_is_importable_and_missing_run_means_zero(self) -> None:
        rows = [
            {"qid": "q0", "reasoning": "I recall a relevant fact."},
            {"qid": "q1", "run": 1, "reasoning": "The event has already occurred."},
            {"qid": "q2", "run": 0, "reasoning": "No memory claim here."},
        ]

        total, candidates = memory_screen.find_candidates(rows, run=0)

        assert total == 2
        assert [row["qid"] for row, _match in candidates] == ["q0"]

    def test_cli_accepts_multiple_paths_and_run_filter(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        first = write_jsonl(tmp_path / "first.jsonl", [
            {"qid": "a0", "run": 0, "reasoning": "I remember this."},
            {"qid": "a1", "run": 1, "reasoning": "I remember this too."},
        ])
        second = write_jsonl(tmp_path / "second.jsonl", [
            {"qid": "b0", "run": 0, "reasoning": "No claim."},
        ])

        assert memory_screen.main([str(first), str(second), "--run", "0"]) == 0
        output = capsys.readouterr().out

        assert str(first) in output and str(second) in output
        assert "a0" in output and "a1" not in output
        assert "1 candidate(s) of 1 rows, run=0 only" in output
        assert "0 candidate(s) of 1 rows, run=0 only" in output

    def test_invalid_run_value_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="invalid run value"):
            memory_screen.find_candidates(
                [{"qid": "bad", "run": "not-an-index", "reasoning": "I recall it."}],
                run=0,
            )


class TestTrancheRunSelection:
    def test_only_run_zero_is_scorable_and_nonzero_cost_is_ignored(self) -> None:
        rows = [
            {"tier": "plain", "qid": "q", "run": 0, "probability": 0.2,
             "cost_usd": 1.0},
            {"tier": "high", "qid": "q", "run": 0, "probability": 0.3,
             "cost_usd": 2.0},
            {"tier": "high", "qid": "q", "run": 1, "probability": 0.99,
             "cost_usd": 99.0},
            {"tier": "angles", "qid": "q", "run": 0, "probability": 0.4,
             "cost_usd": 3.0},
        ]

        arms, costs, ignored = readout.collect_run_zero(rows, {"q": 1.0}, set())

        assert arms == {"plain": {"q": 0.2}, "high": {"q": 0.3}, "angles": {"q": 0.4}}
        assert costs == {"plain": 1.0, "high": 2.0, "angles": 3.0}
        assert ignored == Counter({"high": 1})

    def test_missing_run_is_run_zero_for_legacy_compatibility(self) -> None:
        arms, _costs, ignored = readout.collect_run_zero(
            [{"tier": "plain", "qid": "q", "probability": 0.25}],
            {"q": 0.0},
            set(),
        )

        assert arms["plain"] == {"q": 0.25}
        assert not ignored

    def test_duplicate_run_zero_cell_raises_before_overwrite(self) -> None:
        rows = [
            {"tier": "high", "qid": "q", "run": 0, "probability": 0.2},
            {"tier": "high", "qid": "q", "run": 0, "probability": 0.8},
        ]

        with pytest.raises(ValueError, match="duplicate run-0 row.*tier='high'.*qid='q'"):
            readout.collect_run_zero(rows, {"q": 1.0}, set())

    def test_duplicate_nonzero_cells_remain_unused_raw_rows(self) -> None:
        rows = [
            {"tier": "high", "qid": "q", "run": 1, "probability": 0.2},
            {"tier": "high", "qid": "q", "run": 1, "probability": 0.8},
        ]

        arms, costs, ignored = readout.collect_run_zero(rows, {"q": 1.0}, set())

        assert all(not cells for cells in arms.values())
        assert costs == {"plain": 0.0, "high": 0.0, "angles": 0.0}
        assert ignored == Counter({"high": 2})

    def test_exclusion_is_applied_to_every_arm(self) -> None:
        rows = [
            {"tier": tier, "qid": "memory-hit", "run": 0, "probability": 0.7}
            for tier in readout.ARMS
        ]

        arms, _costs, _ignored = readout.collect_run_zero(
            rows, {"memory-hit": 1.0}, {"memory-hit"}
        )

        assert all("memory-hit" not in cells for cells in arms.values())


class TestTrancheCliAndReporting:
    def test_exclude_qid_is_repeatable(self) -> None:
        args = readout.parse_args([
            "--exclude-qid", "q1", "--exclude-qid", "q2", "--exclude-qid", "q3",
        ])

        assert args.exclude_qid == ["q1", "q2", "q3"]

    def test_report_states_ignored_nonzero_count(self) -> None:
        rows = [
            {"tier": tier, "qid": "q", "run": 0, "probability": 0.5}
            for tier in readout.ARMS
        ] + [
            {"tier": "high", "qid": "q", "run": run, "probability": 0.9}
            for run in (1, 2, 3)
        ]
        stream = StringIO()

        readout.print_readout(rows, {"q": 1.0}, {"q": 0.6}, set(), stream=stream)

        output = stream.getvalue()
        assert "Ignored 3 nonzero-run row(s) (high=3); readout uses run==0 only." in output
        assert output.count("1 scorable rows") == 3

    def test_invalid_run_value_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="invalid run value"):
            readout.collect_run_zero(
                [{"tier": "high", "qid": "bad", "run": "x", "probability": 0.5}],
                {"bad": 1.0},
                set(),
            )
