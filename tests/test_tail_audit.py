"""Pure tests for bench/tail_audit.py: the exact Poisson-binomial tail, bucketing, and
the end-to-end table on synthetic miscalibrated data (no network, no model calls)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

from tail_audit import (  # noqa: E402 - path bootstrap above
    bucket_of,
    load_forecasts,
    main,
    poisson_binomial_tail,
)


def test_poisson_binomial_tail_matches_hand_calcs() -> None:
    assert abs(poisson_binomial_tail([0.5, 0.5], 2) - 0.25) < 1e-12
    assert abs(poisson_binomial_tail([0.5, 0.5], 1) - 0.75) < 1e-12
    # P(X >= 1) = 1 - 0.9^3
    assert abs(poisson_binomial_tail([0.1, 0.1, 0.1], 1) - (1 - 0.9**3)) < 1e-12
    assert poisson_binomial_tail([0.2, 0.3], 0) == 1.0
    assert poisson_binomial_tail([0.2, 0.3], 3) == 0.0


def test_bucket_edges_cover_the_range() -> None:
    assert bucket_of(0.0) == 0
    assert bucket_of(0.049) == 0
    assert bucket_of(0.05) == 1
    assert bucket_of(0.5) == 3
    assert bucket_of(0.94) == 5
    assert bucket_of(1.0) == 6


def test_load_forecasts_last_row_wins_and_skips_router_rows(tmp_path: Path) -> None:
    rows = [
        {"qid": "q1", "tier": "zero", "run": 0, "probability": 0.10},
        {"qid": "q1", "tier": "zero", "run": 0, "probability": 0.20},  # resume overwrite
        {"qid": "q2", "tier": "auto", "run": 0, "probability": None, "router_only": True},
    ]
    path = tmp_path / "x.results.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    latest = load_forecasts([path])
    assert latest == {("q1", "zero", 0): 0.20}


def test_end_to_end_flags_an_overconfident_low_tail(tmp_path: Path, capsys) -> None:
    # 10 questions forecast at 0.03, but 3 resolved YES: calibrated tail-p should be tiny.
    specs = [{"id": f"q{i}", "resolution": 1 if i < 3 else 0,
              "crowd": {"value": 0.15}} for i in range(10)]
    set_path = tmp_path / "set.jsonl"
    set_path.write_text("\n".join(json.dumps(s) for s in specs), encoding="utf-8")
    results = [{"qid": f"q{i}", "tier": "zero", "run": 0, "probability": 0.03}
               for i in range(10)]
    results_path = tmp_path / "set.results.jsonl"
    results_path.write_text("\n".join(json.dumps(r) for r in results), encoding="utf-8")

    assert main([str(set_path), "--results", str(results_path)]) == 0
    out = capsys.readouterr().out
    assert "tier zero" in out and "teacher" in out
    tail_line = next(line for line in out.splitlines() if line.startswith("| [0.00, 0.05)"))
    tail_p = float(tail_line.rstrip("|").split("|")[-1])
    assert tail_p < 0.01  # 3 hits on ten 3% forecasts is a ~0.003 event if calibrated


def test_main_refuses_sets_without_resolutions(tmp_path: Path) -> None:
    set_path = tmp_path / "set.jsonl"
    set_path.write_text(json.dumps({"id": "q1", "crowd": None}), encoding="utf-8")
    assert main([str(set_path)]) == 1
