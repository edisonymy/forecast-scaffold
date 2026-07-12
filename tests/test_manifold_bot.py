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
import shlex
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
    # [AMENDED 2026-07-11] the floor is 25 unique bettors (was 50).
    below = run_manifold.select_markets([mk(uniqueBettorCount=24)], 10, NOW_MS)
    at = run_manifold.select_markets([mk(uniqueBettorCount=25)], 10, NOW_MS)
    assert below == [] and len(at) == 1


def test_selection_half_top_half_mid_band() -> None:
    # [AMENDED 2026-07-11] a batch is drawn half from the top of the volume ranking and half
    # from the mid-band (ranks limit..limit*4), skipping the upper-middle ranks between them.
    # 8 eligible markets, distinct tags, strictly descending volume; limit=4 -> top_k=2,
    # mid_k=2. Top band picks m0,m1; the mid band (candidates[4:16]) picks m4,m5; m2,m3 in the
    # gap are deliberately skipped.
    markets = [
        mk(f"m{i}", groupSlugs=[f"g{i}"], volume24Hours=float(100 - i)) for i in range(8)
    ]
    picked = run_manifold.select_markets(markets, limit=4, now_ms=NOW_MS)
    assert [m["id"] for m in picked] == ["m0", "m1", "m4", "m5"]


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


def test_with_credit_cap_replaces_caller_value() -> None:
    cmd = run_manifold.with_credit_cap(
        run_manifold.DEFAULT_AGENT_CMD + " --max-budget-usd 99", 1.2345678
    )
    tokens = shlex.split(cmd)
    assert tokens.count("--max-budget-usd") == 1
    i = tokens.index("--max-budget-usd")
    assert tokens[i + 1] == "1.234568"


def test_subscription_auth_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in run_manifold.METERED_AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert run_manifold.subscription_auth_error(require_oauth=False) is None
    assert "CLAUDE_CODE_OAUTH_TOKEN" in (
        run_manifold.subscription_auth_error(require_oauth=True) or ""
    )
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-oauth")
    assert run_manifold.subscription_auth_error(require_oauth=True) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-metered-key")
    assert "ANTHROPIC_API_KEY" in (
        run_manifold.subscription_auth_error(require_oauth=True) or ""
    )


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
    # [AMENDED 2026-07-11] the divergence floor is 0.05 (was 0.08).
    # Below threshold (0.03 < 0.05) -> no bet.
    assert run_manifold.decide_bet(
        0.53, 0.50, 25, balance=1000, already_positioned=False
    ) is None
    # Exactly at the 0.05 threshold -> a bet.
    at = run_manifold.decide_bet(0.55, 0.50, 25, balance=1000, already_positioned=False)
    assert at == {"outcome": "YES", "stake": 25.0}
    # Above threshold, forecast higher -> YES.
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


# The medium tier's min_sources floor is 3; the default stub payload clears it.
DEFAULT_SOURCES = ["https://example.com/a", "https://example.com/b", "https://example.com/c"]

# A still-open market state for stubbing score_manifold.fetch_market_state: live-path runs
# consult live state for the exposure cap, and a test must never let that hit the network.
OPEN_STATE = {"probability": 0.50, "resolved": False, "outcome": None}


def fenced(
    probability: float, market_read: str | None = "herding",
    sources: list[str] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "probability": probability,
        "reasoning": "stub reasoning",
        "reference_class": "past comparable cases",
        "base_rate": 0.4,
        "raw_draws": [probability],
        "sources": DEFAULT_SOURCES if sources is None else sources,
        "what_would_change_my_mind": ["new data"],
    }
    # A sighted payload carries a market_read (journaled as a preregistered hypothesis, no
    # longer a bet gate); the blind run ignores it. None simulates an agent that omitted the
    # required field (drives the repair path).
    if market_read is not None:
        payload["market_read"] = market_read
    return f"```json\n{json.dumps(payload)}\n```"


class ScriptedAgent:
    """Stands in for run_bot.run_agent. Returns a constant forecast; records calls."""

    def __init__(
        self, probability: float, market_read: str | None = "herding",
        sources: list[str] | None = None,
    ) -> None:
        self.probability = probability
        self.market_read = market_read
        self.sources = DEFAULT_SOURCES if sources is None else sources
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        self.calls.append({"cmd": cmd, "prompt": prompt, "system": system})
        return fenced(self.probability, self.market_read, self.sources), 0.01, "claude-sonnet-5"


