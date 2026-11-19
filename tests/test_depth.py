"""bench/depth.py: the drift rule and the replay of the 2026-09-22 model switch."""

import json

from bench.depth import drift, main, medians, research_calls


def _trace(path, at, *, seconds, sources, model="m", ok=True):
    call = {"mode": "research", "stage": "angle_research", "ok": ok, "model": model,
            "seconds": seconds,
            "sources": [f"s{i}" for i in range(sources)]}
    path.write_text(json.dumps({"forecast_at": at, "calls": [
        call, {"mode": "research", "stage": "supervisor", "ok": True, "seconds": 1.0}]}),
        encoding="utf-8")


def test_medians_skip_measures_a_call_never_recorded():
    row = medians([{"seconds": 10, "sources": ["a"]}, {"seconds": 30, "sources": []}])
    assert row["seconds"] == 20 and row["sources"] == 0.5
    assert "turns" not in row and "output tokens" not in row


def test_drift_flags_both_directions_and_ignores_missing_measures():
    baseline = {"seconds": 300.0, "sources": 9.0, "turns": 18.0}
    assert drift({"seconds": 290.0, "sources": 8.0}, baseline, 1.5) == {}
    moved = drift({"seconds": 80.0, "sources": 20.0, "output tokens": 1.0}, baseline, 1.5)
    assert set(moved) == {"seconds", "sources"} and moved["seconds"] < 1 < moved["sources"]


def test_main_exits_1_when_runs_shrink_and_0_when_steady(tmp_path, capsys):
    for day in range(1, 11):  # baseline window
        _trace(tmp_path / f"b{day}.json", f"2026-09-{day:02d}T12:00:00+00:00",
               seconds=300, sources=9)
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    _trace(tmp_path / "failed.json", "2026-09-25T12:00:00+00:00", seconds=1, sources=0,
           ok=False)
    args = ["--traces", str(tmp_path), "--as-of", "2026-09-30T00:00:00+00:00"]
    for day in range(20, 26):
        _trace(tmp_path / f"r{day}.json", f"2026-09-{day}T12:00:00+00:00",
               seconds=290, sources=9, model="new")
    assert main(args) == 0
    for day in range(20, 26):
        _trace(tmp_path / f"r{day}.json", f"2026-09-{day}T12:00:00+00:00",
               seconds=80, sources=5, model="new")
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "DRIFT seconds: x0.27" in out and "  new" in out
    assert sum(1 for _ in research_calls(tmp_path)) == 16  # failed + broken skipped
