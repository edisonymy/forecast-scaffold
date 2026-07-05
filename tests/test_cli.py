"""Every CLI subcommand end-to-end against a temp journal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forecast_scaffold.core import Journal, main

RECORD_ARGS = [
    "record",
    "--question", "Will X happen by 2026-12-31?",
    "--probability", "0.62",
    "--resolve-by", "2026-12-31",
    "--criterion", "official announcement of X",
    "--reference-class", "similar events since 2000",
]


def run(journal: Path, *args: str) -> int:
    return main([*args, "--journal", str(journal)])


def test_record_and_score_loop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    assert run(journal, *RECORD_ARGS) == 0
    record_id = capsys.readouterr().out.strip()

    records = Journal(journal).all()
    assert len(records) == 1
    assert records[0].id == record_id
    assert records[0].forecast_at is not None  # probability committed -> timestamped

    assert run(journal, "resolve", "--id", record_id, "--outcome", "yes") == 0
    assert run(journal, "score") == 0
    out = capsys.readouterr().out
    assert "N = 1" in out
    assert "pooled (all groups)" in out

    assert run(journal, "score", "--json") == 0
    grouped = json.loads(capsys.readouterr().out)
    assert grouped["keys"] == ["scaffold_version", "blind"]
    assert len(grouped["groups"]) == 1
    report = grouped["groups"][0]["report"]
    assert report["n"] == 1
    assert report["brier"] == pytest.approx((0.62 - 1.0) ** 2)
    assert grouped["pooled"]["n"] == 1


def test_record_warns_on_round_number(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    args = list(RECORD_ARGS)
    args[args.index("0.62")] = "0.35"
    assert run(journal, *args) == 0
    assert "5%-grid" in capsys.readouterr().err


def test_record_missing_criterion_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    code = run(
        journal, "record", "--question", "Will it rain?", "--probability", "0.62",
        "--resolve-by", "2026-12-31",
    )
    assert code == 2
    assert "resolution_criterion" in capsys.readouterr().err
    assert not journal.exists()


def test_record_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    assert run(journal, *RECORD_ARGS, "--dry-run") == 0
    record = json.loads(capsys.readouterr().out)
    assert record["question"].startswith("Will X")
    assert not journal.exists()


def test_resolve_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    run(journal, *RECORD_ARGS)
    record_id = capsys.readouterr().out.strip()

    assert run(journal, "resolve", "--id", record_id, "--outcome", "true") == 0
    assert run(journal, "resolve", "--id", record_id, "--outcome", "false") == 3
    assert "already resolved" in capsys.readouterr().err
    assert run(journal, "resolve", "--id", record_id, "--outcome", "false", "--overwrite") == 0
    assert run(journal, "resolve", "--id", "missing", "--outcome", "true") == 2


def test_annul_excludes_from_scoring(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    run(journal, *RECORD_ARGS)
    record_id = capsys.readouterr().out.strip()

    assert run(journal, "resolve", "--id", record_id, "--annul", "--note", "source vanished") == 0
    capsys.readouterr()
    assert run(journal, "score", "--json") == 0
    grouped = json.loads(capsys.readouterr().out)
    # The annulled record still falls into a group (it has a scaffold_version/blind label),
    # but it is unscorable, so that group's own report shows n=0 just like the pooled line.
    assert len(grouped["groups"]) == 1
    assert grouped["groups"][0]["report"]["n"] == 0
    assert grouped["pooled"]["n"] == 0


def test_due_lists_past_dates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    run(journal, *RECORD_ARGS)
    capsys.readouterr()
    assert run(journal, "due", "--today", "2027-01-01", "--json") == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert run(journal, "due", "--today", "2026-01-01") == 0
    assert "Nothing due" in capsys.readouterr().out


def test_aggregate_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["aggregate", "--draws", "0.5,0.6,0.7", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["probability"] == pytest.approx(0.6)

    assert main(["aggregate", "--draws", "0.5,0.6,0.7", "--crowd", "0.4", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["probability"] == pytest.approx(0.5)


def test_validate_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", "--probability", "0.5"]) == 0
    assert "50%" in capsys.readouterr().out

    bad = json.dumps({"question": "Q?", "status": "open"})
    assert main(["validate", "--record-json", bad]) == 2

    assert main(["validate", "--record-json", "{not json"]) == 2


def test_config_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config"]) == 0
    config = json.loads(capsys.readouterr().out)
    assert config["clamp"]["min"] == 0.02


# ------------------------------------------------------------------ score --by


def _record_and_resolve(
    journal: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    outcome: str,
    extra_json: dict[str, object],
) -> None:
    """Record via --record-json (to set fields --record has no flags for) and resolve it."""
    base = {
        "question": f"Will X happen? ({extra_json})",
        "resolution_criterion": "official announcement of X",
        "resolve_by": "2026-12-31",
        "probability": 0.62,
        "reference_class": "similar events since 2000",
    }
    base.update(extra_json)
    assert main(["record", "--record-json", json.dumps(base), "--journal", str(journal)]) == 0
    record_id = capsys.readouterr().out.strip()
    assert run(journal, "resolve", "--id", record_id, "--outcome", outcome) == 0
    capsys.readouterr()


def test_score_by_default_separates_scaffold_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "j.jsonl"
    _record_and_resolve(journal, capsys, outcome="true", extra_json={"scaffold_version": "0.1.0"})
    _record_and_resolve(journal, capsys, outcome="false", extra_json={"scaffold_version": "0.2.0"})

    assert run(journal, "score", "--json") == 0
    grouped = json.loads(capsys.readouterr().out)
    assert grouped["keys"] == ["scaffold_version", "blind"]
    labels = {g["group"]["scaffold_version"] for g in grouped["groups"]}
    assert labels == {"0.1.0", "0.2.0"}
    assert all(g["report"]["n"] == 1 for g in grouped["groups"])
    assert grouped["pooled"]["n"] == 2


def test_score_by_blind_vs_sighted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    _record_and_resolve(
        journal, capsys, outcome="true",
        extra_json={"crowd": {"value": 0.5, "shown_to_agent": False}},
    )
    _record_and_resolve(
        journal, capsys, outcome="true",
        extra_json={"crowd": {"value": 0.5, "shown_to_agent": True}},
    )
    _record_and_resolve(journal, capsys, outcome="true", extra_json={})  # no crowd -> unknown

    assert run(journal, "score", "--json") == 0
    grouped = json.loads(capsys.readouterr().out)
    labels = {g["group"]["blind"] for g in grouped["groups"]}
    assert labels == {"blind", "sighted", "unknown"}
    assert grouped["pooled"]["n"] == 3


def test_score_by_model_and_effort(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    _record_and_resolve(
        journal, capsys, outcome="true", extra_json={"model": "opus", "effort": "high"}
    )
    _record_and_resolve(
        journal, capsys, outcome="false", extra_json={"model": "sonnet", "effort": "low"}
    )

    assert run(journal, "score", "--by", "model,effort", "--json") == 0
    grouped = json.loads(capsys.readouterr().out)
    assert grouped["keys"] == ["model", "effort"]
    groups = {(g["group"]["model"], g["group"]["effort"]) for g in grouped["groups"]}
    assert groups == {("opus", "high"), ("sonnet", "low")}


def test_score_by_rejects_unknown_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    assert run(journal, "score", "--by", "not_a_real_key") == 2
    assert "unknown --by key" in capsys.readouterr().err


def test_score_by_small_n_wording_per_group(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "j.jsonl"
    _record_and_resolve(journal, capsys, outcome="true", extra_json={"model": "opus"})
    _record_and_resolve(journal, capsys, outcome="false", extra_json={"model": "sonnet"})

    assert run(journal, "score", "--by", "model") == 0
    out = capsys.readouterr().out
    # Each group has n=1 (< MIN_CALIBRATION_N) -> the noise wording appears once per group,
    # plus once more for the pooled line (n=2, still < MIN_CALIBRATION_N).
    assert out.count("noise") == 3
