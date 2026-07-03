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

    assert run(journal, "score", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["n"] == 1
    assert report["brier"] == pytest.approx((0.62 - 1.0) ** 2)


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
    assert json.loads(capsys.readouterr().out)["n"] == 0


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