class CostAgent(ScriptedAgent):
    """Valid forecasts with a scripted positive cost for budget-boundary tests."""

    def __init__(self, costs: list[float]) -> None:
        super().__init__(0.90)
        self.costs = costs

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        index = len(self.calls)
        self.calls.append({"cmd": cmd, "prompt": prompt, "system": system})
        return fenced(self.probability, self.market_read, self.sources), self.costs[index], (
            "claude-sonnet-5"
        )


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
        budget=run_manifold.MAX_CREDIT_BUDGET_USD, deadline_minutes=0.0,
        require_subscription_auth=False,
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


def test_run_rejects_non_subscription_before_any_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        run_bot, "load_config", lambda: pytest.fail("preflight must precede repo/network work")
    )
    assert run_manifold.run(make_args(tmp_path, provider="openrouter")) == 2


def test_run_rejects_budget_above_operator_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        run_bot, "load_config", lambda: pytest.fail("budget check must precede work")
    )
    assert run_manifold.run(
        make_args(tmp_path, budget=run_manifold.MAX_CREDIT_BUDGET_USD + 0.01)
    ) == 2


def test_run_requires_explicit_oauth_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in run_manifold.METERED_AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        run_bot, "load_config", lambda: pytest.fail("auth check must precede work")
    )
    assert run_manifold.run(make_args(tmp_path, require_subscription_auth=True)) == 2


def test_run_cumulative_credit_cap_is_pair_atomic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    markets = [mk("a", groupSlugs=["a"]), mk("b", groupSlugs=["b"])]
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: markets)
    agent = CostAgent([1.5, 1.5, 2.0])
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())

    args = make_args(tmp_path, budget=5.0)
    assert run_manifold.run(args) == 0
    # First pair costs $3. The next blind call receives only the $2 remainder; after it uses
    # that allowance, no sighted call starts and no orphan half-pair is journaled.
    assert len(agent.calls) == 3
    caps = []
    for call in agent.calls:
        tokens = shlex.split(call["cmd"])
        i = tokens.index("--max-budget-usd")
        caps.append(float(tokens[i + 1]))
    assert caps == pytest.approx([5.0, 3.5, 2.0])
    rows = read_journal(args.journal)
    assert len(rows) == 2
    assert {r["source"]["question_id"] for r in rows} == {"a"}


def test_run_missing_cost_telemetry_reserves_cap_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("unknown-cost")
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    agent = CostAgent([0.0])
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())

    args = make_args(tmp_path, budget=5.0)
    assert run_manifold.run(args) == 1
    assert len(agent.calls) == 1
    assert not Path(args.journal).exists() or read_journal(args.journal) == []


def test_cloud_workflow_is_hourly_subscription_only_and_hard_capped() -> None:
    workflow = (ROOT / ".github" / "workflows" / "manifold.yml").read_text(
        encoding="utf-8"
    )
    lower = workflow.lower()
    assert 'cron: "17 * * * *"' in workflow
    assert "workflow_dispatch" not in workflow  # no extra manual budget window
    assert "--provider subscription" in workflow
    assert "--budget 5" in workflow
    assert "--deadline-minutes 45" in workflow
    assert "--require-subscription-auth" in workflow
    assert "set -o pipefail" in workflow
    assert "claude_code_oauth_token" in lower
    assert "manifold_api_key" in lower
    assert "leak_patterns" in lower
    assert "openrouter" not in lower
    assert "asknews" not in lower


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
    # The stub response carried a betId, so the POST is provably filled.
    assert with_bet[0]["source"]["bet"]["status"] == "placed"
    assert all(r["dry_run"] is False for r in rows)  # live provenance


