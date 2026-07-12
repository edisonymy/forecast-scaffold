"""Guards for the frozen deadline-router census and preregistration."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from bench.analysis import deadline_census as census

ROOT = Path(__file__).resolve().parents[1]


def frozen_rows() -> list[dict]:
    return census.load_jsonl(ROOT / "bench/analysis/deadline-census.jsonl")


def test_frozen_census_has_every_admissible_qid_exactly_once() -> None:
    rows = frozen_rows()
    qids = [row["qid"] for row in rows]

    assert len(rows) == census.EXPECTED_CENSUS_SIZE == 152
    assert len(set(qids)) == 152
    assert not set(qids) & census.MEMORY_CLAIM_EXCLUSIONS
    assert not set(qids) & census.FROZEN_OPUS_PROBE_EXCLUSIONS

    groups = census.validate_census(rows)
    assert {name: len(values) for name, values in groups.items()} == {
        "tagged_development": 92,
        "held_out_motivating": 10,
        "non_fired_controls": 50,
    }


def test_holdouts_are_exact_and_router_tagged() -> None:
    rows = frozen_rows()
    marked = {row["qid"] for row in rows if row["holdout_motivator"]}

    assert marked == census.MOTIVATING_HOLDOUTS
    assert all(
        row["institutional_action_by_deadline"]
        for row in rows
        if row["qid"] in census.MOTIVATING_HOLDOUTS
    )

    broken = deepcopy(rows)
    target = next(row for row in broken if row["holdout_motivator"])
    target["holdout_motivator"] = False
    with pytest.raises(ValueError, match="holdout mismatch"):
        census.validate_census(broken)


def test_validator_filters_standard_exclusions_and_pins_source_order() -> None:
    rows = frozen_rows()
    specs = [{"id": row["qid"]} for row in rows]
    specs.insert(0, {"id": next(iter(census.FROZEN_OPUS_PROBE_EXCLUSIONS))})
    memory_index = next(
        index
        for index, row in enumerate(rows)
        if row["qid"] == "btf2:e3f02b5e-6c19-579a-9ac4-7c5eccb51953"
    )
    specs.insert(memory_index + 1, {"id": next(iter(census.MEMORY_CLAIM_EXCLUSIONS))})

    census.validate_census(rows, specs)

    reversed_rows = list(reversed(rows))
    with pytest.raises(ValueError, match="file order"):
        census.validate_census(reversed_rows, specs)

    contaminated = deepcopy(rows)
    contaminated[0]["qid"] = next(iter(census.FROZEN_OPUS_PROBE_EXCLUSIONS))
    with pytest.raises(ValueError, match="excluded/contaminated"):
        census.validate_census(contaminated)


def test_readout_names_all_three_slices_and_preserves_gate_verbatim() -> None:
    rows = frozen_rows()
    groups = census.validate_census(rows)
    text = census.render_readout(rows, groups, show_qids=False)

    assert "tagged development: 92" in text
    assert "held-out motivating: 10" in text
    assert "non-fired controls: 50" in text
    assert f"preregistered gate: {census.PREREGISTERED_GATE}" in text
    assert census.PREREGISTERED_GATE == (
        "net paired Brier; tagged development delta >= +0.015 and non-deadline "
        "controls degrade <0.003 promote; controls >=0.005 kill; +/-0.002 "
        "contamination guard on non-fired questions; motivating 10 never enter "
        "promote decision."
    )


def test_research_move_contains_retrieval_steps_but_no_number_direction() -> None:
    move = (
        ROOT / "bench/experiments/deadline/research-move.md"
    ).read_text(encoding="utf-8").casefold()

    for required in (
        "official docket",
        "remaining step",
        "window arithmetic",
        "institution-specific",
        "slippage",
    ):
        assert required in move
    for forbidden in ("probability", "hedg", "price the", "odds", "confidence"):
        assert forbidden not in move
