"""Pure (no-network) tests for bench/fetch_btf2.py: resolution normalization, row->spec
transformation and its leak hygiene, deterministic sampling, and exclusion filtering."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

from fetch_btf2 import (  # noqa: E402 - path bootstrap above
    build_spec,
    build_usable_specs,
    normalize_resolution,
    normalize_sota_probability,
    sample_rows,
)


def make_row(**overrides) -> dict:
    row = {
        "question_id": "q1",
        "question": "Will X happen?",
        "resolution_criteria": "Resolves YES if X happens by the date.",
        "background": "Some context about X.",
        "research_summary": "A compiled dossier of evidence about X.",
        "present_date": "2025-01-01",
        "resolution": "Yes",
        "resolution_explanation": "It happened because of reasons.",
        # BTF-2 ships this on a 0-100 scale (verified against the live API); 73.0 means 73%.
        "sota_forecast_probability": 73.0,
        "sota_summary_rationale": "FutureSearch reasoned this way about X.",
    }
    row.update(overrides)
    return row


# --- normalize_resolution ---------------------------------------------------


def test_normalize_resolution_yes_no_variants() -> None:
    assert normalize_resolution("Yes") == 1
    assert normalize_resolution("yes") == 1
    assert normalize_resolution("YES") == 1
    assert normalize_resolution("No") == 0
    assert normalize_resolution("no") == 0


def test_normalize_resolution_bool_and_numeric() -> None:
    assert normalize_resolution(True) == 1
    assert normalize_resolution(False) == 0
    assert normalize_resolution(1) == 1
    assert normalize_resolution(0) == 0
    assert normalize_resolution("1") == 1
    assert normalize_resolution("0") == 0
    assert normalize_resolution("True") == 1
    assert normalize_resolution("False") == 0


def test_normalize_resolution_garbage_is_none() -> None:
    assert normalize_resolution("maybe") is None
    assert normalize_resolution("unresolved") is None
    assert normalize_resolution(None) is None
    assert normalize_resolution(0.5) is None
    assert normalize_resolution([]) is None


# --- normalize_sota_probability ----------------------------------------------


def test_normalize_sota_probability_scales_0_to_100() -> None:
    assert normalize_sota_probability(73.0) == 0.73
    assert normalize_sota_probability(8.0) == 0.08
    assert normalize_sota_probability(96.0) == 0.96
    assert normalize_sota_probability(0.0) == 0.0
    assert normalize_sota_probability(100.0) == 1.0


def test_normalize_sota_probability_passes_through_0_to_1() -> None:
    # Defensive: if a future schema revision already reports 0-1, don't double-divide.
    assert normalize_sota_probability(0.73) == 0.73


def test_normalize_sota_probability_invalid_is_none() -> None:
    assert normalize_sota_probability(None) is None
    assert normalize_sota_probability("not a number") is None
    assert normalize_sota_probability(150.0) is None
    assert normalize_sota_probability(-1.0) is None


# --- build_spec --------------------------------------------------------------


def test_build_spec_includes_as_of_header_and_dossier() -> None:
    spec = build_spec(make_row())
    assert spec is not None
    assert "AS-OF DATE: 2025-01-01" in spec["background"]
    assert "web access is disabled" in spec["background"]
    assert "Frozen research dossier (compiled 2025-01-01)" in spec["background"]
    assert "A compiled dossier of evidence about X." in spec["background"]
    assert "Some context about X." in spec["background"]


def test_build_spec_basic_fields() -> None:
    spec = build_spec(make_row())
    assert spec is not None
    assert spec["id"] == "btf2:q1"
    assert spec["source"] == "btf2"
    assert spec["question"] == "Will X happen?"
    assert spec["criteria"] == "Resolves YES if X happens by the date."
    assert spec["resolution"] == 1
    assert spec["crowd"]["value"] == 0.73  # 73.0 on the raw 0-100 scale
    assert spec["crowd"]["at"] == "2025-01-01"
    assert "futuresearch-sota" in spec["crowd"]["source"]


def test_build_spec_never_leaks_rationale_fields() -> None:
    """resolution_explanation and sota_summary_rationale must not appear anywhere in
    the serialized spec, under any key."""
    spec = build_spec(make_row())
    assert spec is not None
    dumped = json.dumps(spec)
    assert "It happened because of reasons." not in dumped
    assert "FutureSearch reasoned this way about X." not in dumped
    assert "resolution_explanation" not in dumped
    assert "sota_summary_rationale" not in dumped


def test_build_spec_missing_required_field_returns_none() -> None:
    assert build_spec(make_row(question_id=None)) is None
    assert build_spec(make_row(question=None)) is None
    assert build_spec(make_row(resolution_criteria=None)) is None


def test_build_spec_unrecognized_resolution_returns_none() -> None:
    assert build_spec(make_row(resolution="unresolved")) is None


def test_build_spec_missing_research_summary_returns_none() -> None:
    assert build_spec(make_row(research_summary="")) is None
    assert build_spec(make_row(research_summary=None)) is None


def test_build_spec_missing_sota_probability_returns_none() -> None:
    assert build_spec(make_row(sota_forecast_probability=None)) is None


# --- build_usable_specs (exclusion accounting) -------------------------------


def test_build_usable_specs_excludes_missing_research_summary() -> None:
    rows = [make_row(question_id="q1"), make_row(question_id="q2", research_summary="")]
    usable = build_usable_specs(rows)
    assert len(usable) == 1
    assert usable[0]["id"] == "btf2:q1"


def test_build_usable_specs_excludes_missing_sota_and_bad_resolution() -> None:
    rows = [
        make_row(question_id="q1"),
        make_row(question_id="q2", sota_forecast_probability=None),
        make_row(question_id="q3", resolution="dunno"),
    ]
    usable = build_usable_specs(rows)
    assert [s["id"] for s in usable] == ["btf2:q1"]


# --- sample_rows (deterministic sampling) ------------------------------------


def _specs(n: int) -> list[dict]:
    return [build_spec(make_row(question_id=f"q{i}")) for i in range(n)]


def test_sample_rows_same_seed_same_ids() -> None:
    specs = _specs(50)
    a = sample_rows(specs, n=10, seed=42)
    b = sample_rows(specs, n=10, seed=42)
    assert [s["id"] for s in a] == [s["id"] for s in b]


def test_sample_rows_different_seed_different_ids() -> None:
    specs = _specs(50)
    a = sample_rows(specs, n=10, seed=42)
    b = sample_rows(specs, n=10, seed=7)
    assert [s["id"] for s in a] != [s["id"] for s in b]


def test_sample_rows_respects_n() -> None:
    specs = _specs(50)
    sampled = sample_rows(specs, n=5, seed=1)
    assert len(sampled) == 5
