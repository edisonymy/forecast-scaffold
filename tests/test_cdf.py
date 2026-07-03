"""percentiles_to_cdf against the 2026 platform constraints: 201 points, monotone with
minimum step 5e-05, per-bin mass cap 0.2, open-bound tails >= 0.001, closed bounds pinned."""

from __future__ import annotations

import json

import pytest

from forecast_scaffold.core import (
    DEFAULT_CDF_SIZE,
    MAX_PMF_VALUE,
    MIN_CDF_STEP,
    main,
    percentiles_to_cdf,
    validate_cdf,
)

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
