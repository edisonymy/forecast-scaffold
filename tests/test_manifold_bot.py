"""Tests for the Manifold bot (bot/run_manifold.py) and scorer (bot/score_manifold.py).

Every API call and every agent call is stubbed — nothing here touches the network. Covered:
selection filters (bettor floor, close-time window, meme regex, diversity cap), the blind
brief hiding the price while the blind agent-cmd blocks manifold.markets, the sighted brief
carrying the price and the required judgment language, the divergence/stake gates, the
dry-run path never POSTing, both modes landing in the journal with a dry_run flag, and the
scorer's movement-toward math in both directions.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

import run_bot  # noqa: E402
import run_manifold  # noqa: E402
import score_manifold  # noqa: E402

from forecast_scaffold.core import ForecastRecord, Journal  # noqa: E402

NOW_MS = 1_780_000_000_000  # a fixed "now" so close-time windows are deterministic
DAY_MS = run_manifold.DAY_MS


def mk(market_id: str = "m1", **over: Any) -> dict[str, Any]:
    """A market dict that passes every selection filter; override to exercise one filter."""
    base: dict[str, Any] = {
        "id": market_id,
        "question": "Will the Fed cut rates at the next meeting?",
        "outcomeType": "BINARY",
        "mechanism": "cpmm-1",
        "isResolved": False,
        "closeTime": NOW_MS + 10 * DAY_MS,
        "uniqueBettorCount": 100,
        "volume24Hours": 1000.0,
        "textDescription": "Resolves YES if the Fed lowers the target rate.",
        "groupSlugs": ["economics"],
        "probability": 0.40,
        "url": f"https://manifold.markets/x/{market_id}",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- selection


def test_selection_happy_path_and_volume_ranking() -> None:
    markets = [
        mk("low", volume24Hours=10.0, groupSlugs=["a"]),
        mk("high", volume24Hours=9000.0, groupSlugs=["b"]),
        mk("mid", volume24Hours=500.0, groupSlugs=["c"]),
    ]
    picked = run_manifold.select_markets(markets, limit=10, now_ms=NOW_MS)
    assert [m["id"] for m in picked] == ["high", "mid", "low"]  # 24h-volume desc


def test_selection_bettor_floor() -> None:
    below = run_manifold.select_markets([mk(uniqueBettorCount=49)], 10, NOW_MS)
    at = run_manifold.select_markets([mk(uniqueBettorCount=50)], 10, NOW_MS)
    assert below == [] and len(at) == 1


def test_selection_close_time_window() -> None:
    too_soon = mk("soon", closeTime=NOW_MS + 2 * DAY_MS)
    too_far = mk("far", closeTime=NOW_MS + 61 * DAY_MS)
    no_close = mk("none", closeTime=None)
    in_window = mk("ok", closeTime=NOW_MS + 30 * DAY_MS)
    picked = run_manifold.select_markets(
        [too_soon, too_far, no_close, in_window], 10, NOW_MS
    )
    assert [m["id"] for m in picked] == ["ok"]


@pytest.mark.parametrize(
    "question",
    [
        "Will this market resolve YES by Friday?",
        "Will I finish writing my novel?",
        "Does my startup hit 1000 users?",
        "Will @manifoldbot tip me today?",
    ],
)
def test_selection_meme_regex_excludes(question: str) -> None:
    assert run_manifold.select_markets([mk(question=question)], 10, NOW_MS) == []


def test_selection_meme_regex_keeps_ordinary_questions() -> None:
    # "army" must not trip the \bmy rule; "will it" must not trip "will i".
    keep = mk(question="Will the army win the election and will it hold power?")
    assert len(run_manifold.select_markets([keep], 10, NOW_MS)) == 1


def test_selection_empty_criteria_excluded() -> None:
    assert run_manifold.select_markets([mk(textDescription="   ")], 10, NOW_MS) == []


def test_selection_non_binary_or_non_cpmm_excluded() -> None:
    multi = mk("multi", outcomeType="MULTIPLE_CHOICE")
    dpm = mk("dpm", mechanism="dpm-2")
    resolved = mk("done", isResolved=True)
    assert run_manifold.select_markets([multi, dpm, resolved], 10, NOW_MS) == []


def test_selection_diversity_cap() -> None:
    # Five markets sharing one top tag; only DIVERSITY_CAP (3) may be taken.
    same = [
        mk(f"p{i}", groupSlugs=["politics"], volume24Hours=1000.0 - i)
        for i in range(5)
    ]
    picked = run_manifold.select_markets(same, limit=10, now_ms=NOW_MS)
    assert len(picked) == run_manifold.DIVERSITY_CAP
    # Kept the highest-volume three (volume desc).
    assert [m["id"] for m in picked] == ["p0", "p1", "p2"]


def test_selection_untagged_not_capped() -> None:
    untagged = [mk(f"u{i}", groupSlugs=[], volume24Hours=1000.0 - i) for i in range(5)]
    picked = run_manifold.select_markets(untagged, limit=10, now_ms=NOW_MS)
    assert len(picked) == 5  # no shared tag -> the cap never applies


def test_selection_limit_is_honored() -> None:
    markets = [mk(f"m{i}", groupSlugs=[f"g{i}"], volume24Hours=1000.0 - i) for i in range(6)]
    assert len(run_manifold.select_markets(markets, limit=2, now_ms=NOW_MS)) == 2


# --------------------------------------------------------------------------- briefs


def test_blind_brief_hides_price_and_volume() -> None:
    market = mk(probability=0.37, volume24Hours=4242.0, uniqueBettorCount=321)
    brief = run_manifold.build_manifold_brief(market, sighted=False)
    assert "Market signals" not in brief
    assert "Current market probability" not in brief
    assert "0.37" not in brief and "4242" not in brief and "321" not in brief
    assert "Resolution criteria" in brief  # the contract is still there


def test_blind_agent_cmd_blocks_manifold() -> None:
    base = run_manifold.DEFAULT_AGENT_CMD
    blind_cmd = run_manifold.agent_cmd_for(base, blind=True)
    sighted_cmd = run_manifold.agent_cmd_for(base, blind=False)
    assert "manifold.markets" in blind_cmd
    assert "metaculus.com" in blind_cmd  # the whole aggregator block travels together
    assert "manifold.markets" not in sighted_cmd


def test_sighted_brief_has_price_and_judgment_language() -> None:
    market = mk(probability=0.37, volume24Hours=4242.0, uniqueBettorCount=321)
    brief = run_manifold.build_manifold_brief(market, sighted=True)
    assert "Current market probability: 0.37" in brief
    assert "4242" in brief and "321" in brief
    # The v0.4.11 crowd-signals framing, adapted: REQUIRED step, a judgment call, and the
    # herding-vs-informed dichotomy the agent must resolve in reasoning.
    assert "REQUIRED" in brief
    assert "judgment call" in brief
    assert "herding" in brief
    assert "SAME contract" in brief


# --------------------------------------------------------------------------- bet gate


def test_decide_bet_divergence_gate() -> None:
    # Below threshold -> no bet.
    assert run_manifold.decide_bet(
        0.55, 0.50, 25, balance=1000, already_positioned=False
    ) is None
    # At/above threshold, forecast higher -> YES.
    up = run_manifold.decide_bet(0.60, 0.50, 25, balance=1000, already_positioned=False)
    assert up == {"outcome": "YES", "stake": 25.0}
    # Forecast lower -> NO.
    down = run_manifold.decide_bet(0.30, 0.50, 25, balance=1000, already_positioned=False)
    assert down == {"outcome": "NO", "stake": 25.0}


def test_decide_bet_balance_floor_and_position_guard() -> None:
    assert run_manifold.decide_bet(
        0.90, 0.30, 25, balance=199, already_positioned=False
    ) is None
    assert run_manifold.decide_bet(
        0.90, 0.30, 25, balance=1000, already_positioned=True
    ) is None


# --------------------------------------------------------------------------- run loop


def fenced(probability: float, market_read: str | None = "herding") -> str:
    payload: dict[str, Any] = {
        "probability": probability,
        "reasoning": "stub reasoning",
        "reference_class": "past comparable cases",
        "base_rate": 0.4,
        "raw_draws": [probability],
        "sources": ["https://example.com/a", "https://example.com/b"],
        "what_would_change_my_mind": ["new data"],
    }
    # A sighted payload carries a market_read (the trading gate); the blind run ignores it.
    # None simulates an agent that omitted the required field (drives the repair path).
    if market_read is not None:
        payload["market_read"] = market_read
    return f"```json\n{json.dumps(payload)}\n```"


class ScriptedAgent:
    """Stands in for run_bot.run_agent. Returns a constant forecast; records calls."""

    def __init__(self, probability: float, market_read: str | None = "herding") -> None:
        self.probability = probability
        self.market_read = market_read
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        self.calls.append({"cmd": cmd, "prompt": prompt, "system": system})
        return fenced(self.probability, self.market_read), 0.01, "claude-sonnet-5"


class BetSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, float]] = []

    def __call__(self, api_key: str, market_id: str, outcome: str, amount: float) -> dict:
        self.calls.append((api_key, market_id, outcome, amount))
        return {"betId": "x"}


def make_args(tmp_path: Path, **over: Any) -> argparse.Namespace:
    base = dict(
        limit=10, tier="medium", live=False, stake=25.0, max_bets=10,
        provider="subscription", timeout=60, agent_cmd=run_manifold.DEFAULT_AGENT_CMD,
        journal=str(tmp_path / "manifold.jsonl"),
        phase_file=str(tmp_path / "manifold-phase.json"),
    )
    base.update(over)
    return argparse.Namespace(**base)


def seed_phase(tmp_path: Path, phase: int = 1, killed: bool = False) -> str:
    """Write a phase file so a test can run in a chosen phase (fresh runs start at phase 0)."""
    path = tmp_path / "manifold-phase.json"
    run_manifold.save_phase(path, {"phase": phase, "killed": killed, "history": []})
    return str(path)


def read_journal(path: str) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_run_dry_run_journals_both_modes_and_never_posts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("mkt1", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)

    args = make_args(tmp_path)  # dry-run (live=False)
    assert run_manifold.run(args) == 0

    rows = read_journal(args.journal)
    assert len(rows) == 2  # one blind, one sighted
    by_mode = {r["source"]["mode"]: r for r in rows}
    assert by_mode["blind"]["blind"] is True
    assert by_mode["sighted"]["blind"] is False
    # Shared pair id links the two records.
    assert by_mode["blind"]["source"]["pair_id"] == by_mode["sighted"]["source"]["pair_id"]
    # Both carry the dry_run provenance flag and the market price at forecast time.
    assert all(r["dry_run"] is True for r in rows)
    assert by_mode["sighted"]["crowd"]["value"] == 0.30
    assert by_mode["sighted"]["crowd"]["shown_to_agent"] is True
    assert by_mode["blind"]["crowd"]["shown_to_agent"] is False
    # Divergence 0.90 vs 0.30 -> a would-be YES bet is journaled, but NOTHING was POSTed.
    bet = by_mode["sighted"]["source"]["bet"]
    assert bet["outcome"] == "YES" and bet["stake"] == 25.0 and bet["dry_run"] is True
    assert spy.calls == []  # dry-run never POSTs


def test_run_live_posts_and_respects_max_bets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    markets = [
        mk("a", probability=0.30, groupSlugs=["x"]),
        mk("b", probability=0.30, groupSlugs=["y"]),
    ]
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: markets)
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    seed_phase(tmp_path, phase=1)  # phase 0 would force dry-run; phase 1 flat-stakes live
    args = make_args(tmp_path, live=True, max_bets=1)
    assert run_manifold.run(args) == 0

    # Both markets diverge, but the per-run cap allows only one live bet.
    assert len(spy.calls) == 1
    assert spy.calls[0][1] == "a"  # first market
    rows = read_journal(args.journal)
    sighted = [r for r in rows if r["source"]["mode"] == "sighted"]
    with_bet = [r for r in sighted if "bet" in r["source"]]
    assert len(with_bet) == 1
    assert with_bet[0]["source"]["bet"]["dry_run"] is False
    assert all(r["dry_run"] is False for r in rows)  # live provenance


def test_run_skips_market_with_existing_position(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("held", probability=0.30, groupSlugs=["z"])
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    # Pre-seed the journal with an existing bet on this market.
    journal = Journal(str(tmp_path / "manifold.jsonl"))
    journal.append(ForecastRecord(
        question="held market", question_type="binary", probability=0.5,
        source={"platform": "manifold", "question_id": "held", "pair_id": "old",
                "bet": {"outcome": "YES", "stake": 25, "dry_run": False}},
    ))

    seed_phase(tmp_path, phase=1)
    args = make_args(tmp_path, live=True)
    assert run_manifold.run(args) == 0
    assert spy.calls == []  # already positioned -> no new bet


# --------------------------------------------------------------------------- scoring math


@pytest.mark.parametrize(
    "p_us,p_0,p_now,expected",
    [
        (0.90, 0.30, 0.50, +0.20),   # we're high; market rose toward us
        (0.90, 0.30, 0.20, -0.10),   # we're high; market fell away
        (0.10, 0.70, 0.50, +0.20),   # we're low; market fell toward us
        (0.10, 0.70, 0.90, -0.20),   # we're low; market rose away
    ],
)
def test_movement_toward_both_directions(
    p_us: float, p_0: float, p_now: float, expected: float
) -> None:
    assert score_manifold.movement_toward(p_us, p_0, p_now) == pytest.approx(expected)


def test_movement_toward_skips_low_divergence() -> None:
    assert score_manifold.movement_toward(0.72, 0.70, 0.90) is None


def test_bet_pnl_share_model() -> None:
    # YES bought at 0.30, resolves YES -> stake*(1/0.3 - 1).
    assert score_manifold.bet_pnl("YES", 25, 0.30, 1.0) == pytest.approx(25 * (1 / 0.3 - 1))
    # YES resolves NO -> total loss.
    assert score_manifold.bet_pnl("YES", 25, 0.30, 0.0) == pytest.approx(-25.0)
    # NO bought at 0.30, resolves NO -> profit.
    assert score_manifold.bet_pnl("NO", 25, 0.30, 0.0) == pytest.approx(
        25 * (1.0 / 0.7 - 1)
    )


def test_brier() -> None:
    assert score_manifold.brier(0.9, True) == pytest.approx(0.01)
    assert score_manifold.brier(0.9, False) == pytest.approx(0.81)


def _pair_records() -> list[ForecastRecord]:
    """A synthetic blind+sighted pair on one market: p_0=0.30, blind=0.85, sighted=0.90."""
    common = dict(question="Will X happen?", question_type="binary")
    blind = ForecastRecord(
        **common, probability=0.85, blind=True,
        crowd={"value": 0.30, "source": "manifold market", "shown_to_agent": False},
        source={"platform": "manifold", "question_id": "m1", "pair_id": "pair1",
                "mode": "blind"},
    )
    sighted = ForecastRecord(
        **common, probability=0.90, blind=False,
        crowd={"value": 0.30, "source": "manifold market", "shown_to_agent": True},
        source={"platform": "manifold", "question_id": "m1", "pair_id": "pair1",
                "mode": "sighted",
                "bet": {"outcome": "YES", "stake": 25, "dry_run": True,
                        "p_market_at_bet": 0.30}},
    )
    return [blind, sighted]


def test_score_rows_open_market_movement() -> None:
    records = _pair_records()
    # Market rose from 0.30 to 0.55 (toward both our high forecasts), still open.
    state = {"probability": 0.55, "resolved": False, "outcome": None}
    result = score_manifold.score_rows(records, lambda mid: state)
    assert result["summary"]["n_pairs"] == 1
    row = result["rows"][0]
    assert row["movement_blind"] == pytest.approx(0.25)
    assert row["movement_sighted"] == pytest.approx(0.25)
    # No resolution yet -> no Brier, bet marked to the open price.
    assert "brier_blind" not in row
    assert row["bet"]["pnl"] == pytest.approx(25 * (0.55 / 0.30 - 1))


def test_score_rows_resolved_market_brier_and_pnl() -> None:
    records = _pair_records()
    state = {"probability": 1.0, "resolved": True, "outcome": True}  # resolved YES
    result = score_manifold.score_rows(records, lambda mid: state)
    row = result["rows"][0]
    assert row["brier_blind"] == pytest.approx((0.85 - 1) ** 2)
    assert row["brier_sighted"] == pytest.approx((0.90 - 1) ** 2)
    # YES bet at 0.30, resolved YES -> marked to 1.0.
    assert row["bet"]["pnl"] == pytest.approx(25 * (1.0 / 0.30 - 1))
    s = result["summary"]
    assert s["n_resolved"] == 1 and s["n_brier_scored"] == 1
    assert s["brier_sighted"] == pytest.approx(0.01)


def test_render_table_smoke() -> None:
    result = score_manifold.score_rows(
        _pair_records(), lambda mid: {"probability": 0.55, "resolved": False, "outcome": None}
    )
    table = score_manifold.render_table(result)
    assert "blind-vs-sighted" in table
    assert "mean movement-toward-us" in table


# --------------------------------------------------------------------------- market_read gate


def test_market_read_validation_missing_and_invalid() -> None:
    # Missing -> a repairable error that lists the allowed values.
    missing = run_manifold.validate_market_read({"probability": 0.6})
    assert missing and "informed" in missing[0] and "herding" in missing[0]
    # An unknown value is equally repairable.
    assert run_manifold.validate_market_read({"market_read": "bogus"})
    # Each allowed value passes; case/whitespace is normalized.
    for value in run_manifold.MARKET_READS:
        assert run_manifold.validate_market_read({"market_read": value}) == []
    assert run_manifold.validate_market_read({"market_read": "  INFORMED "}) == []


def test_run_informed_read_logs_and_skips_bet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    market = mk("inf", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90, market_read="informed"))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    seed_phase(tmp_path, phase=1)
    args = make_args(tmp_path, live=True)
    assert run_manifold.run(args) == 0

    assert spy.calls == []  # an "informed" read never bets, even wildly divergent + live
    rows = read_journal(args.journal)
    sighted = next(r for r in rows if r["source"]["mode"] == "sighted")
    assert sighted["source"]["market_read"] == "informed"
    assert "bet" not in sighted["source"]  # forecast logged, no bet recorded
    assert "market_read=informed" in capsys.readouterr().out  # the one-line marker


def test_run_herding_read_produces_bet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("herd", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90, market_read="stale"))
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())

    args = make_args(tmp_path)  # fresh -> phase 0 dry-run; a would-be bet is still journaled
    assert run_manifold.run(args) == 0
    sighted = next(r for r in read_journal(args.journal) if r["source"]["mode"] == "sighted")
    assert sighted["source"]["market_read"] == "stale"
    assert sighted["source"]["bet"]["outcome"] == "YES"
    assert sighted["source"]["bet"]["dry_run"] is True


def test_run_sighted_missing_market_read_fails_the_pair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("nomr", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    # The agent never returns market_read -> the sighted repair loop exhausts and the pair
    # is dropped (neither mode journaled), exactly like any invalid-payload failure.
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90, market_read=None))
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())

    args = make_args(tmp_path)
    assert run_manifold.run(args) == 0
    # The pair is dropped before either mode is journaled (no file, or an empty one).
    assert not Path(args.journal).exists() or read_journal(args.journal) == []


# --------------------------------------------------------------------------- phase machine


def mf_record(
    pair_id: str, mode: str, p: float, p_market: float, *, market_id: str = "m",
    bet: dict[str, Any] | None = None, market_read: str | None = None,
    forecast_at: str | None = None, resolved: bool | None = None,
) -> ForecastRecord:
    """A synthetic manifold forecast record for the phase-machine tests."""
    src: dict[str, Any] = {
        "platform": "manifold", "question_id": market_id, "pair_id": pair_id, "mode": mode,
    }
    if market_read is not None:
        src["market_read"] = market_read
    if bet is not None:
        src["bet"] = bet
    rec = ForecastRecord(
        question="Will X happen?", question_type="binary", probability=p,
        blind=(mode == "blind"), forecast_at=forecast_at, source=src,
        crowd={"value": p_market, "source": "manifold market",
               "shown_to_agent": mode == "sighted"},
    )
    if resolved is not None:
        rec.status = "resolved"
        rec.resolution = {"outcome": resolved, "resolved_on": "2026-07-01", "note": ""}
    return rec


def test_fresh_run_creates_phase0_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("f0", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())

    args = make_args(tmp_path)
    assert not Path(args.phase_file).exists()
    assert run_manifold.run(args) == 0
    assert Path(args.phase_file).exists()
    state = json.loads(Path(args.phase_file).read_text(encoding="utf-8"))
    assert state["phase"] == 0 and state["killed"] is False


def test_phase0_forces_dry_run_even_with_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("p0", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    args = make_args(tmp_path, live=True)  # fresh journal -> phase 0
    assert run_manifold.run(args) == 0
    assert spy.calls == []  # phase 0 forces dry-run regardless of --live
    sighted = next(r for r in read_journal(args.journal) if r["source"]["mode"] == "sighted")
    assert sighted["dry_run"] is True
    assert sighted["source"]["bet"]["dry_run"] is True  # would-be bet still journaled


def test_phase_promotion_0_to_1_journals_evidence() -> None:
    records: list[ForecastRecord] = []
    for i in range(3):
        pid = f"p{i}"
        records.append(mf_record(pid, "blind", 0.60, 0.40, market_id=f"m{i}"))
        records.append(mf_record(
            pid, "sighted", 0.62, 0.40, market_id=f"m{i}", market_read="herding",
            bet={"outcome": "YES", "stake": 25, "dry_run": True, "p_market_at_bet": 0.40},
        ))
    state = {"phase": 0, "killed": False, "history": []}
    new, transitions = run_manifold.evaluate_promotions(state, records, lambda mid: {})
    assert new["phase"] == 1 and new["killed"] is False
    assert transitions[0]["to"] == 1
    ev = transitions[0]["evidence"]
    assert ev["valid_pairs"] == 3 and ev["bet_decisions_evaluated"] == 3
    # The transition was appended to history with its evidence.
    assert new["history"][-1]["evidence"]["valid_pairs"] == 3


def test_phase_promotion_0_to_1_not_met_without_pairs() -> None:
    # Only 2 valid pairs -> stays at phase 0.
    records: list[ForecastRecord] = []
    for i in range(2):
        pid = f"p{i}"
        records.append(mf_record(pid, "blind", 0.60, 0.40, market_id=f"m{i}"))
        records.append(mf_record(pid, "sighted", 0.62, 0.40, market_id=f"m{i}",
                                 market_read="herding"))
    state = {"phase": 0, "killed": False, "history": []}
    new, transitions = run_manifold.evaluate_promotions(state, records, lambda mid: {})
    assert new["phase"] == 0 and transitions == []


def _movement_journal(
    n: int, n_toward: int, *, now: datetime
) -> tuple[list[ForecastRecord], dict[str, dict[str, Any]]]:
    """n live divergent YES bets (p_us=0.9, entry 0.4), `n_toward` of which the market moved
    toward; plus a lookup mapping each market to its current (open) price."""
    old = (now - timedelta(days=10)).isoformat(timespec="seconds")
    records: list[ForecastRecord] = []
    states: dict[str, dict[str, Any]] = {}
    for i in range(n):
        mid = f"bet{i}"
        p_now = 0.60 if i < n_toward else 0.30  # toward = price rose toward our 0.9
        states[mid] = {"probability": p_now, "resolved": False, "outcome": None}
        records.append(mf_record(
            f"b{i}", "sighted", 0.90, 0.40, market_id=mid, market_read="herding",
            forecast_at=old,
            bet={"outcome": "YES", "stake": 25, "dry_run": False, "p_market_at_bet": 0.40},
        ))
    return records, states


def test_phase_promotion_1_to_2(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 11, tzinfo=UTC)
    records, states = _movement_journal(50, 40, now=now)  # 40/50 toward -> p << 0.05
    # 10 resolved pairs where sighted (0.9) beats blind (0.8) on YES resolutions.
    for i in range(10):
        pid, mid = f"rp{i}", f"res{i}"
        states[mid] = {"probability": 1.0, "resolved": True, "outcome": True}
        records.append(mf_record(pid, "blind", 0.80, 0.50, market_id=mid))
        records.append(mf_record(pid, "sighted", 0.90, 0.50, market_id=mid))

    state = {"phase": 1, "killed": False, "history": []}
    new, transitions = run_manifold.evaluate_promotions(
        state, records, lambda mid: states[mid], now=now
    )
    assert new["phase"] == 2 and new["killed"] is False
    ev = transitions[-1]["evidence"]
    assert ev["n_movement"] == 50 and ev["moved_toward"] == 40 and ev["moved_away"] == 10
    assert ev["binomial_p"] < 0.05
    assert ev["n_resolved_pairs"] == 10
    assert ev["brier_sighted"] <= ev["brier_blind"]


def test_phase_1_to_2_blocked_when_sighted_brier_worse() -> None:
    now = datetime(2026, 7, 11, tzinfo=UTC)
    records, states = _movement_journal(50, 40, now=now)
    # Sighted (0.6) LOSES to blind (0.9) on YES resolutions -> Brier gate fails.
    for i in range(10):
        pid, mid = f"rp{i}", f"res{i}"
        states[mid] = {"probability": 1.0, "resolved": True, "outcome": True}
        records.append(mf_record(pid, "blind", 0.90, 0.50, market_id=mid))
        records.append(mf_record(pid, "sighted", 0.60, 0.50, market_id=mid))
    state = {"phase": 1, "killed": False, "history": []}
    new, transitions = run_manifold.evaluate_promotions(
        state, records, lambda mid: states[mid], now=now
    )
    assert new["phase"] == 1 and transitions == []  # movement passes but Brier blocks


def test_phase_kill_path_sets_killed() -> None:
    now = datetime(2026, 7, 11, tzinfo=UTC)
    records, states = _movement_journal(50, 20, now=now)  # 20/50 toward -> rate 0.4 <= 0.5
    state = {"phase": 1, "killed": False, "history": []}
    new, transitions = run_manifold.evaluate_promotions(
        state, records, lambda mid: states[mid], now=now
    )
    assert new["killed"] is True
    assert transitions[-1]["to"] == "killed"
    assert transitions[-1]["evidence"]["n_movement"] == 50


def test_killed_state_blocks_betting_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("kk", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    seed_phase(tmp_path, phase=2, killed=True)  # killed persists across runs
    args = make_args(tmp_path, live=True)
    assert run_manifold.run(args) == 0
    assert spy.calls == []  # betting disabled permanently; forecasting continues


def test_binomial_p_value_hand_computed() -> None:
    assert run_manifold.binomial_p_value(35, 50) < 0.05      # ~0.0033
    assert run_manifold.binomial_p_value(26, 50) >= 0.05     # ~0.44
    assert run_manifold.binomial_p_value(50, 50) == pytest.approx(0.5 ** 50)
    assert run_manifold.binomial_p_value(0, 0) is None


# --------------------------------------------------------------------------- phase-2 sizing


def test_kelly_stake_cap_and_floor() -> None:
    # p=0.7, m=0.5, balance=2000 -> kelly=0.4; raw=0.25*0.4*2000=200; cap=5%*2000=100.
    assert run_manifold.kelly_stake(0.70, 0.50, 2000) == pytest.approx(100.0)
    # NO-side mirror: kelly=(m-p)/m=0.4 -> same 100.
    assert run_manifold.kelly_stake(0.30, 0.50, 2000) == pytest.approx(100.0)
    # Thin edge + small balance -> raw below 10 -> floored to 10.
    assert run_manifold.kelly_stake(0.58, 0.50, 200) == pytest.approx(10.0)


def test_convergence_exit_and_reforecast_detection() -> None:
    assert run_manifold.should_converge_exit(0.60, 0.58) is True   # within 0.03
    assert run_manifold.should_converge_exit(0.60, 0.50) is False
    # YES entry 0.40, price fell to 0.25 -> 0.15 against us (> 0.10) -> re-forecast.
    assert run_manifold.should_reforecast(0.90, 0.40, 0.25) is True
    assert run_manifold.should_reforecast(0.90, 0.40, 0.55) is False  # moved toward us


def test_open_exposure_counts_only_open_placed_bets() -> None:
    records = [
        mf_record("a", "sighted", 0.9, 0.4, market_id="ma",
                  bet={"outcome": "YES", "stake": 25, "dry_run": False}),      # counts
        mf_record("b", "sighted", 0.9, 0.4, market_id="mb",
                  bet={"outcome": "YES", "stake": 30, "dry_run": True}),       # dry-run: out
        mf_record("c", "sighted", 0.9, 0.4, market_id="mc", resolved=True,
                  bet={"outcome": "YES", "stake": 40, "dry_run": False}),      # resolved: out
        mf_record("d", "sighted", 0.9, 0.4, market_id="md"),                   # no bet: out
    ]
    assert run_manifold.open_exposure(records) == pytest.approx(25.0)


def test_exposure_cap_blocks_a_bet_over_30pct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("e", probability=0.30, groupSlugs=["x"])
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 1200.0)  # 30% = 360
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    # Pre-seed 350 mana of open exposure on a different market: 350 + 25 > 360 -> block.
    journal = Journal(str(tmp_path / "manifold.jsonl"))
    journal.append(ForecastRecord(
        question="prior open bet", question_type="binary", probability=0.9,
        source={"platform": "manifold", "question_id": "other", "pair_id": "old",
                "bet": {"outcome": "YES", "stake": 350, "dry_run": False}},
    ))
    seed_phase(tmp_path, phase=1)
    args = make_args(tmp_path, live=True)
    assert run_manifold.run(args) == 0
    assert spy.calls == []  # the exposure cap refused the bet


def test_phase_load_save_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "phase.json"
    assert run_manifold.load_phase(path) == {"phase": 0, "killed": False, "history": []}
    state = {"phase": 2, "killed": False, "history": [{"to": 1, "at": "t", "evidence": {}}]}
    run_manifold.save_phase(path, state)
    assert run_manifold.load_phase(path) == state
