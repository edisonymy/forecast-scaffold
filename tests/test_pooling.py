"""Unit tests for the non-binary pools (v0.4.28): quantile averaging for continuous
questions, per-option geometric mean for multiple choice, and the escape-mass average.

The harness-level behaviour (which runs feed these, what gets journaled) lives in
tests/test_multirun.py::TestNonBinaryPooling; this file pins the arithmetic.
"""

from __future__ import annotations

import math

import pytest

from forecast_scaffold.core import (
    POOL_PERCENTILE_KEYS,
    geo_mean_odds,
    pool_escape_mass,
    pool_mc,
    pool_percentiles,
)


def pcts(*values: float) -> dict[str, float]:
    return dict(zip(POOL_PERCENTILE_KEYS, [float(v) for v in values], strict=True))


class TestPoolPercentiles:
    def test_per_key_arithmetic_mean(self) -> None:
        pooled = pool_percentiles([
            pcts(10, 20, 30, 40, 50),
            pcts(20, 30, 40, 50, 60),
            pcts(30, 40, 50, 60, 70),
        ])
        assert pooled == pcts(20, 30, 40, 50, 60)

    def test_single_run_is_the_identity(self) -> None:
        assert pool_percentiles([pcts(1, 2, 3, 4, 5)]) == pcts(1, 2, 3, 4, 5)

    def test_the_pool_is_strictly_increasing(self) -> None:
        # Disagreeing runs must still produce a usable percentile set: the mean of strictly
        # increasing sequences is strictly increasing, and the repair covers float noise.
        pooled = pool_percentiles([
            pcts(1, 2, 3, 4, 5),
            pcts(100, 200, 300, 400, 500),
            pcts(2, 4, 6, 8, 1000),
        ])
        values = [pooled[k] for k in POOL_PERCENTILE_KEYS]
        assert all(a < b for a, b in zip(values, values[1:], strict=False))

    def test_location_disagreement_recentres_but_does_not_widen(self) -> None:
        """Vincentization is shape-preserving, and the docstring says so: three runs that
        each declare a 20-wide interval and disagree only about WHERE it sits pool to a
        20-wide interval at their average location — not to something spanning all three.
        (A vertical CDF average would widen here; that is the trade this pool makes.)"""
        runs = [pcts(0, 5, 10, 15, 20), pcts(20, 25, 30, 35, 40), pcts(40, 45, 50, 55, 60)]
        pooled = pool_percentiles(runs)
        assert pooled["90"] - pooled["10"] == pytest.approx(20.0)
        assert pooled["50"] == pytest.approx(30.0)

    def test_width_disagreement_averages_the_widths(self) -> None:
        runs = [pcts(28, 29, 30, 31, 32), pcts(10, 20, 30, 40, 50)]
        pooled = pool_percentiles(runs)
        assert pooled["90"] - pooled["10"] == pytest.approx((4 + 40) / 2)
        assert pooled["50"] == pytest.approx(30.0)

    def test_log_scaled_pool_is_geometric_about_the_zero_point(self) -> None:
        pooled = pool_percentiles(
            [pcts(20, 50, 100, 200, 500), pcts(200, 500, 1000, 2000, 5000)],
            zero_point=0.0,
        )
        assert pooled["50"] == pytest.approx(math.sqrt(100 * 1000))
        assert pooled["10"] == pytest.approx(math.sqrt(20 * 200))
        # and it is NOT the linear mean, which would sit a factor 1.7 higher
        assert pooled["50"] != pytest.approx(550.0)

    def test_log_scale_with_an_offset_zero_point(self) -> None:
        pooled = pool_percentiles(
            [pcts(11, 12, 13, 14, 15), pcts(101, 102, 103, 104, 105)], zero_point=10.0
        )
        assert pooled["10"] == pytest.approx(10.0 + math.sqrt(1 * 91))

    def test_zero_point_above_every_value_pools_on_the_negative_side(self) -> None:
        # A "reversed" log scale: zero_point sits above range_max, so x - zero_point < 0
        # for every declared value and the geometric mean is taken on |x - zero_point|.
        pooled = pool_percentiles(
            [pcts(-90, -80, -70, -60, -50), pcts(-9, -8, -7, -6, -5)], zero_point=10.0
        )
        assert pooled["50"] == pytest.approx(10.0 - math.sqrt(80 * 17))
        values = [pooled[k] for k in POOL_PERCENTILE_KEYS]
        assert all(a < b for a, b in zip(values, values[1:], strict=False))

    def test_value_on_the_zero_point_is_a_clear_error(self) -> None:
        with pytest.raises(ValueError, match="straddles zero_point"):
            pool_percentiles([pcts(-1, 0, 1, 2, 3)], zero_point=0.0)

    def test_empty_runs_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            pool_percentiles([])

    def test_missing_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="missing percentile key '90'"):
            pool_percentiles([{"10": 1.0, "25": 2.0, "50": 3.0, "75": 4.0}])

    def test_extra_keys_are_dropped_not_averaged(self) -> None:
        # Only the five contract keys are guaranteed present in EVERY run, so a run's extra
        # percentile cannot be averaged against runs that never declared it.
        pooled = pool_percentiles([
            {**pcts(10, 20, 30, 40, 50), "95": 60.0},
            pcts(20, 30, 40, 50, 60),
        ])
        assert set(pooled) == set(POOL_PERCENTILE_KEYS)


