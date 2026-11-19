"""bench/canary.py: per-run scores by model, paired on the same question."""

import json
import math
from pathlib import Path

from bench.canary import main, paired_deltas, run_models, run_scores


def _call(index: int, model: str, ok: bool = True) -> dict:
    return {"mode": "research", "stage": "angle_research", "run_index": index,
            "model": model, "ok": ok}


def _row(tmp_path: Path, qid: int, calls: list[dict], **fields) -> dict:
    trace = tmp_path / "traces" / f"{qid}.json"
    trace.parent.mkdir(exist_ok=True)
    trace.write_text(json.dumps({"calls": calls}), encoding="utf-8")
    return {"source": {"question_id": qid}, "trace_path": f"traces/{qid}.json", **fields}


def test_run_models_takes_the_final_successful_call_of_each_run():
    trace = {"calls": [_call(1, "old"), _call(2, "new", ok=False), _call(2, "new"),
                       _call(3, "old"), {"mode": "other", "ok": True, "run_index": 4}]}
    assert run_models(trace) == ["old", "new", "old"]


def test_run_scores_binary_and_multiple_choice_are_log_scores_in_points():
    binary = {"question_type": "binary", "raw_draws": [0.8, 0.5]}
    assert run_scores(binary, True) == [100 * math.log(0.8), 100 * math.log(0.5)]
    assert run_scores(binary, False)[0] == 100 * math.log(1 - 0.8)
    choice = {"question_type": "multiple_choice",
              "run_probabilities": [{"A": 0.7, "B": 0.3}, {"A": 0.0, "B": 1.0}]}
    assert run_scores(choice, "A") == [100 * math.log(0.7), 100 * math.log(0.001)]
    assert run_scores({"question_type": "numeric"}, 3.0) is None  # no scaling: unscorable


def test_paired_deltas_compare_the_candidate_with_run_one_and_skip_misaligned(tmp_path: Path):
    rows = [
        # candidate better on a YES
        _row(tmp_path, 1, [_call(1, "old"), _call(2, "new"), _call(3, "old")],
             question_type="binary", raw_draws=[0.5, 0.8, 0.5]),
        # a run failed after the trace was written: two values, three models -> left out
        _row(tmp_path, 2, [_call(1, "old"), _call(2, "new"), _call(3, "old")],
             question_type="binary", raw_draws=[0.5, 0.8]),
        # one model only -> nothing to pair
        _row(tmp_path, 3, [_call(1, "old"), _call(2, "old")],
             question_type="binary", raw_draws=[0.5, 0.6]),
        # unresolved
        _row(tmp_path, 4, [_call(1, "old"), _call(2, "new")],
             question_type="binary", raw_draws=[0.5, 0.9]),
    ]
    journal = tmp_path / "forecasts.jsonl"
    journal.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n",
                       encoding="utf-8")
    deltas = paired_deltas(journal, {1: True, 2: True, 3: True})
    assert list(deltas) == ["new"]
    assert deltas["new"] == [100 * math.log(0.8) - 100 * math.log(0.5)]


def test_main_reports_and_handles_an_empty_sample(tmp_path: Path, capsys):
    journal, overlay = tmp_path / "forecasts.jsonl", tmp_path / "resolutions.jsonl"
    journal.write_text("", encoding="utf-8")
    overlay.write_text("", encoding="utf-8")
    assert main(["--journal", str(journal), "--overlay", str(overlay)]) == 0
    assert "no resolved question" in capsys.readouterr().out
    row = _row(tmp_path, 1, [_call(1, "old"), _call(2, "new")],
               question_type="binary", raw_draws=[0.5, 0.8])
    journal.write_text(json.dumps(row) + "\n", encoding="utf-8")
    overlay.write_text(json.dumps({"question_id": 1, "status": "resolved",
                                   "outcome": True}) + "\n", encoding="utf-8")
    assert main(["--journal", str(journal), "--overlay", str(overlay)]) == 0
    assert "new: +47.0 points per question against the incumbent, n=1" in capsys.readouterr().out
