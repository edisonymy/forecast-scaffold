"""Reproducibility guards for the operator-supplied MiniBench diagnostic."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench" / "analysis"))

import minibench_2026_07_15 as analysis  # noqa: E402


def test_binary_signature_is_concentrated_not_global() -> None:
    summary = analysis.summarize_binaries()

    assert summary["n"] == 9
    assert summary["below"] == 7
    assert summary["mean_signed_pp"] == pytest.approx(-9.6667, abs=1e-4)
    assert summary["mean_absolute_pp"] == pytest.approx(11.1333, abs=1e-4)
    assert summary["top3_absolute_share"] == pytest.approx(0.769, abs=1e-3)
    assert summary["same_modal_outcome"] == 8
    assert summary["pearson_excluding_sk"] == pytest.approx(0.972, abs=1e-3)
    assert summary["spearman_excluding_sk"] == pytest.approx(0.958, abs=1e-3)
    assert summary["one_sided_sign_probability"] == pytest.approx(0.08984375)


def test_numeric_signature_is_dispersion_not_location() -> None:
    current = analysis.summarize_numerics()
    combined = analysis.summarize_numerics(
        analysis.CURRENT_NUMERICS + analysis.PRIOR_NUMERICS
    )

    assert current["bot_narrower_count"] == current["n"] == 6
    assert current["bot_median_inside_community"] == 6
    assert current["community_median_inside_bot"] == 6
    assert current["bot_interval_nested"] == 5
    assert current["mean_width_ratio"] == pytest.approx(0.547, abs=1e-3)
    assert current["median_width_ratio"] == pytest.approx(0.55, abs=1e-3)
    assert combined["bot_narrower_count"] == combined["n"] == 8
    assert combined["mean_width_ratio"] == pytest.approx(0.587, abs=1e-3)


# --- platform-faithful scoring (corrected 2026-07-26 against Metaculus/metaculus source) --

import math  # noqa: E402

import minibench_numeric_tails as tails  # noqa: E402


def test_bucket_index_is_right_closed_at_edges() -> None:
    """An outcome exactly on a bucket edge scores in the bucket BELOW (formulas.py).

    This is the correction that matters most here: declared percentiles land on bucket
    edges constantly, and those are exactly the density cliffs the tail analysis is about.
    A left-closed reading is biased pessimistic precisely there.
    """
    n = 200
    assert tails.bucket_index(0.5, n) == 100          # edge -> bucket below
    assert tails.bucket_index(0.5 + 1e-6, n) == 101   # just past it -> next bucket
    assert tails.bucket_index(0.005, n) == 1          # first interior edge
    assert tails.bucket_index(0.0, n) == 1            # clamps up, never bucket 0


def test_bucket_index_out_of_bounds() -> None:
    n = 200
    assert tails.bucket_index(-0.01, n) == 0          # below the lower bound
    assert tails.bucket_index(1.01, n) == n + 1       # above the upper bound
    assert tails.bucket_index(1.0, n) == n            # exactly at the top stays inbound


def test_platform_pmf_carries_the_out_of_bound_ends() -> None:
    cdf = [0.001, 0.5, 0.999]
    pmf = tails.platform_pmf(cdf)
    assert len(pmf) == len(cdf) + 1
    assert pmf[0] == pytest.approx(0.001)             # mass under the lower bound
    assert pmf[-1] == pytest.approx(0.001)            # mass over the upper bound
    assert sum(pmf) == pytest.approx(1.0)


def test_platform_score_is_50_ln_not_100_log2() -> None:
    """A uniform forecast scores 0; e times baseline scores exactly 50."""
    n = 200
    uniform = [i / n for i in range(n + 1)]
    assert tails.platform_score(uniform, 0.5, open_bounds=0) == pytest.approx(0.0, abs=1e-9)

    # one bucket carrying e * baseline mass, both bounds closed -> exactly 50 points
    target, mass = 100, math.e / n
    cdf = [0.0] * (n + 1)
    for i in range(1, n + 1):
        step = mass if i == target else (1 - mass) / (n - 1)
        cdf[i] = cdf[i - 1] + step
    loc = (target - 0.5) / n                          # mid-bucket, unambiguous
    assert tails.platform_score(cdf, loc, open_bounds=0) == pytest.approx(50.0, abs=1e-6)


def test_open_bounds_shrink_the_baseline() -> None:
    """baseline = (1 - 0.05*open_bounds)/N: open bounds make the same mass score higher."""
    n = 200
    uniform = [i / n for i in range(n + 1)]
    closed = tails.platform_score(uniform, 0.5, open_bounds=0)
    one_open = tails.platform_score(uniform, 0.5, open_bounds=1)
    two_open = tails.platform_score(uniform, 0.5, open_bounds=2)
    assert closed < one_open < two_open
    assert one_open == pytest.approx(50.0 * math.log(1 / 0.95), abs=1e-9)
    assert two_open == pytest.approx(50.0 * math.log(1 / 0.90), abs=1e-9)


def test_out_of_bound_outcome_uses_the_flat_baseline() -> None:
    n = 200
    cdf = [0.001 + 0.998 * i / n for i in range(n + 1)]
    score = tails.platform_score(cdf, 1.5, open_bounds=2)   # resolved above the upper bound
    assert score == pytest.approx(50.0 * math.log(0.001 / 0.05), abs=1e-9)
