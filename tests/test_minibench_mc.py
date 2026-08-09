"""Formula lock for the multiple-choice MiniBench scorer.

The four cells below are OUR OWN submissions, scored by Metaculus, pulled from the wave2
census (my_score_data.spot_baseline_score). They exist so the MC baseline formula cannot
drift the way the numeric one did before 2026-07-26 — if someone "simplifies"
100*ln(p*N)/ln(N), these fail. Everything else in this file is derived from
Metaculus/metaculus source semantics (score_math.py, questions/serializers/common.py),
not from intuition.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench" / "analysis"))

import minibench_mc as mc  # noqa: E402

# (qid, submitted vector, winner index, platform spot_baseline_score)
PLATFORM_CELLS = [
    (44983, [0.655, 0.34, 0.005], 0, 61.48595389736174),
    (45003, [0.87, 0.075, 0.038, 0.008, 0.007, 0.002], 0, 92.22763603456741),
    (45007, [0.9, 0.082, 0.007, 0.009, 0.002], 0, 93.45358308985776),
    (45021, [0.02, 0.16, 0.82], 2, 81.93621710126436),
]

# platform baseline_score / spot_baseline_score for the same four cells: the accuracy
# variant is the spot score times coverage (score_math.py::
# evaluate_forecasts_baseline_accuracy multiplies by forecast_duration/total_duration).
PLATFORM_COVERAGE = {
    44983: (58.29850304995352, 0.9481596910291247),
    45003: (83.88862326665924, 0.9095822778675291),
    45007: (86.43749989096223, 0.9249244066741733),
    45021: (79.19490014876166, 0.9665432814757029),
}


@pytest.mark.parametrize(("qid", "probs", "winner", "expected"), PLATFORM_CELLS)
def test_matches_platform_spot_baseline(qid: int, probs: list[float], winner: int,
                                        expected: float) -> None:
    """Reproduce Metaculus's own spot baseline score to well inside the 0.05 tolerance."""
    assert mc.mc_baseline_score(probs, winner) == pytest.approx(expected, abs=0.05)
    assert mc.mc_baseline_score(probs, winner) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(("qid", "probs", "winner", "expected"), PLATFORM_CELLS)
def test_accuracy_baseline_is_spot_times_coverage(qid: int, probs: list[float], winner: int,
                                                  expected: float) -> None:
    """Why baseline_score != spot_baseline_score: coverage weighting, nothing else.

    We submit once, late, and never update, so the time-average over the open->close
    horizon is a single forecast's score scaled by its coverage fraction.
    """
    platform_accuracy, coverage = PLATFORM_COVERAGE[qid]
    assert mc.mc_baseline_score(probs, winner) * coverage == pytest.approx(
        platform_accuracy, abs=1e-9
    )


def test_uniform_forecast_scores_zero_for_every_n() -> None:
    """100*ln(p*N)/ln(N) with p = 1/N is exactly 0 — the definition of the baseline."""
    for n in (2, 3, 5, 6, 12, 40):
        assert mc.mc_baseline_score([1.0 / n] * n, 0) == pytest.approx(0.0, abs=1e-12)


def test_n2_reduces_to_the_binary_scaling() -> None:
    """Binary's pmf is [1-p, p], so the same branch gives 100 * (1 + log2 p)."""
    for p in (0.5, 0.6, 0.75, 0.9, 0.25):
        got = mc.mc_baseline_score([1 - p, p], 1)
        assert got == pytest.approx(100.0 * (1.0 + math.log2(p)), abs=1e-9)
    assert mc.mc_baseline_score([0.5, 0.5], 1) == pytest.approx(0.0, abs=1e-12)
    assert mc.mc_baseline_score([0.75, 0.25], 1) == pytest.approx(-100.0, abs=1e-9)


def test_log_base_is_irrelevant_but_the_100_is_not() -> None:
    """100*ln(pN)/ln(N) == 100*log2(pN)/log2(N); it is NOT the numeric file's 50*ln."""
    probs, winner = [0.655, 0.34, 0.005], 0
    n, p = 3, 0.655
    assert mc.mc_baseline_score(probs, winner) == pytest.approx(
        100.0 * math.log2(p * n) / math.log2(n), abs=1e-9
    )
    assert mc.mc_baseline_score(probs, winner) != pytest.approx(50.0 * math.log(p * n), abs=1.0)


def test_zero_probability_clips_to_the_platform_floor_not_minus_infinity() -> None:
    """p=0 cannot be submitted (serializer rejects <0.001); clipping pays the worst score."""
    n = 4
    score = mc.mc_baseline_score([0.0, 1.0, 0.0, 0.0], 0)
    assert math.isfinite(score)
    # after clip+renormalize: [0.001, 0.999, 0.001, 0.001] sums to 1.002
    p = 0.001 / 1.002
    assert score == pytest.approx(100.0 * math.log(p * n) / math.log(n), abs=1e-9)
    assert score < -390.0   # the worst score N=4 can pay is ~ -398.4


def test_clip_renormalize_matches_the_serializer_window() -> None:
    out = mc.clip_renormalize([0.0, 1.0])
    assert sum(out) == pytest.approx(1.0, abs=1e-12)
    assert min(out) >= mc.PLATFORM_MIN_P / 1.002
    assert mc.clip_renormalize([0.2, 0.3, 0.5]) == pytest.approx([0.2, 0.3, 0.5], abs=1e-12)
    with pytest.raises(ValueError):
        mc.clip_renormalize([])


