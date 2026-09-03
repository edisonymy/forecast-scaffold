"""Verdict rules of the preregistered phase scorer (bench/analysis/phase_pools.py).

FIX H (2026-09-03 review, two reviewers): the herding spread ratio is computed PER FAMILY —
a binary spread is a difference of probabilities, a continuous spread a difference of medians
in question units, so one pooled mean of the two ratios is not a quantity — and phase 2's
KEEP now also has to clear a CI90 lower bound above zero, the same bar supervisor_verdict
applies to the arm with the stronger published evidence.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench" / "analysis"))

import phase_pools  # noqa: E402


def row(spread1: float, spread2: float) -> dict[str, object]:
    """The two fields herding_line reads."""
    return {"spread_phase1": spread1, "spread_phase2": spread2}


class TestHerdingRatioIsPerFamily:
    def test_two_families_with_different_ratios_get_different_verdicts(self) -> None:
        # One family herds hard (spreads collapse to a fifth), the other barely moves.
        herding = [row(0.20, 0.04), row(0.30, 0.06)]        # ratio 0.20
        independent = [row(20.0, 18.0), row(30.0, 27.0)]    # ratio 0.90
        ratio_herding = phase_pools.herding_line(herding)
        ratio_independent = phase_pools.herding_line(independent)
        assert ratio_herding == 0.2
        assert ratio_independent == 0.9
        assert ratio_herding < phase_pools.HERDING_RATIO <= ratio_independent

        # Same (flat, non-positive) delta in both families: only the herding family is DROPped.
        verdict_herding = phase_pools.phase2_verdict(0.0, 0.0, ratio_herding, n=2)
        verdict_independent = phase_pools.phase2_verdict(0.0, 0.0, ratio_independent, n=2)
        assert verdict_herding.startswith("DROP phase 2 — HERDING")
        assert not verdict_independent.startswith("DROP")
        assert verdict_herding != verdict_independent

    def test_pooling_the_families_would_have_hidden_the_herding(self) -> None:
        # The bug this fixes: the binary family's 0.20 ratio averaged with the continuous
        # family's 0.90 lands at 0.55, above the threshold — so a herding binary arm would
        # have been KEPT on the strength of unrelated continuous rows.
        herding = [row(0.20, 0.04), row(0.30, 0.06)]
        independent = [row(20.0, 18.0), row(30.0, 27.0)]
        pooled = phase_pools.herding_line(herding + independent)
        assert pooled is not None and pooled > phase_pools.HERDING_RATIO
        assert phase_pools.phase2_verdict(0.0, 0.0, pooled, n=4).startswith("KEEP") is False
        assert phase_pools.phase2_verdict(
            0.0, 0.0, phase_pools.herding_line(herding), n=2).startswith("DROP")

    def test_no_spreads_means_no_ratio(self) -> None:
        assert phase_pools.herding_line([]) is None
        assert phase_pools.herding_line([{"spread_phase1": 0.0, "spread_phase2": 0.1}]) is None


class TestPhase2NeedsACiLowerBound:
    def test_a_positive_mean_alone_is_only_extend(self) -> None:
        verdict = phase_pools.phase2_verdict(0.0100, -0.0050, 0.9, n=6)
        assert verdict.startswith("EXTEND phase 2")
        assert "does not exclude zero" in verdict

    def test_a_ci_above_zero_keeps_it(self) -> None:
        verdict = phase_pools.phase2_verdict(0.0100, 0.0020, 0.9, n=12)
        assert verdict.startswith("KEEP phase 2")
        assert "excludes zero on the positive side" in verdict

    def test_a_negative_mean_still_drops_first(self) -> None:
        assert phase_pools.phase2_verdict(-0.01, 0.5, 0.9, n=6).startswith("DROP phase 2")

    def test_an_unavailable_ci_is_extend_not_keep(self) -> None:
        verdict = phase_pools.phase2_verdict(0.01, float("nan"), 0.9, n=1)
        assert verdict.startswith("EXTEND")

    def test_the_bar_matches_the_supervisors(self) -> None:
        # Both arms now demand a CI90 lower bound above zero; the supervisor keeps its extra
        # sample-size gate, which is why it needs the n arguments.
        keep_phase2 = phase_pools.phase2_verdict(0.01, 0.002, 0.9, n=60)
        keep_supervisor = phase_pools.supervisor_verdict(
            0.01, 0.002, n=60, n_binary=40, n_mixed=60)
        assert keep_phase2.startswith("KEEP") and keep_supervisor.startswith("KEEP")
        drop_phase2 = phase_pools.phase2_verdict(0.01, -0.002, 0.9, n=60)
        drop_supervisor = phase_pools.supervisor_verdict(
            0.01, -0.002, n=60, n_binary=40, n_mixed=60)
        assert not drop_phase2.startswith("KEEP") and not drop_supervisor.startswith("KEEP")

    def test_empty_and_nan_samples_are_reported_not_decided(self) -> None:
        assert phase_pools.phase2_verdict(0.0, 0.0, None, n=0).startswith("no phase-2 rows")
        assert phase_pools.phase2_verdict(
            float("nan"), float("nan"), None, n=3) == "no paired phase-2 rows yet"


def test_the_header_documents_the_rule_change() -> None:
    """The rules are preregistered, so a change to them must be written down where the
    readout prints it — phase_pools' own docstring is the preregistration."""
    doc = phase_pools.__doc__ or ""
    assert "RULE CHANGE 2026-09-03" in doc
    assert "CI90 lower bound" in doc or "clause (b) is NEW" in doc
    assert "PER FAMILY" in doc


def test_report_family_returns_the_per_family_ratio_and_bound(capsys) -> None:
    scored = [
        {"scores": {"phase1": 0.0, "phase2": 0.10}, "spread_phase1": 0.2,
         "spread_phase2": 0.04, "supervisor_mode": None, "consumed": "phase2"},
        {"scores": {"phase1": 0.0, "phase2": 0.12}, "spread_phase1": 0.3,
         "spread_phase2": 0.06, "supervisor_mode": None, "consumed": "phase2"},
    ]
    out = phase_pools.report_family("TEST", scored, "nats")
    assert out["phase2_n"] == 2
    assert out["phase2_ratio"] == 0.2
    assert not math.isnan(out["phase2_delta"])
    assert "spread ratio" in capsys.readouterr().out
