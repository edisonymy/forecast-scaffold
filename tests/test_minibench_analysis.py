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
