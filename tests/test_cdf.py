"""percentiles_to_cdf against the 2026 platform constraints: 201 points, monotone with
minimum step 5e-05, per-bin mass cap 0.2, open-bound tails >= 0.001, closed bounds pinned.

The second half covers declared out-of-bound mass (v0.4.23): p_below_lower / p_above_upper,
their validation, and the BMEX regression that motivated them."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from forecast_scaffold.core import (
    DEFAULT_CDF_SIZE,
    MAX_PMF_VALUE,
    MIN_CDF_STEP,
    ForecastRecord,
    _scale_location,
    main,
    percentiles_to_cdf,
    validate_cdf,
    validate_escape_mass,
    validate_record,
)

# minibench_numeric_tails imports the platform's own scoring formula; pytest does not put
# bench/analysis on sys.path, so add it the way tests/test_minibench_analysis.py does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench" / "analysis"))

import minibench_numeric_tails as tails  # noqa: E402

WIDE = {"10": 10.0, "25": 25.0, "50": 50.0, "75": 75.0, "90": 90.0}
NARROW = {"10": 49.0, "25": 49.5, "50": 50.0, "75": 50.5, "90": 51.0}  # spike -> needs capping


def pmf(cdf: list[float]) -> list[float]:
    return [b - a for a, b in zip(cdf, cdf[1:], strict=False)]


@pytest.mark.parametrize("lower_open", [False, True])
@pytest.mark.parametrize("upper_open", [False, True])
@pytest.mark.parametrize("declared", [WIDE, NARROW])
def test_constructed_cdf_meets_all_platform_constraints(
    lower_open: bool, upper_open: bool, declared: dict[str, float]
) -> None:
    cdf = percentiles_to_cdf(
        declared, 0.0, 100.0, lower_open=lower_open, upper_open=upper_open
    )
    assert len(cdf) == DEFAULT_CDF_SIZE
    assert validate_cdf(cdf, lower_open=lower_open, upper_open=upper_open) == []
    assert all(step >= MIN_CDF_STEP - 1e-12 for step in pmf(cdf))
    assert all(step <= MAX_PMF_VALUE + 1e-9 for step in pmf(cdf))


def test_closed_bounds_are_pinned() -> None:
    cdf = percentiles_to_cdf(WIDE, 0.0, 100.0)
    assert cdf[0] == pytest.approx(0.0, abs=1e-9)
    assert cdf[-1] == pytest.approx(1.0, abs=1e-9)


def test_open_bounds_leave_tail_mass() -> None:
    cdf = percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True, upper_open=True)
    assert cdf[0] == pytest.approx(0.001, abs=1e-6)
    assert cdf[-1] == pytest.approx(0.999, abs=1e-6)


def test_median_lands_near_declared_median() -> None:
    cdf = percentiles_to_cdf(WIDE, 0.0, 100.0)
    # value 50 sits at location index 100; the CDF there should be ~0.5
    assert cdf[100] == pytest.approx(0.5, abs=0.02)


def test_spike_is_capped_but_mass_preserved() -> None:
    cdf = percentiles_to_cdf(NARROW, 0.0, 100.0)
    steps = pmf(cdf)
    assert max(steps) <= MAX_PMF_VALUE + 1e-9
    assert sum(steps) == pytest.approx(cdf[-1] - cdf[0])


def test_log_scaling_shifts_mass() -> None:
    linear = percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True, upper_open=True)
    logged = percentiles_to_cdf(
        WIDE, 0.0, 100.0, lower_open=True, upper_open=True, zero_point=-10.0
    )
    assert validate_cdf(logged, lower_open=True, upper_open=True) == []
    assert linear != logged


def test_discrete_size() -> None:
    cdf = percentiles_to_cdf(WIDE, 0.0, 100.0, cdf_size=21)
    assert len(cdf) == 21
    assert validate_cdf(cdf, cdf_size=21) == []


def test_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        percentiles_to_cdf({"10": 30.0, "25": 20.0, "50": 50.0, "75": 60.0, "90": 70.0}, 0, 100)
    with pytest.raises(ValueError, match="strictly inside"):
        percentiles_to_cdf({"10": 0.0, "25": 25.0, "50": 50.0, "75": 75.0, "90": 90.0}, 0, 100)
    with pytest.raises(ValueError, match="range_min"):
        percentiles_to_cdf(WIDE, 100.0, 0.0)


def test_validate_cdf_catches_violations() -> None:
    good = percentiles_to_cdf(WIDE, 0.0, 100.0)
    flat = list(good)
    flat[50] = flat[49]  # kill the min step
    assert any("below minimum" in e for e in validate_cdf(flat))
    assert any("expected" in e for e in validate_cdf(good[:-1]))
    open_checked = validate_cdf(good, lower_open=True)
    assert any("0.001" in e for e in open_checked)  # closed-pinned start fails open check


def test_cdf_cli(capsys: pytest.CaptureFixture[str]) -> None:
    code = main([
        "cdf", "--percentiles", "10:10,25:25,50:50,75:75,90:90",
        "--min", "0", "--max", "100", "--open-upper",
    ])
    assert code == 0
    cdf = json.loads(capsys.readouterr().out)
    assert len(cdf) == DEFAULT_CDF_SIZE

    assert main(["cdf", "--percentiles", "10:90,25:80,50:50,75:20,90:10",
                 "--min", "0", "--max", "100"]) == 2


# ------------------------------------------------------- declared escape mass (v0.4.23)
# The 2026-07-27 MiniBench wave lost two numerics (BMEX q45012, bluetongue q44967) to
# outcomes that landed OUTSIDE the question range, each scoring the -195.6 floor. These
# tests pin the fix: a forecaster may now declare the out-of-bound mass, and the submitted
# CDF must carry it — while a forecast that declares nothing is byte-identical to before.

# Captured from v0.4.22 BEFORE the change, at cdf_size=21 so the fixture stays readable.
# Keyed (lower_open, upper_open) over WIDE on [0, 100].
V0_4_22_CDFS: dict[tuple[bool, bool], list[float]] = {
    (False, False): [
        0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5,
        0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0,
    ],
    (False, True): [
        0.0, 0.0525526316, 0.1051052632, 0.1576578947, 0.2102105263, 0.2627631579,
        0.3153157895, 0.3678684211, 0.4204210526, 0.4729736842, 0.5255263158,
        0.5780789474, 0.6306315789, 0.6831842105, 0.7357368421, 0.7882894737,
        0.8408421053, 0.8933947368, 0.9459473684, 0.9724736842, 0.999,
    ],
    (True, False): [
        0.001, 0.0275263158, 0.0540526316, 0.1066052632, 0.1591578947, 0.2117105263,
        0.2642631579, 0.3168157895, 0.3693684211, 0.4219210526, 0.4744736842,
        0.5270263158, 0.5795789474, 0.6321315789, 0.6846842105, 0.7372368421,
        0.7897894737, 0.8423421053, 0.8948947368, 0.9474473684, 1.0,
    ],
    (True, True): [
        0.001, 0.0289444444, 0.0568888889, 0.1122777778, 0.1676666667, 0.2230555556,
        0.2784444444, 0.3338333333, 0.3892222222, 0.4446111111, 0.5, 0.5553888889,
        0.6107777778, 0.6661666667, 0.7215555556, 0.7769444444, 0.8323333333,
        0.8877222222, 0.9431111111, 0.9710555556, 0.999,
    ],
}

# BMEX q45012 exactly as bot/journal/forecasts.jsonl recorded it on 2026-07-27.
BMEX_PERCENTILES = {"10": 102000.0, "25": 132000.0, "50": 170000.0,
                    "75": 238000.0, "90": 382000.0}
BMEX_SCALING = {"range_min": 100000.0, "range_max": 1500000.0, "zero_point": None,
                "lower_open": True, "upper_open": True, "cdf_size": 201}
BMEX_OUTCOME = 29902.71  # resolved BELOW the 100k floor


def build_bmex(**kwargs: float) -> list[float]:
    return percentiles_to_cdf(
        BMEX_PERCENTILES, BMEX_SCALING["range_min"], BMEX_SCALING["range_max"],
        lower_open=True, upper_open=True, zero_point=None,
        cdf_size=int(BMEX_SCALING["cdf_size"]), **kwargs,
    )


@pytest.mark.parametrize(("lower_open", "upper_open"), sorted(V0_4_22_CDFS))
def test_absent_fields_reproduce_v0_4_22_exactly(lower_open: bool, upper_open: bool) -> None:
    """Back-compat is the whole back-compat story: with neither field passed, the
    construction must be bit-for-bit what v0.4.22 shipped, for every bound combination."""
    cdf = percentiles_to_cdf(
        WIDE, 0.0, 100.0, lower_open=lower_open, upper_open=upper_open, cdf_size=21
    )
    assert cdf == V0_4_22_CDFS[(lower_open, upper_open)]


def test_absent_fields_reproduce_the_bmex_cdf_that_was_actually_submitted() -> None:
    """The 201-point object, not just the readable 21-point one: rebuilding BMEX q45012
    from its journaled percentiles reproduces the CDF the platform received."""
    journal = Path(__file__).resolve().parents[1] / "bot" / "journal" / "forecasts.jsonl"
    submitted = next(
        json.loads(line)["submitted_cdf"]
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip() and (json.loads(line).get("source") or {}).get("question_id") == 45012
    )
    assert build_bmex() == submitted


def test_declared_lower_escape_lands_at_the_endpoint() -> None:
    cdf = percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True, p_below_lower=0.13)
    assert 0.129 <= cdf[0] <= 0.131
    assert cdf[-1] == pytest.approx(1.0, abs=1e-9)  # upper bound stays closed and pinned
    assert validate_cdf(cdf, lower_open=True) == []


def test_declared_upper_escape_lands_at_the_endpoint() -> None:
    cdf = percentiles_to_cdf(WIDE, 0.0, 100.0, upper_open=True, p_above_upper=0.13)
    assert 0.129 <= 1.0 - cdf[-1] <= 0.131
    assert cdf[0] == pytest.approx(0.0, abs=1e-9)
    assert validate_cdf(cdf, upper_open=True) == []


def test_percentiles_become_conditional_on_landing_inside() -> None:
    """The declared median is placed at p_below + 0.5*(1 - p_below - p_above), because the
    five percentiles now describe the distribution GIVEN the outcome is in range."""
    cdf = percentiles_to_cdf(
        WIDE, 0.0, 100.0, lower_open=True, upper_open=True,
        p_below_lower=0.20, p_above_upper=0.10, cdf_size=201,
    )
    assert cdf[100] == pytest.approx(0.20 + 0.5 * 0.70, abs=0.01)  # value 50 -> location 0.5


def test_a_lone_field_defaults_the_other_to_zero() -> None:
    lone = percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True, upper_open=True,
                              p_below_lower=0.13)
    both = percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True, upper_open=True,
                              p_below_lower=0.13, p_above_upper=0.0)
    assert lone == both
    # p_above = 0 on an OPEN upper bound still leaves the platform's 0.001 floor there.
    assert 1.0 - lone[-1] == pytest.approx(0.001, abs=1e-9)


def test_escape_mass_on_a_closed_bound_is_rejected() -> None:
    with pytest.raises(ValueError, match="only valid when the lower bound is OPEN"):
        percentiles_to_cdf(WIDE, 0.0, 100.0, upper_open=True, p_below_lower=0.1)
    with pytest.raises(ValueError, match="only valid when the upper bound is OPEN"):
        percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True, p_above_upper=0.1)


def test_escape_mass_range_and_sum_are_validated() -> None:
    with pytest.raises(ValueError, match=r"p_below_lower=0.6 must be in \[0, 0.5\]"):
        percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True, p_below_lower=0.6)
    with pytest.raises(ValueError, match=r"p_above_upper=-0.1 must be in \[0, 0.5\]"):
        percentiles_to_cdf(WIDE, 0.0, 100.0, upper_open=True, p_above_upper=-0.1)
    with pytest.raises(ValueError, match="exceeds 0.6"):
        percentiles_to_cdf(WIDE, 0.0, 100.0, lower_open=True, upper_open=True,
                           p_below_lower=0.5, p_above_upper=0.5)


def test_validate_escape_mass_accepts_the_boundary_values() -> None:
    assert validate_escape_mass(0.5, 0.1, lower_open=True, upper_open=True) == []
    assert validate_escape_mass(None, None, lower_open=False, upper_open=False) == []
    assert validate_escape_mass(0.0, 0.0, lower_open=True, upper_open=True) == []


def test_record_validation_rejects_escape_mass_the_scaling_forbids() -> None:
    base = {
        "question": "How large will X be?", "question_type": "numeric",
        "resolution_criterion": "per the source", "resolve_by": "2027-01-01",
        "percentiles": {"10": 1.0, "25": 2.0, "50": 3.0, "75": 4.0, "90": 5.0},
        "reference_class": "past X",
    }
    closed_upper = {**BMEX_SCALING, "upper_open": False}
    errors, _ = validate_record(ForecastRecord.from_dict(
        {**base, "scaling": closed_upper, "p_above_upper": 0.1}))
    assert any("only valid when the upper bound is OPEN" in e for e in errors)

    errors, _ = validate_record(ForecastRecord.from_dict(
        {**base, "scaling": BMEX_SCALING, "p_above_upper": 0.1}))
    assert errors == []

    # Escape mass on a binary record is a category error, scaling or not.
    errors, _ = validate_record(ForecastRecord.from_dict(
        {"question": "Will X?", "resolution_criterion": "c", "resolve_by": "2027-01-01",
         "probability": 0.4, "reference_class": "past X", "p_below_lower": 0.1}))
    assert any("meaningless on a binary forecast" in e for e in errors)


def test_escape_mass_record_serializes_only_when_set() -> None:
    record = ForecastRecord(question="q", question_type="numeric",
                            percentiles={"10": 1.0, "50": 2.0, "90": 3.0})
    assert "p_below_lower" not in record.to_dict()
    record.p_below_lower = 0.13
    assert record.to_dict()["p_below_lower"] == 0.13


@pytest.mark.parametrize("p_below", [0.0, 0.001, 0.05, 0.3, 0.5])
@pytest.mark.parametrize("p_above", [0.0, 0.001, 0.05, 0.3])
@pytest.mark.parametrize("declared", [WIDE, NARROW])
def test_escape_mass_keeps_every_platform_constraint(
    p_below: float, p_above: float, declared: dict[str, float]
) -> None:
    """Property sweep incl. the extremes (0.5, 0.0) and (0.3, 0.3): monotone, min step,
    per-bin cap, and endpoints that honor what was declared."""
    if p_below + p_above > 0.6:
        pytest.skip("forbidden by MAX_TOTAL_ESCAPE_MASS")
    cdf = percentiles_to_cdf(
        declared, 0.0, 100.0, lower_open=True, upper_open=True,
        p_below_lower=p_below, p_above_upper=p_above,
    )
    assert validate_cdf(cdf, lower_open=True, upper_open=True) == []
    steps = pmf(cdf)
    assert all(step >= MIN_CDF_STEP - 1e-12 for step in steps)
    assert all(step <= MAX_PMF_VALUE + 1e-9 for step in steps)
    assert all(0.0 <= v <= 1.0 for v in cdf)
    assert cdf[0] == pytest.approx(max(p_below, 0.001), abs=1e-9)
    assert 1.0 - cdf[-1] == pytest.approx(max(p_above, 0.001), abs=1e-9)


def test_bmex_regression_declared_escape_mass_turns_the_worst_score_positive() -> None:
    """The reason this feature exists, scored with the platform's own formula.

    BMEX q45012 (2026-07-27) resolved at $29,902.71 — below its $100k open floor. The run's
    journal says it believed ~12-15% of the mass sat there, but the strictly-inside-bounds
    contract compressed that belief into a p10 of $102k, and the standardization pinned the
    below-bound mass at the platform's 0.001 floor. Metaculus scores an out-of-bound outcome
    against a flat 0.05 baseline, so that is 50*ln(0.001/0.05) = -195.6, the worst payable
    value. Declaring p_below_lower=0.13 on the SAME five percentiles pays +47.8 instead.
    """
    location = _scale_location(BMEX_OUTCOME, BMEX_SCALING["range_min"],
                               BMEX_SCALING["range_max"], None)
    assert location < 0.0  # the outcome really is outside the range

    as_submitted = tails.platform_score(build_bmex(), location, open_bounds=2)
    with_declaration = tails.platform_score(
        build_bmex(p_below_lower=0.13), location, open_bounds=2
    )

    assert as_submitted == pytest.approx(50.0 * math.log(0.001 / 0.05), abs=1.0)
    assert as_submitted == pytest.approx(-195.6, abs=1.0)
    assert with_declaration == pytest.approx(50.0 * math.log(0.13 / 0.05), abs=1.0)
    assert with_declaration == pytest.approx(47.77, abs=1.0)
    assert with_declaration - as_submitted == pytest.approx(243.4, abs=1.0)


def test_cdf_cli_reports_declared_tail_mass(capsys: pytest.CaptureFixture[str]) -> None:
    code = main([
        "cdf", "--percentiles", "10:5,25:8,50:12,75:20,90:35",
        "--min", "0", "--max", "100", "--open-upper", "--p-above-upper", "0.08",
    ])
    assert code == 0
    captured = capsys.readouterr()
    cdf = json.loads(captured.out)  # stdout stays pure JSON for piping
    assert len(cdf) == DEFAULT_CDF_SIZE
    assert 1.0 - cdf[-1] == pytest.approx(0.08, abs=1e-9)
    assert "above range_max 0.0800" in captured.err

    # ...and the closed-bound rejection reaches the CLI as an error exit, not a traceback.
    assert main(["cdf", "--percentiles", "10:5,25:8,50:12,75:20,90:35",
                 "--min", "0", "--max", "100", "--p-above-upper", "0.08"]) == 2