class TestPoolEscapeMass:
    def test_mean_over_the_declaring_runs_only(self) -> None:
        assert pool_escape_mass([0.1, None, 0.2]) == pytest.approx(0.15)

    def test_all_none_is_none(self) -> None:
        assert pool_escape_mass([None, None, None]) is None

    def test_empty_is_none(self) -> None:
        assert pool_escape_mass([]) is None

    def test_a_lone_declaration_survives_the_pool(self) -> None:
        # A run that omits the field said nothing about the tail, not that it is zero —
        # averaging silence in as 0 would re-create the undeclared-tail failure.
        assert pool_escape_mass([None, 0.3, None]) == pytest.approx(0.3)


class TestPoolMC:
    def test_geometric_mean_then_renormalize(self) -> None:
        pooled = pool_mc([{"A": 0.6, "B": 0.4}, {"A": 0.8, "B": 0.2}])
        geo_a, geo_b = math.sqrt(0.6 * 0.8), math.sqrt(0.4 * 0.2)
        assert pooled["A"] == pytest.approx(geo_a / (geo_a + geo_b))
        assert sum(pooled.values()) == pytest.approx(1.0)

    def test_identical_runs_are_unchanged(self) -> None:
        run = {"A": 0.5, "B": 0.3, "C": 0.2}
        pooled = pool_mc([run, run, run])
        assert pooled == {k: pytest.approx(v) for k, v in run.items()}

    def test_a_zero_does_not_annihilate_the_pool(self) -> None:
        pooled = pool_mc([{"A": 0.0, "B": 1.0}, {"A": 0.5, "B": 0.5}])
        assert 0.0 < pooled["A"] < 0.01  # log-space pooling keeps it tiny but finite
        assert sum(pooled.values()) == pytest.approx(1.0)

    def test_mismatched_option_sets_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="same exact option labels"):
            pool_mc([{"A": 0.5, "B": 0.5}, {"A": 0.5, "C": 0.5}])

    def test_empty_runs_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            pool_mc([])

    def test_optionless_run_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one option"):
            pool_mc([{}])

    def test_on_two_options_it_reproduces_geo_mean_odds(self) -> None:
        # Renormalizing per-option geometric means over two options IS the geometric mean
        # of odds, so the MC pool agrees with the binary pool wherever both are defined.
        runs = [{"A": 0.9, "B": 0.1}, {"A": 0.5, "B": 0.5}, {"A": 0.3, "B": 0.7}]
        pooled = pool_mc(runs)
        assert pooled["A"] == pytest.approx(geo_mean_odds([0.9, 0.5, 0.3]))
        # and it is NOT the arithmetic mean (0.5667), which ignores the odds scale
        assert pooled["A"] != pytest.approx(sum([0.9, 0.5, 0.3]) / 3)