def test_run_skips_market_with_existing_position(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("held", probability=0.30, groupSlugs=["z"])
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    monkeypatch.setattr(score_manifold, "fetch_market_state", lambda mid: dict(OPEN_STATE))
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    # Pre-seed the journal with an existing bet on this market. The forecast_at is > 3 days
    # old so the re-forecast dedupe does NOT skip it — the position guard is what blocks the
    # bet here, not the fresh-pair skip.
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat(timespec="seconds")
    journal = Journal(str(tmp_path / "manifold.jsonl"))
    journal.append(ForecastRecord(
        question="held market", question_type="binary", probability=0.5, forecast_at=old,
        source={"platform": "manifold", "question_id": "held", "pair_id": "old",
                "bet": {"outcome": "YES", "stake": 25, "dry_run": False}},
    ))

    seed_phase(tmp_path, phase=1)
    args = make_args(tmp_path, live=True)
    assert run_manifold.run(args) == 0
    assert spy.calls == []  # already positioned -> no new bet


def test_already_bet_ids_ignore_dry_run_and_count_unknown() -> None:
    # A dry-run would-be bet is not a position; a placed bet is; an "unknown"-status bet
    # (POST failed after send — may have filled) is guarded too.
    records = [
        mf_record("a", "sighted", 0.9, 0.4, market_id="paper",
                  bet={"outcome": "YES", "stake": 25, "dry_run": True}),
        mf_record("b", "sighted", 0.9, 0.4, market_id="real",
                  bet={"outcome": "YES", "stake": 25, "dry_run": False}),
        mf_record("c", "sighted", 0.9, 0.4, market_id="lost",
                  bet={"outcome": "YES", "stake": 25, "dry_run": False,
                       "status": "unknown"}),
    ]
    assert run_manifold.already_bet_market_ids(records) == {"real", "lost"}


def test_dry_run_bet_does_not_block_later_live_bet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A phase-0 paper bet journaled >3 days ago (so the fresh-pair dedupe is out of the way)
    # must NOT hold the position guard against a later live bet on the same market.
    market = mk("paper", probability=0.30, groupSlugs=["z"])
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    monkeypatch.setattr(score_manifold, "fetch_market_state", lambda mid: dict(OPEN_STATE))
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    old = (datetime.now(UTC) - timedelta(days=10)).isoformat(timespec="seconds")
    journal = Journal(str(tmp_path / "manifold.jsonl"))
    journal.append(ForecastRecord(
        question="paper market", question_type="binary", probability=0.5, forecast_at=old,
        source={"platform": "manifold", "question_id": "paper", "pair_id": "old",
                "bet": {"outcome": "YES", "stake": 25, "dry_run": True}},
    ))

    seed_phase(tmp_path, phase=1)
    args = make_args(tmp_path, live=True)
    assert run_manifold.run(args) == 0
    assert len(spy.calls) == 1 and spy.calls[0][1] == "paper"  # the paper bet did not block


def test_bet_post_failure_journals_unknown_and_guards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A POST that raises may still have filled (timeout after fill): the bet is journaled
    # with status "unknown" so the market stays guarded and the stake stays in exposure.
    market = mk("tmo", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    monkeypatch.setattr(score_manifold, "fetch_market_state", lambda mid: dict(OPEN_STATE))
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    def boom(api_key: str, market_id: str, outcome: str, amount: float) -> dict:
        raise RuntimeError("timeout after send")

    monkeypatch.setattr(run_manifold, "place_bet", boom)
    seed_phase(tmp_path, phase=1)
    args = make_args(tmp_path, live=True)
    assert run_manifold.run(args) == 0

    sighted = next(r for r in read_journal(args.journal) if r["source"]["mode"] == "sighted")
    bet = sighted["source"]["bet"]
    assert bet["status"] == "unknown" and bet["dry_run"] is False
    assert bet["outcome"] == "YES" and bet["stake"] == 25.0
    assert bet["p_market_at_bet"] == 0.30

    # The unknown-status stake counts toward exposure (conservative: it may have filled).
    assert run_manifold.open_exposure(list(Journal(args.journal))) == pytest.approx(25.0)

    # A second run must NOT re-bet the market even with the fresh-pair dedupe window forced
    # to 0 — the position guard alone holds it.
    monkeypatch.setattr(run_manifold, "recently_forecast_market_ids",
                        lambda records, **k: set())
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    assert run_manifold.run(args) == 0
    assert spy.calls == []


def test_bet_response_without_id_treated_as_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A 2xx body with no bet id is no proof of a fill: same conservative path as a raise.
    market = mk("noid", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 5000.0)
    monkeypatch.setattr(run_manifold, "place_bet", lambda *a: {})
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    seed_phase(tmp_path, phase=1)
    args = make_args(tmp_path, live=True)
    assert run_manifold.run(args) == 0
    sighted = next(r for r in read_journal(args.journal) if r["source"]["mode"] == "sighted")
    assert sighted["source"]["bet"]["status"] == "unknown"


def test_run_skips_market_with_fresh_pair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A market whose journaled pair is < 3 days old is skipped before any forecast runs
    # (re-forecasting the same market every run wastes budget).
    market = mk("dup", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    agent = ScriptedAgent(0.90)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())

    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    journal = Journal(str(tmp_path / "manifold.jsonl"))
    for mode in ("blind", "sighted"):
        journal.append(ForecastRecord(
            question="dup market", question_type="binary", probability=0.5,
            blind=(mode == "blind"), forecast_at=now_iso,
            source={"platform": "manifold", "question_id": "dup", "pair_id": "fresh",
                    "mode": mode},
            crowd={"value": 0.30, "source": "manifold market",
                   "shown_to_agent": mode == "sighted"},
        ))

    args = make_args(tmp_path)
    assert run_manifold.run(args) == 0
    assert "skip (fresh pair exists)" in capsys.readouterr().out
    assert agent.calls == []               # the fresh market is never forecast again
    assert len(read_journal(args.journal)) == 2  # only the seeded pair; nothing appended


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


def test_run_informed_read_still_bets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # [AMENDED 2026-07-11] market_read is no longer a bet gate: an "informed" read is
    # journaled as a preregistered hypothesis but the divergent bet still goes through.
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

    assert len(spy.calls) == 1  # informed no longer gates: the divergent bet is placed
    assert spy.calls[0][2] == "YES"
    rows = read_journal(args.journal)
    sighted = next(r for r in rows if r["source"]["mode"] == "sighted")
    assert sighted["source"]["market_read"] == "informed"  # still REQUIRED + journaled
    assert sighted["source"]["bet"]["outcome"] == "YES"    # and the bet IS recorded
    assert "market_read=informed" in capsys.readouterr().out  # the informational marker


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


# --------------------------------------------------------------------------- source floor


def test_run_source_floor_announced_and_journaled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The tier's min_sources floor (medium -> 3) is announced in every brief and the
    # consulted sources land in the journal on BOTH modes.
    market = mk("src", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    srcs = ["https://a.example", "https://b.example", "https://c.example"]
    agent = ScriptedAgent(0.90, sources=srcs)
    monkeypatch.setattr(run_bot, "run_agent", agent)
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())

    args = make_args(tmp_path)  # medium tier -> min_sources = 3
    assert run_manifold.run(args) == 0
    # Announced in the brief the blind AND sighted agents saw.
    assert agent.calls and all("Research floor" in c["prompt"] for c in agent.calls)
    assert all("at least 3 DISTINCT" in c["prompt"] for c in agent.calls)
    # Journaled on both records.
    rows = read_journal(args.journal)
    assert len(rows) == 2
    for r in rows:
        assert r["research"]["sources"] == srcs
        assert r["research"]["n_searches"] == 3


def test_run_source_floor_rejects_too_few_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An agent returning fewer than the floor (here 0 < 3) is rejected by the repair loop;
    # the stub repeats the too-thin payload, so the pair is dropped entirely.
    market = mk("thin", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90, sources=[]))
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())

    args = make_args(tmp_path)  # medium -> min_sources = 3; [] fails the floor
    assert run_manifold.run(args) == 0
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
    n: int, n_toward: int, *, now: datetime, dry_run: bool = False
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
            bet={"outcome": "YES", "stake": 25, "dry_run": dry_run,
                 "p_market_at_bet": 0.40},
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


