"""Provider routing (subscription vs OpenRouter) and benchmark scoring math."""

from __future__ import annotations

import json
import math
import shlex
import sys
from pathlib import Path

import pytest

from forecast_scaffold.core import ForecastRecord

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "bench"))

import fetch_set  # noqa: E402
import report  # noqa: E402
import run_bot  # noqa: E402


class TestAgentEnvironment:
    def test_subscription_strips_secrets_and_endpoint_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in run_bot._SECRETS_TO_HIDE:
            monkeypatch.setenv(name, "sekrit")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://some-gateway.example")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "gateway-tok")
        env = run_bot.agent_environment("subscription")
        for name in run_bot._SECRETS_TO_HIDE:
            assert name not in env
        # the CLI's own credential stays: claude authenticates itself with it
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"
        # inherited endpoint overrides must not redirect the subscription path
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_subscription_drops_empty_api_key_keeps_real_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        assert "ANTHROPIC_API_KEY" not in run_bot.agent_environment("subscription")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "real-key")  # documented API-billing opt-in
        assert run_bot.agent_environment("subscription")["ANTHROPIC_API_KEY"] == "real-key"

    def test_openrouter_maps_key_and_neutralizes_conflicts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "real-api-key")
        env = run_bot.agent_environment("openrouter")
        assert env["ANTHROPIC_BASE_URL"] == run_bot.OPENROUTER_BASE_URL
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-test"
        # an inherited real key would out-rank the auth token and bill the API account
        assert env["ANTHROPIC_API_KEY"] == ""
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "OPENROUTER_API_KEY" not in env  # re-enters only as ANTHROPIC_AUTH_TOKEN

    def test_openrouter_without_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            run_bot.agent_environment("openrouter")


class TestOpenrouterModelCmd:
    def test_bare_model_id_gets_anthropic_prefix(self) -> None:
        cmd = run_bot.openrouter_model_cmd("claude -p --model claude-sonnet-5 --output-format json")
        assert "--model anthropic/claude-sonnet-5" in cmd

    def test_existing_slug_untouched(self) -> None:
        # compare parsed tokens: shlex.join may quote (e.g. a ~) without changing meaning
        for slug in ("anthropic/claude-sonnet-5", "~anthropic/claude-sonnet-latest"):
            cmd = run_bot.openrouter_model_cmd(f"claude -p --model {slug}")
            tokens = shlex.split(cmd)
            assert tokens[tokens.index("--model") + 1] == slug

    def test_no_model_flag_is_a_noop(self) -> None:
        assert run_bot.openrouter_model_cmd("claude -p") == "claude -p"


class TestPrimaryModel:
    """The model tag must be a single clean id so `score --by model` groups cleanly —
    not the CLI's helper models joined together (the ablation's polluted tag bug)."""

    CMD = "claude -p --model claude-fable-5 --output-format json"

    def test_requested_model_wins_over_helper(self) -> None:
        usage = {"claude-fable-5": {"outputTokens": 900},
                 "claude-haiku-4-5-20251001": {"outputTokens": 40}}
        assert run_bot._primary_model(usage, self.CMD) == "claude-fable-5"

    def test_dated_key_matches_by_prefix(self) -> None:
        usage = {"claude-fable-5-20260115": {"outputTokens": 900},
                 "claude-haiku-4-5-20251001": {"outputTokens": 40}}
        assert run_bot._primary_model(usage, self.CMD) == "claude-fable-5"

    def test_falls_back_to_max_tokens_when_flag_absent(self) -> None:
        usage = {"claude-sonnet-5": {"inputTokens": 5000, "outputTokens": 800},
                 "claude-haiku-4-5-20251001": {"inputTokens": 100, "outputTokens": 20}}
        assert run_bot._primary_model(usage, "claude -p") == "claude-sonnet-5"

    def test_no_usage_falls_back_to_flag(self) -> None:
        assert run_bot._primary_model(None, self.CMD) == "claude-fable-5"
        assert run_bot._primary_model({}, self.CMD) == "claude-fable-5"


def test_record_carries_provider() -> None:
    record = ForecastRecord(question="Will X?", probability=0.4, provider="openrouter")
    assert record.provider == "openrouter"
    assert ForecastRecord(question="Will X?").provider is None  # additive, defaults absent


class TestBenchScoring:
    def test_kl_zero_when_equal(self) -> None:
        assert report.kl_bernoulli(0.37, 0.37) == pytest.approx(0.0, abs=1e-12)

    def test_kl_known_value(self) -> None:
        expected = 0.75 * math.log(0.75 / 0.5) + 0.25 * math.log(0.25 / 0.5)
        assert report.kl_bernoulli(0.75, 0.5) == pytest.approx(expected)

    def test_logit_clamps_extremes(self) -> None:
        assert report.logit(0.0) == pytest.approx(math.log(0.001 / 0.999))
        assert report.logit(1.0) == pytest.approx(math.log(0.999 / 0.001))

    def test_gap_stats_hand_computed(self) -> None:
        stats = report.gap_stats([(0.5, 0.6), (0.5, 0.3)])
        assert stats["n"] == 2
        assert stats["mean_abs_dp"] == pytest.approx(0.15)
        assert stats["rms_dp"] == pytest.approx(math.sqrt((0.01 + 0.04) / 2))


class TestContractFidelity:
    def test_infer_excluded_by_default(self) -> None:
        # RAND/INFER contracts sit behind a login: unverifiable -> not scored by default.
        assert "infer" not in fetch_set.DEFAULT_SOURCES
        assert set(fetch_set.DEFAULT_SOURCES) == {"metaculus", "manifold", "polymarket"}

    def test_unknown_source_contract_is_none(self) -> None:
        assert fetch_set.source_contract("infer", "123") is None

    def test_metaculus_contract_requires_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Without the token the criteria + fine print can't be verified at the source,
        # so the question must be excluded rather than scored against a paraphrase.
        monkeypatch.delenv("METACULUS_TOKEN", raising=False)
        assert fetch_set.source_contract("metaculus", "11045") is None


class TestReportAutoImputation:
    def test_gap_stats_bias_sign(self) -> None:
        stats = report.gap_stats([(0.5, 0.7), (0.5, 0.7)])
        assert stats["bias"] == pytest.approx(0.2)  # student above teacher -> positive

    def test_router_only_rows_impute_from_routed_tier(self, tmp_path, monkeypatch) -> None:
        set_file = tmp_path / "s.jsonl"
        set_file.write_text("{}", encoding="utf-8")
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        monkeypatch.setattr(report, "RESULTS_DIR", results_dir)
        crowd = {"value": 0.5, "source": "manifold live", "at": "t"}
        rows = [
            {"qid": "q1", "source": "manifold", "question": "?", "tier": "medium",
             "effort": "medium", "probability": 0.30, "crowd": crowd, "cost_usd": 0.40,
             "model": "m", "provider": "subscription", "scaffold_version": "0.1.0"},
            {"qid": "q1", "source": "manifold", "question": "?", "tier": "auto",
             "effort": "medium (auto)", "router_only": True, "probability": None,
             "crowd": crowd, "cost_usd": 0.05, "model": "", "provider": "subscription",
             "scaffold_version": "0.1.0"},
        ]
        (results_dir / "s.results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        assert report.main([str(set_file)]) == 0
        text = (results_dir / "s.report.md").read_text(encoding="utf-8")
        # auto row appears in the crowd table with medium's p (gap 0.2) and summed cost
        auto_line = next(line for line in text.splitlines() if line.startswith("| auto"))
        assert "0.200" in auto_line and "$0.45" in auto_line
