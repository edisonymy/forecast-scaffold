"""to_decision_record must produce the documented interop mapping (docs/schema.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forecast_scaffold.core import ForecastRecord, Journal, main, to_decision_record


def test_mapping_fields_and_provenance_strings() -> None:
    record = ForecastRecord(
        question="Will X happen by 2026-12-31?",
        resolution_criterion="official announcement of X",
        resolve_by="2026-12-31",
        probability=0.62,
        reference_class="similar events since 2000",
        why_it_matters="moves the Y decision",
        parent_id="2026-07-01-abcd1234",
        fast_proxy=True,
        reasoning="base rate 0.35; strong current signal",
        what_would_change_my_mind=["a formal denial"],
    )
    out = to_decision_record(record)

    assert out["title"] == record.question
    assert out["method"] == "forecast"
    assert out["needs_system2"] is False
    assert out["rationale"] == record.reasoning
    assert out["prediction"] == {
        "expectation": record.question,
        "probability": 0.62,
        "resolve_by": "2026-12-31",
    }
    assert out["assumptions"] == [
        "estimand_kind: probability",
        "reference_class: similar events since 2000",
        "VOI: moves the Y decision",
        "resolves_when: official announcement of X",
        "parent_decision: 2026-07-01-abcd1234",
        "fast_proxy: true",
    ]
    assert "resolution" not in out


def test_resolution_maps_to_realized() -> None:
    record = ForecastRecord(
        question="Q?", resolution_criterion="c", resolve_by="2026-01-01", probability=0.9
    ).resolve(True, note="it happened")
    out = to_decision_record(record)
    assert out["status"] == "resolved"
    assert out["resolution"]["realized"] is True
    assert out["resolution"]["what_happened"] == "it happened"


def test_numeric_kind_maps_to_magnitude() -> None:
    record = ForecastRecord(question="How many?", question_type="numeric")
    assert "estimand_kind: magnitude" in to_decision_record(record)["assumptions"]


def test_multiple_choice_payload_survives_export() -> None:
    # Regression (v0.4.12): the export previously kept only the binary probability slot
    # (null here) and silently dropped the MC distribution.
    record = ForecastRecord(
        question="Which option wins?",
        question_type="multiple_choice",
        options=["A", "B", "C"],
        probabilities=[0.5, 0.3, 0.2],
    )
    prediction = to_decision_record(record)["prediction"]
    assert prediction["options"] == ["A", "B", "C"]
    assert prediction["probabilities"] == [0.5, 0.3, 0.2]


def test_numeric_percentiles_survive_export() -> None:
    record = ForecastRecord(
        question="How many by 2026-12-31?",
        question_type="numeric",
        percentiles={"10": 5.0, "25": 8.0, "50": 12.0, "75": 20.0, "90": 35.0},
    )
    prediction = to_decision_record(record)["prediction"]
    assert prediction["percentiles"] == {
        "10": 5.0, "25": 8.0, "50": 12.0, "75": 20.0, "90": 35.0,
    }


def test_export_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = tmp_path / "j.jsonl"
    Journal(journal).append(
        ForecastRecord(
            question="Q?", resolution_criterion="c", resolve_by="2026-01-01", probability=0.4
        )
    )
    assert main(["export", "--journal", str(journal)]) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["method"] == "forecast"