def test_eval_phase1_excludes_dry_run_bets() -> None:
    # The movement test counts LIVE divergent bets only: an aged dry-run would-be bet is
    # excluded from n_movement while an otherwise-identical placed bet counts.
    now = datetime(2026, 7, 11, tzinfo=UTC)
    old = (now - timedelta(days=10)).isoformat(timespec="seconds")
    states = {mid: {"probability": 0.60, "resolved": False, "outcome": None}
              for mid in ("live", "paper")}
    records = [
        mf_record("a", "sighted", 0.90, 0.40, market_id="live", forecast_at=old,
                  bet={"outcome": "YES", "stake": 25, "dry_run": False,
                       "p_market_at_bet": 0.40}),
        mf_record("b", "sighted", 0.90, 0.40, market_id="paper", forecast_at=old,
                  bet={"outcome": "YES", "stake": 25, "dry_run": True,
                       "p_market_at_bet": 0.40}),
    ]
    ev = run_manifold.eval_phase1(records, lambda mid: states[mid], now=now)
    assert ev["n_movement"] == 1 and ev["moved_toward"] == 1


def test_phase_kill_ignores_dry_run_bets() -> None:
    # 50 aged divergent DRY-RUN bets at toward-rate 0.4 must NOT trip the permanent kill:
    # would-be bets carry no live-money movement evidence.
    now = datetime(2026, 7, 11, tzinfo=UTC)
    records, states = _movement_journal(50, 20, now=now, dry_run=True)
    state = {"phase": 1, "killed": False, "history": []}
    new, transitions = run_manifold.evaluate_promotions(
        state, records, lambda mid: states[mid], now=now
    )
    assert new["killed"] is False and transitions == []


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


