"""Post-hoc logistic recalibration (Platt scaling) — the recalibration layer in core.py
plus its `calibrate-fit` CLI and the (inert-by-default) bot wiring.

The layer ships INERT: with no recalibration.json present, load returns the identity map and
apply is a byte-exact no-op, so an unfitted deployment behaves exactly as before.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

from forecast_scaffold.core import (
    RECAL_MIN_N,
    ForecastRecord,
    Journal,
    _logit,
    _sigmoid,
    apply_recalibration,
    extremize_logodds,
    fit_platt,
    load_recalibration,
    main,
    recalibration_cv,
)

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- fixtures


def _spread(n: int) -> list[float]:
    """n probabilities spread evenly over the open interval (0, 1)."""
    return [(i + 0.5) / n for i in range(n)]


def overconfident_pairs(n: int = 600, seed: int = 0) -> list[tuple[float, bool]]:
    """Recorded probabilities are OVERconfident: outcomes are drawn from the shrunk truth
    sigmoid(0.5*logit(p)), so the honest fit recovers a slope a ~ 0.5 (< 1)."""
    rng = random.Random(seed)
    out = []
    for p in _spread(n):
        out.append((p, rng.random() < _sigmoid(0.5 * _logit(p))))
    return out


def calibrated_pairs(n: int = 200, seed: int = 1) -> list[tuple[float, bool]]:
    """Already well-calibrated: outcome ~ Bernoulli(p). Recalibration should NOT help."""
    rng = random.Random(seed)
    return [(p, rng.random() < p) for p in _spread(n)]


def resolved_records(pairs: list[tuple[float, bool]]) -> list[ForecastRecord]:
    recs = []
    for i, (p, o) in enumerate(pairs):
        r = ForecastRecord(question=f"Q{i}?", probability=p, resolve_by="2026-01-01")
        r.resolve(bool(o))
        recs.append(r)
    return recs


def write_journal(path: Path, pairs: list[tuple[float, bool]]) -> None:
    j = Journal(str(path))
    for r in resolved_records(pairs):
        j.append(r)


# --------------------------------------------------------------------------- fit / apply


def test_fit_recovers_half_slope() -> None:
    a, b = fit_platt(overconfident_pairs())
    # true slope is 0.5; finite-sample noise keeps it comfortably inside a wide band
    assert a == pytest.approx(0.5, abs=0.15)
    assert abs(b) < 0.25
    # record the recovered slope for the caller
    print(f"recovered slope a = {a:.4f}")


def test_fit_identity_below_min_n() -> None:
    # A 2-param fit needs more than the direction check's MIN_CALIBRATION_N=5.
    assert fit_platt(overconfident_pairs()[: RECAL_MIN_N - 1]) == (1.0, 0.0)
    assert fit_platt([]) == (1.0, 0.0)


def test_apply_is_exact_identity_at_1_0() -> None:
    for p in (0.0, 0.02, 0.37, 0.5, 0.9, 1.0):
        assert apply_recalibration(p, 1.0, 0.0) == p  # not merely approx — exact no-op


def test_apply_shrinks_when_overconfident() -> None:
    a, b = fit_platt(overconfident_pairs())
    assert a < 1.0
    # a<1 pulls a confident call back toward 0.5
    assert 0.5 < apply_recalibration(0.9, a, b) < 0.9
    assert 0.1 < apply_recalibration(0.1, a, b) < 0.5


def test_apply_clamps_to_defaults_band() -> None:
    # A strong stretch on an extreme p is held inside DEFAULTS clamp [0.02, 0.98].
    assert apply_recalibration(0.999, 3.0, 0.0) <= 0.98
    assert apply_recalibration(0.001, 3.0, 0.0) >= 0.02


def test_apply_caller_clamp_band_does_not_tighten() -> None:
    # The bot submits inside [0.01, 0.99] (run_bot's final binary clamp). Passing that band
    # means ACTIVATING recalibration never tightens it: a stretch fit pushes 0.95 above the
    # DEFAULTS 0.98 ceiling and is capped at the caller's own 0.99 instead.
    stretched = apply_recalibration(0.95, 3.0, 0.0, clamp_band=(0.01, 0.99))
    assert stretched > 0.98
    assert stretched == pytest.approx(0.99)


def test_extremize_pushes_away_from_half_and_is_not_wired() -> None:
    # Data-free AIA fallback: sigmoid(sqrt(3)*logit(p)) moves AWAY from 0.5.
    assert extremize_logodds(0.9) > 0.9
    assert extremize_logodds(0.1) < 0.1
    assert extremize_logodds(0.5) == pytest.approx(0.5)


# --------------------------------------------------------------------------- CV


def test_cv_reports_out_of_sample_improvement_on_overconfident() -> None:
    cv = recalibration_cv(overconfident_pairs())
    assert set(cv) == {"raw_brier", "recal_brier", "delta", "mean_slope"}
    assert cv["delta"] < 0  # recalibration helps out-of-sample
    assert cv["delta"] == pytest.approx(cv["recal_brier"] - cv["raw_brier"])
    assert cv["mean_slope"] < 1.0


def test_cv_shows_no_gain_on_already_calibrated() -> None:
    cv = recalibration_cv(calibrated_pairs())
    assert cv["delta"] >= 0  # nothing to gain — must not emit params off this


# --------------------------------------------------------------------------- load


def test_load_missing_file_returns_identity(tmp_path: Path) -> None:
    assert load_recalibration(tmp_path / "nope.json") == (1.0, 0.0)


def test_load_malformed_file_returns_identity(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_recalibration(bad) == (1.0, 0.0)
    missing_keys = tmp_path / "partial.json"
    missing_keys.write_text('{"b": 0.1}', encoding="utf-8")
    assert load_recalibration(missing_keys) == (1.0, 0.0)


# --------------------------------------------------------------------------- calibrate-fit CLI


def test_calibrate_fit_writes_and_load_round_trips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "j.jsonl"
    out = tmp_path / "recal.json"
    write_journal(journal, overconfident_pairs())
    code = main(["calibrate-fit", "--journal", str(journal), "--out", str(out)])
    assert code == 0
    assert out.exists()
    params = json.loads(out.read_text(encoding="utf-8"))
    assert params["n"] == 600
    assert params["cv_delta"] < 0
    assert params["scaffold_version"]
    assert "fit_at" in params
    # the fitted map round-trips through load_recalibration
    a, b = load_recalibration(out)
    assert (a, b) == (params["a"], params["b"])
    assert a < 1.0  # overconfident -> shrinking
    text = capsys.readouterr().out
    assert "overconfident" in text
    assert "wrote" in text


def test_calibrate_fit_refuses_on_already_calibrated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "j.jsonl"
    out = tmp_path / "recal.json"
    write_journal(journal, calibrated_pairs())  # delta >= 0
    code = main(["calibrate-fit", "--journal", str(journal), "--out", str(out)])
    assert code == 0
    assert not out.exists()  # nothing written
    assert "NOT emitting" in capsys.readouterr().out


def test_calibrate_fit_refuses_below_min_n(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "j.jsonl"
    out = tmp_path / "recal.json"
    write_journal(journal, overconfident_pairs(n=RECAL_MIN_N - 1))
    code = main(["calibrate-fit", "--journal", str(journal), "--out", str(out)])
    assert code == 0
    assert not out.exists()
    assert "RECAL_MIN_N" in capsys.readouterr().out


# --------------------------------------------------------------------------- bot wiring
#
# The bot applies recalibration to the pooled binary probability before submission, loading
# params from run_bot.RECAL_PARAMS_PATH. With no file there the step is a byte-exact no-op.

sys.path.insert(0, str(ROOT / "bot"))
import run_bot  # noqa: E402

from forecast_scaffold.core import DEFAULTS, geo_mean_odds  # noqa: E402

POST = {"id": 1, "title": "Will X happen?"}
QUESTION = {
    "id": 1,
    "type": "binary",
    "title": "Will X happen?",
    "resolution_criteria": "Resolves YES per source S.",
    "scheduled_resolve_time": "2026-12-15T00:00:00Z",
}


def _fenced(payload: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


class _ScriptedAgent:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)

    def __call__(self, cmd, prompt, system, timeout, provider="subscription"):
        return self.outputs.pop(0), 0.05, "claude-sonnet-5"


class _StubClient:
    def community_prediction(self, question: dict[str, Any]) -> None:
        return None


RESEARCH = {
    "probability": 0.30,
    "dossier": "- fact A (src, 2026)\n- fact B (src, 2026)",
    "reasoning": "researched",
    "sources": ["https://example.com/a"],
    "reference_class": "class R",
    "base_rate": 0.2,
}


def _reasoning(p: float) -> dict[str, Any]:
    return {"probability": p, "reasoning": "x", "sources": [], "named_scenarios": []}


def _run_bot_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, params_path: Path
) -> dict[str, Any]:
    monkeypatch.setattr(
        run_bot,
        "run_agent",
        _ScriptedAgent([_fenced(RESEARCH), _fenced(_reasoning(0.20)), _fenced(_reasoning(0.40))]),
    )
    monkeypatch.setattr(run_bot, "verify_dossier", lambda *a, **k: ("", 0.0))
    monkeypatch.setattr(run_bot, "RECAL_PARAMS_PATH", params_path)
    args = argparse.Namespace(
        blind=False, effort="medium", provider="subscription", timeout=60,
        dry_run=True, comment=False, budget=0.0,
        agent_cmd="claude -p --model claude-sonnet-5 --output-format json",
    )
    config = json.loads(json.dumps(DEFAULTS))
    config["tiers"] = {"medium": {"draws": 5, "searches": 5, "runs": 3}}
    journal_path = tmp_path / "j.jsonl"
    ok = run_bot.forecast_question(
        _StubClient(), POST, QUESTION, args, config, Journal(str(journal_path)), {"usd": 0.0},
        None,
    )
    assert ok
    return json.loads(journal_path.read_text(encoding="utf-8").splitlines()[-1])


def test_bot_no_params_is_byte_identical_no_raw_probability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _run_bot_once(monkeypatch, tmp_path, tmp_path / "absent.json")
    pooled = geo_mean_odds([0.30, 0.20, 0.40])
    assert record["probability"] == pytest.approx(pooled)
    # inert: no recalibration happened, so the raw field is absent from the journal line
    assert "raw_probability" not in record


def test_bot_journals_raw_and_recalibrated_when_params_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A real fitted-and-improving params file (overconfident history -> a<1).
    fit_journal = tmp_path / "hist.jsonl"
    params = tmp_path / "recal.json"
    write_journal(fit_journal, overconfident_pairs())
    assert main(["calibrate-fit", "--journal", str(fit_journal), "--out", str(params)]) == 0
    assert params.exists()

    record = _run_bot_once(monkeypatch, tmp_path, params)
    pooled = geo_mean_odds([0.30, 0.20, 0.40])
    a, b = load_recalibration(params)
    expected = apply_recalibration(pooled, a, b)
    # both numbers journaled; probability is the recalibrated one, raw_probability the pooled
    assert record["raw_probability"] == pytest.approx(pooled)
    assert record["probability"] == pytest.approx(expected)
    assert record["raw_probability"] != record["probability"]
