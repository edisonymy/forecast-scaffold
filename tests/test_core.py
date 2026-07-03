"""Schema, journal, scoring, and aggregation — including hand-computed known values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forecast_scaffold.core import (
    DEFAULTS,
    AlreadyResolved,
    ForecastRecord,
    Journal,
    aggregate_binary,
    blend_with_crowd,
    brier_score,
    calibration_report,
    geo_mean_odds,
    load_config,
    median,
    trimmed_mean,
    validate_mc,
    validate_percentiles,
    validate_probability,
    validate_record,
)


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


# ------------------------------------------------------------------ schema


def test_roundtrip_is_lossless() -> None:
    record = make_record(
        raw_draws=[0.6, 0.62, 0.65],
        aggregation="trimmed_mean(n=3)",
        crowd={"value": 0.55, "source": "metaculus", "at": "2026-07-03T12:00:00+00:00"},
        what_would_change_my_mind=["a formal denial"],
    )
    restored = ForecastRecord.from_dict(json.loads(record.to_json()))
    assert restored == record


def test_from_dict_tolerates_unknown_keys() -> None:
    data = make_record().to_dict()
    data["some_future_field"] = "ignored"
    restored = ForecastRecord.from_dict(data)
    assert restored.question == "Will X happen by 2026-12-31?"


def test_id_is_generated_and_stable() -> None:
    record = make_record()
    assert record.id.startswith(record.created[:10])
    assert ForecastRecord.from_dict(record.to_dict()).id == record.id


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        ForecastRecord(question="  ")
    with pytest.raises(ValueError):
        make_record(question_type="essay")
    with pytest.raises(ValueError):
        make_record(status="pending")
    with pytest.raises(ValueError):
        make_record(probability=1.5)


def test_to_dict_drops_none_values() -> None:
    data = make_record().to_dict()
    assert "resolution" not in data
    assert "percentiles" not in data


# ------------------------------------------------------------------ journal


def test_journal_append_resolve_and_idempotency(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl")
    record = make_record()
    journal.append(record)

    assert journal.get(record.id) is not None
    assert journal.resolve(record.id, True, note="it happened")
    stored = journal.get(record.id)
    assert stored is not None and stored.status == "resolved"
    assert stored.resolution is not None and stored.resolution["outcome"] is True

    with pytest.raises(AlreadyResolved):
        journal.resolve(record.id, False)
    assert journal.resolve(record.id, False, overwrite=True)


def test_journal_resolve_missing_id_returns_false(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl")
    assert not journal.resolve("nope", True)


def test_due_filters_by_date(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl")
    journal.append(make_record(resolve_by="2026-01-01"))
    journal.append(make_record(resolve_by="2099-01-01"))
    due = journal.due("2026-06-01")
    assert len(due) == 1 and due[0].resolve_by == "2026-01-01"


def test_annulled_records_are_not_scored(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl")
    record = make_record()
    journal.append(record)
    assert journal.annul(record.id, "resolution source vanished")
    stored = journal.get(record.id)
    assert stored is not None and stored.status == "annulled" and not stored.scorable
    assert brier_score(journal.all()) is None


# ------------------------------------------------------------------ scoring


def test_brier_known_values() -> None:
    hit = make_record(probability=0.8).resolve(True)
    miss = make_record(probability=0.8).resolve(False)
    assert brier_score([hit]) == pytest.approx(0.04)
    assert brier_score([miss]) == pytest.approx(0.64)
    assert brier_score([hit, miss]) == pytest.approx(0.34)
    coin = make_record(probability=0.5).resolve(True)
    assert brier_score([coin]) == pytest.approx(0.25)


def test_calibration_direction_overconfident() -> None:
    records = [make_record(probability=0.9).resolve(i < 2) for i in range(6)]
    report = calibration_report(records)
    assert report.n == 6
    assert report.direction == "over-confident"
    assert "p>=0.7" in report.high_confidence


def test_calibration_small_n_is_flagged() -> None:
    records = [make_record(probability=0.9).resolve(True)]
    report = calibration_report(records)
    assert report.direction == "insufficient data"
    assert "noise" in report.summary()


def test_calibration_empty() -> None:
    assert calibration_report([]).n == 0


# ------------------------------------------------------------------ aggregation


def test_trimmed_mean_drops_extremes() -> None:
    assert trimmed_mean([0.1, 0.5, 0.5, 0.9]) == pytest.approx(0.5)
    assert trimmed_mean([0.2, 0.4, 0.6]) == pytest.approx(0.4)  # n<4: plain mean
    with pytest.raises(ValueError):
        trimmed_mean([])


def test_geo_mean_odds_known_values() -> None:
    assert geo_mean_odds([0.5, 0.5]) == pytest.approx(0.5)
    assert geo_mean_odds([0.8, 0.8]) == pytest.approx(0.8)
    # odds 9 * 9 * 1 -> 81 ** (1/3) = 4.3267 -> p = 0.8123
    assert geo_mean_odds([0.9, 0.9, 0.5]) == pytest.approx(0.8123, abs=1e-4)


def test_geo_mean_odds_drops_single_extreme_each_end() -> None:
    # Samotsvety's rule: with n>=4, the most extreme forecast on each end is removed.
    assert geo_mean_odds([0.01, 0.5, 0.5, 0.99]) == pytest.approx(0.5)


def test_median_and_blend() -> None:
    assert median([0.2, 0.9, 0.4]) == pytest.approx(0.4)
    assert blend_with_crowd(0.6, 0.4, crowd_weight=0.5) == pytest.approx(0.5)
    assert blend_with_crowd(0.6, 0.4, crowd_weight=1.0) == pytest.approx(0.4)


def test_aggregate_binary_clamps_and_describes() -> None:
    p, desc = aggregate_binary([0.99, 0.995, 0.999, 0.992])
    assert p == pytest.approx(DEFAULTS["clamp"]["max"])
    assert "clamped" in desc and "trimmed_mean(n=4)" in desc

    p, desc = aggregate_binary([0.5, 0.6, 0.7], crowd=0.4)
    assert p == pytest.approx(0.5)  # trimmed mean 0.6, blended 50/50 with 0.4
    assert "crowd" in desc


# ------------------------------------------------------------------ validation


def test_validate_probability_flags_llm_failure_modes() -> None:
    assert any("50%" in w for w in validate_probability(0.5))
    assert any("5%-grid" in w for w in validate_probability(0.35))
    assert any("clamp" in w for w in validate_probability(0.995))
    assert validate_probability(0.37) == []


def test_validate_percentiles() -> None:
    good = {"10": 1.0, "25": 2.0, "50": 3.0, "75": 4.0, "90": 5.0}
    assert validate_percentiles(good) == []
    bad = dict(good, **{"75": 2.5})
    assert any("strictly increasing" in e for e in validate_percentiles(bad))
    assert any("missing" in e for e in validate_percentiles({"50": 1.0}))


def test_validate_mc() -> None:
    assert validate_mc(["a", "b"], [0.6, 0.4]) == []
    assert any("sum" in e for e in validate_mc(["a", "b"], [0.6, 0.6]))
    assert any("options but" in e for e in validate_mc(["a"], [0.5, 0.5]))


def test_validate_record_open_requirements() -> None:
    bare = ForecastRecord(question="Will it rain?")
    errors, _ = validate_record(bare)
    joined = " ".join(errors)
    assert "resolution_criterion" in joined
    assert "resolve_by" in joined
    assert "probability" in joined

    errors, warnings = validate_record(make_record())
    assert errors == []
    assert warnings == []  # 0.62 is off-grid, reference class present


# ------------------------------------------------------------------ config


def test_load_config_defaults_are_a_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no forecast.toml here
    config = load_config()
    config["clamp"]["min"] = 0.2
    assert DEFAULTS["clamp"]["min"] == 0.02


def test_load_config_merges_toml(tmp_path: Path) -> None:
    toml = tmp_path / "forecast.toml"
    toml.write_text("[clamp]\nmin = 0.05\n", encoding="utf-8")
    config = load_config(str(toml))
    assert config["clamp"]["min"] == 0.05
    assert config["clamp"]["max"] == 0.98  # untouched default survives
    assert config["tiers"]["medium"]["draws"] == 5