def test_open_exposure_live_state_lookup() -> None:
    # Journal status is never written back, so the live lookup is what closes a position:
    # a resolved market's stake falls out; a lookup failure fails CLOSED (still counted);
    # no lookup (offline/dry paths) keeps journal-only behavior.
    records = [
        mf_record("a", "sighted", 0.9, 0.4, market_id="open1",
                  bet={"outcome": "YES", "stake": 25, "dry_run": False}),
        mf_record("b", "sighted", 0.9, 0.4, market_id="done1",
                  bet={"outcome": "NO", "stake": 40, "dry_run": False}),
    ]
    states = {
        "open1": {"probability": 0.5, "resolved": False, "outcome": None},
        "done1": {"probability": 1.0, "resolved": True, "outcome": True},
    }
    live = run_manifold.open_exposure(records, state_lookup=lambda mid: states[mid])
    assert live == pytest.approx(25.0)

    def raising(mid: str) -> dict[str, Any]:
        raise RuntimeError("api down")

    assert run_manifold.open_exposure(records, state_lookup=raising) == pytest.approx(65.0)
    assert run_manifold.open_exposure(records) == pytest.approx(65.0)


def test_exposure_cap_blocks_a_bet_over_30pct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    market = mk("e", probability=0.30, groupSlugs=["x"])
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 1200.0)  # 30% = 360
    monkeypatch.setattr(score_manifold, "fetch_market_state", lambda mid: dict(OPEN_STATE))
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


# ------------------------------------------------------------------ degradation markers


def test_betting_disabled_marker_on_balance_read_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A --live phase-1 run silently degraded to forecast-only must print the grep-able
    # marker (the workflow alert step watches stdout for BETTING-DISABLED).
    market = mk("bal", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))

    def bad_balance(key: str) -> float:
        raise RuntimeError("503 from /v0/me")

    monkeypatch.setattr(run_manifold, "get_balance", bad_balance)
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    seed_phase(tmp_path, phase=1)
    assert run_manifold.run(make_args(tmp_path, live=True)) == 0
    assert "BETTING-DISABLED: balance-read" in capsys.readouterr().out
    assert spy.calls == []


def test_betting_disabled_marker_on_missing_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    market = mk("nk", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())
    # Stub the whole lookup: the env var AND the operator keyfile fallback must both miss.
    monkeypatch.setattr(run_manifold, "manifold_api_key", lambda: "")

    seed_phase(tmp_path, phase=1)
    assert run_manifold.run(make_args(tmp_path, live=True)) == 0
    assert "BETTING-DISABLED: no-key" in capsys.readouterr().out


def test_betting_disabled_marker_below_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    market = mk("bf", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "get_balance", lambda key: 500.0)  # < 1,100 floor
    spy = BetSpy()
    monkeypatch.setattr(run_manifold, "place_bet", spy)
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    seed_phase(tmp_path, phase=1)
    assert run_manifold.run(make_args(tmp_path, live=True)) == 0
    assert "BETTING-DISABLED: below-floor" in capsys.readouterr().out
    assert spy.calls == []


def test_no_marker_on_phase0_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Phase 0 forcing dry-run is a LEGITIMATE zero-bet state, not a degradation: no marker.
    market = mk("p0m", probability=0.30)
    monkeypatch.setattr(run_manifold, "gather_markets", lambda limit, **k: [market])
    monkeypatch.setattr(run_bot, "run_agent", ScriptedAgent(0.90))
    monkeypatch.setattr(run_manifold, "place_bet", BetSpy())
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-key")

    assert run_manifold.run(make_args(tmp_path, live=True)) == 0  # fresh -> phase 0
    assert "BETTING-DISABLED" not in capsys.readouterr().out


def test_phase_load_save_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "phase.json"
    assert run_manifold.load_phase(path) == {"phase": 0, "killed": False, "history": []}
    state = {"phase": 2, "killed": False, "history": [{"to": 1, "at": "t", "evidence": {}}]}
    run_manifold.save_phase(path, state)
    assert run_manifold.load_phase(path) == state