def test_options_at_time_can_be_smaller_than_the_vector() -> None:
    """N is options ACTIVE at forecast time (sum(~np.isnan(pmf))), not the final count."""
    probs = [0.5, 0.3, 0.2]
    assert mc.mc_baseline_score(probs, 0, n_options=2) == pytest.approx(
        100.0 * math.log(0.5 * 2) / math.log(2), abs=1e-9
    )
    assert mc.mc_baseline_score(probs, 0, n_options=2) == pytest.approx(0.0, abs=1e-12)


def test_degenerate_option_counts_raise() -> None:
    with pytest.raises(ValueError):
        mc.mc_baseline_score([1.0], 0)
    with pytest.raises(ValueError):
        mc.mc_baseline_score([0.5, 0.5], 2)


def test_peer_score_is_100_not_50_and_carries_the_small_field_correction() -> None:
    """score_math.py halves ONLY QUESTION_CONTINUOUS_TYPES, so MC peer keeps the 100."""
    p, gmp, n = 0.6, 0.3, 10
    assert mc.mc_peer_score(p, gmp, n) == pytest.approx(
        100.0 * (10 / 9) * math.log(2.0), abs=1e-9
    )
    assert mc.mc_peer_score(0.4, 0.4, 5) == pytest.approx(0.0, abs=1e-12)
    with pytest.raises(ValueError):
        mc.mc_peer_score(0.5, 0.5, 1)


# --- label matching: report, never guess ------------------------------------------------

OPTIONS = ["Justin J. Pearson", "London Lamar", "M. LaTroy Alexandria-Williams",
           "Jim Torino", "Any other candidate"]


def test_exact_match_wins() -> None:
    assert mc.match_option("London Lamar", OPTIONS) == (1, "exact")


def test_whitespace_and_case_are_forgiven() -> None:
    assert mc.match_option("  london   lamar ", OPTIONS) == (1, "casefold")


def test_truncated_platform_label_matches_by_prefix() -> None:
    idx, how = mc.match_option("M. LaTroy Alexandria", OPTIONS)
    assert (idx, how) == (2, "prefix")
    # and the other direction: our stored label is the truncated one
    idx, how = mc.match_option("Any other candidate (write-in)", OPTIONS)
    assert (idx, how) == (4, "prefix")


def test_ambiguous_prefix_is_reported_not_guessed() -> None:
    idx, how = mc.match_option("Jo", ["Joe Biden", "John Smith"])
    assert idx is None
    assert "ambiguous" in how


def test_unknown_label_is_reported() -> None:
    assert mc.match_option("Somebody Else", OPTIONS) == (None, "no match")


# --- wave plumbing ----------------------------------------------------------------------


def test_load_resolutions_keeps_only_string_outcomes(tmp_path: Path) -> None:
    """MC outcomes are labels; numeric/binary outcomes in the same file belong elsewhere."""
    path = tmp_path / "res.json"
    path.write_text(
        '{"1": "Option A", "2": 12.5, "3": 1, "4": "annulled", "5": "Ambiguous"}',
        encoding="utf-8",
    )
    assert mc.load_resolutions([path]) == {1: "Option A"}


def test_score_wave_scores_matches_and_reports_the_rest() -> None:
    rows = [
        {"source": {"question_id": 44983}, "question": "ETH vs SOL",
         "options": ["Ethereum (ETH)", "Solana (SOL)", "Neither - they are equal"],
         "probabilities": [0.655, 0.34, 0.005]},
        {"source": {"question_id": 999}, "question": "bad label",
         "options": ["A", "B"], "probabilities": [0.5, 0.5]},
        {"source": {"question_id": 1000}, "question": "length mismatch",
         "options": ["A", "B", "C"], "probabilities": [0.5, 0.5]},
    ]
    resolutions = {44983: "Ethereum (ETH)", 999: "C", 1000: "A"}
    scored, unmatched = mc.score_wave(rows, resolutions)

    assert len(scored) == 1
    assert scored[0]["score"] == pytest.approx(61.48595389736174, abs=0.05)
    assert scored[0]["argmax_hit"] is True
    assert scored[0]["top2_hit"] is True
    assert scored[0]["uniform"] == pytest.approx(1 / 3)

    assert {m["qid"] for m in unmatched} == {999, 1000}
    assert any("no match" in m["why"] for m in unmatched)
    assert any("probabilities" in m["why"] for m in unmatched)


def test_load_mc_rows_takes_the_latest_row_per_question(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    journal.write_text(
        '{"question_type": "multiple_choice", "forecast_at": "2026-07-01T00:00:00+00:00",'
        ' "source": {"question_id": 7}, "options": ["A", "B"], "probabilities": [0.9, 0.1]}\n'
        '{"question_type": "multiple_choice", "forecast_at": "2026-07-05T00:00:00+00:00",'
        ' "source": {"question_id": 7}, "options": ["A", "B"], "probabilities": [0.2, 0.8]}\n'
        '{"question_type": "binary", "forecast_at": "2026-07-05T00:00:00+00:00",'
        ' "source": {"question_id": 8}, "probability": 0.3}\n',
        encoding="utf-8",
    )
    rows = mc.load_mc_rows(journal)
    assert len(rows) == 1
    assert rows[0]["probabilities"] == [0.2, 0.8]


def test_cli_runs_on_the_shipped_wave(capsys: pytest.CaptureFixture[str]) -> None:
    repo = Path(__file__).resolve().parents[1]
    resolutions = repo / "bench" / "analysis" / "minibench-2026-07-27-resolutions.json"
    journal = repo / "bot" / "journal" / "forecasts.jsonl"
    if not (resolutions.exists() and journal.exists()):
        pytest.skip("wave fixtures not present")
    assert mc.main(["--resolutions", str(resolutions), "--journal", str(journal)]) == 0
    out = capsys.readouterr().out
    assert "44983" in out
    assert "mean baseline" in out
