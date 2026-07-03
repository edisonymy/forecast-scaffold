"""Regression tests from the pre-publication adversarial review: every finding that
changed behavior is pinned here so it can't silently regress."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forecast_scaffold.core import (
    AlreadyResolved,
    ForecastRecord,
    Journal,
    aggregate_binary,
    calibration_report,
    geo_mean_odds,
    load_config,
    main,
    percentiles_to_cdf,
    trimmed_mean,
    validate_cdf,
    validate_percentiles,
    validate_record,
)

WIDE = {"10": 10.0, "25": 25.0, "50": 50.0, "75": 75.0, "90": 90.0}


def make_record(**overrides: object) -> ForecastRecord:
    base: dict[str, object] = {
        "question": "Will X happen by 2026-12-31?",
        "resolution_criterion": "official announcement of X before the date",
        "resolve_by": "2026-12-31",
        "probability": 0.62,
        "reference_class": "similar events since 2000",
    }
    base.update(overrides)
    return ForecastRecord(**base)  # type: ignore[arg-type]


# ---------------------------------------------------- annul guards (math-1 / skills-2)


def test_annul_after_resolve_requires_overwrite(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl")
    record = make_record()
    journal.append(record)
    journal.resolve(record.id, True)
    with pytest.raises(AlreadyResolved):
        journal.annul(record.id, "changed my mind")
    stored = journal.get(record.id)
    assert stored is not None and stored.resolution is not None
    assert stored.resolution["outcome"] is True  # data survived
    assert journal.annul(record.id, "genuinely ambiguous", overwrite=True)


def test_annul_after_resolve_cli_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = str(tmp_path / "j.jsonl")
    main([
        "record", "--question", "Q?", "--probability", "0.62", "--resolve-by", "2026-12-31",
        "--criterion", "c", "--journal", journal,
    ])
    record_id = capsys.readouterr().out.strip()
    assert main(["resolve", "--id", record_id, "--outcome", "true", "--journal", journal]) == 0
    assert main(["resolve", "--id", record_id, "--annul", "--journal", journal]) == 3
    assert main([
        "resolve", "--id", record_id, "--annul", "--overwrite", "--journal", journal,
    ]) == 0


# ---------------------------------------------------- date hygiene (math-2)


def test_non_iso_resolve_by_is_an_error() -> None:
    record = make_record(resolve_by="2026-1-5")
    errors, _ = validate_record(record)
    assert any("ISO date" in e for e in errors)
    errors, _ = validate_record(make_record(resolve_by="Dec 2026"))
    assert any("ISO date" in e for e in errors)


def test_due_parses_dates_not_strings(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl")
    # Legacy malformed record (bypasses validation): must surface as due, not vanish.
    record = make_record()
    record.resolve_by = "2026-1-5"
    journal.append(record)
    assert len(journal.due("2026-06-01")) == 1


def test_due_today_must_be_iso(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["due", "--today", "Dec 2026"]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


# ---------------------------------------------------- binary outcome typing (math-3)


def test_binary_resolve_rejects_non_bool(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl")
    record = make_record()
    journal.append(record)
    with pytest.raises(ValueError, match="true/false"):
        journal.resolve(record.id, 0.7)
    with pytest.raises(ValueError, match="true/false"):
        journal.resolve(record.id, "ture")


def test_binary_resolve_cli_rejects_garbage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = str(tmp_path / "j.jsonl")
    main([
        "record", "--question", "Q?", "--probability", "0.62", "--resolve-by", "2026-12-31",
        "--criterion", "c", "--journal", journal,
    ])
    record_id = capsys.readouterr().out.strip()
    assert main(["resolve", "--id", record_id, "--outcome", "0.7", "--journal", journal]) == 2
    # common truthy spellings still work
    assert main(["resolve", "--id", record_id, "--outcome", "1.0", "--journal", journal]) == 0


# ---------------------------------------------------- config wiring (math-4 / skills-3)


def test_config_journal_path_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FORECAST_JOURNAL", raising=False)
    (tmp_path / "forecast.toml").write_text(
        '[journal]\npath = "custom.jsonl"\n', encoding="utf-8"
    )
    assert main([
        "record", "--question", "Q?", "--probability", "0.62", "--resolve-by", "2026-12-31",
        "--criterion", "c",
    ]) == 0
    capsys.readouterr()
    assert (tmp_path / "custom.jsonl").exists()


def test_config_aggregation_method_is_honored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    toml = tmp_path / "median.toml"
    toml.write_text('[aggregation]\nmethod = "median"\n', encoding="utf-8")
    assert main(["--config", str(toml), "aggregate", "--draws", "0.2,0.9,0.4", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["probability"] == pytest.approx(0.4)
    assert "median" in result["aggregation"]


def test_explicit_missing_config_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(FileNotFoundError):
        load_config("no-such-file.toml")
    assert main(["--config", "no-such-file.toml", "config"]) == 2
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------- MC/numeric recording (skills-1)


def test_record_multiple_choice_via_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = str(tmp_path / "j.jsonl")
    assert main([
        "record", "--question", "Which option wins?", "--type", "multiple_choice",
        "--options", "A,B,C", "--probabilities", "0.5,0.3,0.2",
        "--resolve-by", "2026-12-31", "--criterion", "per the official result",
        "--journal", journal,
    ]) == 0
    capsys.readouterr()
    record = Journal(journal).all()[0]
    assert record.options == ["A", "B", "C"]
    assert record.probabilities == [0.5, 0.3, 0.2]
    assert record.forecast_at is not None


def test_record_numeric_via_flags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = str(tmp_path / "j.jsonl")
    assert main([
        "record", "--question", "How many by 2026-12-31?", "--type", "numeric",
        "--percentiles", "10:5,25:8,50:12,75:20,90:35",
        "--resolve-by", "2026-12-31", "--criterion", "per the published count",
        "--journal", journal,
    ]) == 0
    capsys.readouterr()
    record = Journal(journal).all()[0]
    assert record.percentiles == {"10": 5.0, "25": 8.0, "50": 12.0, "75": 20.0, "90": 35.0}


def test_record_via_record_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = str(tmp_path / "j.jsonl")
    payload = json.dumps({
        "question": "Q?", "probability": 0.62, "resolve_by": "2026-12-31",
        "resolution_criterion": "c",
    })
    assert main(["record", "--record-json", payload, "--journal", journal]) == 0
    capsys.readouterr()
    assert Journal(journal).all()[0].probability == 0.62
    assert main(["record", "--record-json", "{not json", "--journal", journal]) == 2


def test_record_bad_probability_is_a_cli_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["record", "--question", "Q?", "--probability", "1.5"]) == 2
    assert "error" in capsys.readouterr().err


# ---------------------------------------------------- calibration direction (math-10, math-5)


def test_direction_underconfident() -> None:
    records = [make_record(probability=0.6).resolve(True) for _ in range(6)]
    assert calibration_report(records).direction == "under-confident"


def test_direction_confident_no_is_overconfident() -> None:
    # p=0.05 on events that occur 20% of the time: overconfident in NO.
    records = [make_record(probability=0.05).resolve(i < 2) for i in range(10)]
    report = calibration_report(records)
    assert report.direction == "over-confident"
    assert "were right 80%" in report.high_confidence
    assert "claimed ~95%" in report.high_confidence


def test_direction_boundary_gap_is_well_calibrated() -> None:
    records = [make_record(probability=0.7).resolve(i < 6) for i in range(10)]
    report = calibration_report(records)  # folded gap exactly 0.1
    assert report.direction == "well-calibrated"


def test_high_confidence_note_exact_numbers() -> None:
    records = [make_record(probability=0.9).resolve(i < 2) for i in range(6)]
    report = calibration_report(records)
    assert "were right 33%" in report.high_confidence
    assert "claimed ~90%" in report.high_confidence


# ---------------------------------------------------- aggregation guards (math-5/6)


def test_out_of_range_draws_raise() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        trimmed_mean([55.0])
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        aggregate_binary([5.0, 7.0])


def test_geo_mean_odds_edge_inputs_and_no_drop() -> None:
    assert geo_mean_odds([0.0, 1.0]) == pytest.approx(0.5)  # eps-clamped, symmetric
    # n=4, no dropping: odds 1*1*1*9 -> 9**0.25 = 1.732 -> p = 0.634
    assert geo_mean_odds([0.5, 0.5, 0.5, 0.9], drop_extremes=False) == pytest.approx(
        0.634, abs=1e-3
    )
    assert geo_mean_odds([0.5, 0.5, 0.5, 0.9]) == pytest.approx(0.5)


def test_crowd_beyond_clamp_band_is_clamped() -> None:
    p, desc = aggregate_binary([0.97, 0.96, 0.97], crowd=0.999)
    assert p == pytest.approx(0.98)
    assert "clamped" in desc


# ---------------------------------------------------- CDF guards (math-5/7/8/9)


def test_extra_nonmonotone_percentile_key_is_caught() -> None:
    bad = dict(WIDE, **{"80": 5.0})
    assert any("strictly increasing" in e for e in validate_percentiles(bad))
    assert any("not a number" in e for e in validate_percentiles(dict(WIDE, foo=1.0)))


def test_zero_point_inside_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="zero_point"):
        percentiles_to_cdf(WIDE, 0.0, 100.0, zero_point=50.0)


def test_log_scaled_cdf_pinned_values() -> None:
    logged = percentiles_to_cdf(
        WIDE, 0.0, 100.0, lower_open=True, upper_open=True, zero_point=-10.0
    )
    assert logged[100] == pytest.approx(0.2097204209, abs=1e-9)
    assert logged[50] == pytest.approx(0.0509710894, abs=1e-9)


def test_single_open_bound_distortion_is_pinned() -> None:
    # Inherited from the platform standardization; a future "fix" must be deliberate.
    lower_open = percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True)
    assert lower_open[100] == pytest.approx(0.4744736842, abs=1e-9)


def test_validate_cdf_catches_bin_cap_violation() -> None:
    good = percentiles_to_cdf(WIDE, 0.0, 100.0)
    bad = list(good)
    bad[101] = bad[100] + 0.3
    assert any("above cap" in e for e in validate_cdf(bad))


# ---------------------------------------------------- export of annulled records (math-12)


def test_annulled_exports_as_resolved_with_flag(tmp_path: Path) -> None:
    from forecast_scaffold.core import to_decision_record

    record = make_record().annul("ambiguous")
    out = to_decision_record(record)
    assert out["status"] == "resolved"
    assert "annulled: true" in out["assumptions"]
    assert out["resolution"]["realized"] is None
