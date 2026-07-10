"""The reasoning-spine A/B harness (--spine-file): the zero tier is the no-method
ablation cell, so it doubles as the rig for comparing alternate METHOD texts against
each other — same dossier, same tools, only the words after ZERO_SYSTEM vary. Covers
the system-prompt splice (zero only), the row-level provenance stamp (arm/spine_sha),
and the CLI wiring/warning in main().
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bot"))

import run_bench  # noqa: E402

SPEC = {
    "id": "btf2:q1",
    "source": "btf2",
    "question": "Will X happen?",
    "criteria": "Resolves YES if X happens by the date.",
    "resolve_by": "2026-12-31",
    "background": "Some context about X.",
    "crowd": {"value": 0.5},
}


def fenced(payload: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


PAYLOAD = fenced({"probability": 0.42, "reasoning": "x", "sources": []})


class ScriptedAgent:
    """Replaces run_bench.run_agent; returns a fixed reply and records every call
    (mirrors the ScriptedAgent pattern in tests/test_multirun.py)."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        self.calls.append({"cmd": cmd, "prompt": prompt, "system": system})
        return self.outputs.pop(0), 0.01, "claude-sonnet-5"


def base_args(**overrides: Any) -> argparse.Namespace:
    defaults = dict(
        provider="subscription", agent_cmd="claude -p", timeout=60,
        tier_config=None, spine_text=None, spine_arm=None, spine_sha=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestSystemPromptSplice:
    def test_zero_tier_includes_spine_text_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        args = base_args(spine_text="ALTERNATE REASONING METHOD.", spine_arm="alt_v1",
                         spine_sha="abc123abc123")
        row = run_bench.forecast_one(SPEC, "zero", args)
        assert row is not None
        system = agent.calls[0]["system"]
        assert system.startswith(run_bench.ZERO_SYSTEM)
        assert "ALTERNATE REASONING METHOD." in system

    def test_zero_tier_is_unchanged_when_spine_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "zero", base_args())
        assert row is not None
        assert agent.calls[0]["system"] == run_bench.ZERO_SYSTEM

    def test_non_zero_tier_never_gets_the_spine_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        args = base_args(spine_text="ALTERNATE REASONING METHOD.", spine_arm="alt_v1",
                         spine_sha="abc123abc123")
        row = run_bench.forecast_one(SPEC, "low", args)
        assert row is not None
        system = agent.calls[0]["system"]
        assert "ALTERNATE REASONING METHOD." not in system
        assert run_bench.ZERO_SYSTEM not in system  # took the build_system path, not zero's


class TestRowProvenance:
    def test_zero_tier_row_carries_arm_and_spine_sha_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        args = base_args(spine_text="ALTERNATE REASONING METHOD.", spine_arm="alt_v1",
                         spine_sha="abc123abc123")
        row = run_bench.forecast_one(SPEC, "zero", args)
        assert row is not None
        assert row["arm"] == "alt_v1"
        assert row["spine_sha"] == "abc123abc123"

    def test_zero_tier_row_lacks_arm_and_spine_sha_when_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        row = run_bench.forecast_one(SPEC, "zero", base_args())
        assert row is not None
        assert "arm" not in row
        assert "spine_sha" not in row

    def test_non_zero_tier_row_never_carries_arm_even_if_spine_is_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # --spine-file only applies to the zero tier; a row from another tier must stay
        # the pre-existing shape even in the same invocation that set --spine-file.
        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        args = base_args(spine_text="ALTERNATE REASONING METHOD.", spine_arm="alt_v1",
                         spine_sha="abc123abc123")
        row = run_bench.forecast_one(SPEC, "low", args)
        assert row is not None
        assert "arm" not in row
        assert "spine_sha" not in row


class TestCliWiring:
    def test_spine_file_derives_arm_and_sha_and_reaches_zero_tier_rows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        spine_path = tmp_path / "concede_uncertainty.txt"
        spine_text = "Explicitly weigh the base rate before adjusting.\n"
        spine_path.write_text(spine_text, encoding="utf-8")
        expected_sha = hashlib.sha256(spine_text.encode("utf-8")).hexdigest()[:12]

        set_path = tmp_path / "set.jsonl"
        set_path.write_text(json.dumps(SPEC) + "\n", encoding="utf-8")

        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        monkeypatch.setattr(run_bench, "RESULTS_DIR", tmp_path / "results")

        code = run_bench.main([
            str(set_path), "--tiers", "zero", "--spine-file", str(spine_path),
            "--tag", "alt",
        ])
        assert code == 0

        results_path = tmp_path / "results" / "set.alt.results.jsonl"
        rows = [json.loads(line) for line in
                results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 1
        assert rows[0]["arm"] == "concede_uncertainty"
        assert rows[0]["spine_sha"] == expected_sha
        assert spine_text.strip() in agent.calls[0]["system"]

    def test_spine_file_with_non_zero_tiers_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        spine_path = tmp_path / "spine.txt"
        spine_path.write_text("method text", encoding="utf-8")
        set_path = tmp_path / "set.jsonl"
        set_path.write_text(json.dumps(SPEC) + "\n", encoding="utf-8")

        agent = ScriptedAgent([PAYLOAD, PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        monkeypatch.setattr(run_bench, "RESULTS_DIR", tmp_path / "results")

        code = run_bench.main([
            str(set_path), "--tiers", "zero,low", "--spine-file", str(spine_path),
            "--tag", "alt",
        ])
        assert code == 0
        out = capsys.readouterr().out
        assert "--spine-file only applies to the zero tier" in out
        assert "low" in out

    def test_no_spine_file_leaves_rows_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        set_path = tmp_path / "set.jsonl"
        set_path.write_text(json.dumps(SPEC) + "\n", encoding="utf-8")

        agent = ScriptedAgent([PAYLOAD])
        monkeypatch.setattr(run_bench, "run_agent", agent)
        monkeypatch.setattr(run_bench, "RESULTS_DIR", tmp_path / "results")

        code = run_bench.main([str(set_path), "--tiers", "zero"])
        assert code == 0
        results_path = tmp_path / "results" / "set.results.jsonl"
        row = json.loads(results_path.read_text(encoding="utf-8").splitlines()[0])
        assert "arm" not in row
        assert "spine_sha" not in row
