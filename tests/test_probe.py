"""The contamination probe (v0.4.7): no-tools outcome recall, scored differentially.

The probe's validity rests on three mechanical guarantees under test here: the agent
command carries NO tool path (recall must not become lookup), the prompt frames the
question as a past event WITHOUT any as-of restriction (the model should use post-event
knowledge), and scoring/flagging is a pure function of the recorded rows.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bot"))

import contamination_probe as probe  # noqa: E402

SPEC = {
    "id": "btf2:q1",
    "question": "Will X happen by December 2025?",
    "criteria": "Resolves YES if X is reported by source S before 2025-12-31.",
    "resolve_by": "2025-12-31",
    "resolution": 1,
}


def fenced(payload: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


PROBE_PAYLOAD = fenced({"recall": "unknown", "confidence": 0.0, "basis": "no memory"})


class ScriptedAgent:
    """Replaces contamination_probe.run_agent; returns a fixed reply and records every
    call (mirrors the ScriptedAgent pattern in tests/test_spine.py)."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: str, prompt: str, system: str | None, timeout: int,
                 provider: str = "subscription") -> tuple[str, float, str]:
        self.calls.append({"cmd": cmd, "prompt": prompt, "system": system,
                           "provider": provider})
        return self.outputs.pop(0), 0.01, "irrelevant"


class ScriptedDirect:
    """Replaces contamination_probe.run_direct (the openrouter-direct transport). Its
    signature is (prompt, system, model, timeout) — no cmd, no provider — and it echoes
    the model back as the id, matching bench/direct_agent.run_direct."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, prompt: str, system: str | None, model: str,
                 timeout: int) -> tuple[str, float, str]:
        self.calls.append({"prompt": prompt, "system": system, "model": model})
        return self.outputs.pop(0), 0.02, model


def base_args(**overrides: Any) -> argparse.Namespace:
    defaults = dict(provider="subscription", timeout=60)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestPromptAndCommand:
    def test_prompt_frames_past_event_without_as_of_lock(self) -> None:
        prompt = probe.build_probe_prompt(SPEC, "2026-07-10")
        assert "MEMORY" in prompt and "2026-07-10" in prompt
        assert "AS-OF" not in prompt  # recall wants post-event knowledge, not a freeze
        assert SPEC["criteria"] in prompt

    def test_disallowed_belt_covers_every_research_and_fs_path(self) -> None:
        for tool in ("WebSearch", "WebFetch", "Read", "Glob", "Grep", "Bash"):
            assert tool in probe.PROBE_DISALLOWED

    def test_system_demands_unknown_over_guessing(self) -> None:
        assert "unknown" in probe.PROBE_SYSTEM
        assert "NOT recall" in probe.PROBE_SYSTEM


class TestPayloadParsing:
    def test_valid_payload(self) -> None:
        got = probe.parse_probe_payload(
            {"recall": "Yes", "confidence": "0.9", "basis": "remember the reporting"})
        assert got == ("yes", 0.9, "remember the reporting")

    def test_confidence_clamped(self) -> None:
        got = probe.parse_probe_payload({"recall": "no", "confidence": 7})
        assert got is not None and got[1] == 1.0

    def test_bad_recall_value_rejected(self) -> None:
        assert probe.parse_probe_payload({"recall": "maybe", "confidence": 0.5}) is None

    def test_non_numeric_confidence_rejected(self) -> None:
        assert probe.parse_probe_payload({"recall": "yes", "confidence": "high"}) is None


class TestScoring:
    def rows(self) -> list[dict]:
        return [
            # confident correct recall -> contaminated
            {"qid": "q1", "model": "m-new", "recall": "yes", "confidence": 0.9,
             "resolution": 1, "correct": True},
            # honest unknown -> not contaminated, not answered
            {"qid": "q2", "model": "m-new", "recall": "unknown", "confidence": 0.0,
             "resolution": 0, "correct": None},
            # low-confidence correct -> answered but below the flag threshold
            {"qid": "q3", "model": "m-old", "recall": "no", "confidence": 0.3,
             "resolution": 0, "correct": True},
            # confident but WRONG -> answered, never flagged
            {"qid": "q1", "model": "m-old", "recall": "no", "confidence": 0.9,
             "resolution": 1, "correct": False},
        ]

    def test_contaminated_requires_correct_and_confident(self) -> None:
        rows = self.rows()
        assert probe.contaminated(rows[0])
        assert not probe.contaminated(rows[1])  # unknown
        assert not probe.contaminated(rows[2])  # under threshold
        assert not probe.contaminated(rows[3])  # wrong

    def test_report_flags_only_the_contaminated_model(self) -> None:
        text = probe.report(self.rows(), base_rate_yes=0.3)
        assert "contaminated for m-new (1): q1" in text
        assert "contaminated for m-old (0): none detected" in text
        assert "70%" in text  # majority baseline printed for interpretation
        assert "latent knowledge" in text  # the under-detection caveat is always present

    def test_report_accuracy_is_on_answered_only(self) -> None:
        text = probe.report(self.rows(), base_rate_yes=0.3)
        # m-new answered 1 of 2 (one unknown), and that answer was correct
        assert "m-new" in text
        line = next(ln for ln in text.splitlines() if ln.startswith("m-new"))
        assert "100%" in line


class TestProviderRouting:
    """--provider openrouter must reuse run_bench's own OpenRouter plumbing (the
    openrouter_model_cmd rewrite), not a reimplementation of it."""

    def test_default_provider_leaves_command_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([PROBE_PAYLOAD])
        monkeypatch.setattr(probe, "run_agent", agent)
        row = probe.probe_one(SPEC, "claude-opus-4-6", base_args())
        assert row is not None
        assert "--model claude-opus-4-6" in agent.calls[0]["cmd"]
        assert "anthropic/" not in agent.calls[0]["cmd"]
        assert agent.calls[0]["provider"] == "subscription"

    def test_openrouter_provider_routes_through_openrouter_model_cmd(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([PROBE_PAYLOAD])
        monkeypatch.setattr(probe, "run_agent", agent)
        # A bare (non-slug) id is exactly what openrouter_model_cmd rewrites to
        # "anthropic/<id>" — seeing that prefix in the constructed command proves the
        # probe reused the helper rather than passing the id through untouched.
        row = probe.probe_one(SPEC, "claude-opus-4-6",
                              base_args(provider="openrouter"))
        assert row is not None
        assert "--model anthropic/claude-opus-4-6" in agent.calls[0]["cmd"]
        assert agent.calls[0]["provider"] == "openrouter"

    def test_openrouter_slug_with_existing_author_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = ScriptedAgent([PROBE_PAYLOAD])
        monkeypatch.setattr(probe, "run_agent", agent)
        row = probe.probe_one(SPEC, "google/gemini-2.5-pro",
                              base_args(provider="openrouter"))
        assert row is not None
        assert "--model google/gemini-2.5-pro" in agent.calls[0]["cmd"]

    def test_row_records_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = ScriptedAgent([PROBE_PAYLOAD, PROBE_PAYLOAD])
        monkeypatch.setattr(probe, "run_agent", agent)
        default_row = probe.probe_one(SPEC, "claude-opus-4-6", base_args())
        assert default_row is not None and default_row["provider"] == "subscription"
        or_row = probe.probe_one(SPEC, "google/gemini-2.5-pro",
                                 base_args(provider="openrouter"))
        assert or_row is not None and or_row["provider"] == "openrouter"

    def test_default_behavior_unchanged_without_provider_arg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An args namespace with no `provider` attribute at all (the pre-existing
        shape) must still behave exactly like subscription."""
        agent = ScriptedAgent([PROBE_PAYLOAD])
        monkeypatch.setattr(probe, "run_agent", agent)
        args = argparse.Namespace(timeout=60)
        row = probe.probe_one(SPEC, "claude-opus-4-6", args)
        assert row is not None
        assert row["provider"] == "subscription"
        assert agent.calls[0]["provider"] == "subscription"
        assert "--model claude-opus-4-6" in agent.calls[0]["cmd"]

    def test_resumability_key_unaffected_by_provider(self) -> None:
        """Resume logic keys on (qid, model); model strings differ across providers
        (google/gemini-2.5-pro cannot collide with a subscription id), so adding a
        provider field must not change what counts as 'already done'."""
        done_rows = [
            {"qid": "btf2:q1", "model": "google/gemini-2.5-pro", "provider": "openrouter"},
            {"qid": "btf2:q1", "model": "claude-opus-4-6", "provider": "subscription"},
        ]
        done = {(row["qid"], row["model"]) for row in done_rows}
        assert ("btf2:q1", "google/gemini-2.5-pro") in done
        assert ("btf2:q1", "claude-opus-4-6") in done
        assert len(done) == 2


class TestDirectProvider:
    """--provider openrouter-direct routes through the tool-less native chat transport
    (bench/direct_agent.run_direct), never the CLI. Rows record the provider, and the
    (qid, model) resume key is unchanged."""

    def test_openrouter_direct_is_a_provider_choice(self) -> None:
        assert "openrouter-direct" in probe.PROBE_PROVIDERS

    def test_routes_through_run_direct_not_run_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        direct = ScriptedDirect([PROBE_PAYLOAD])
        monkeypatch.setattr(probe, "run_direct", direct)

        def _boom(*_a: Any, **_k: Any) -> tuple[str, float, str]:
            raise AssertionError("run_agent must not be called for openrouter-direct")

        monkeypatch.setattr(probe, "run_agent", _boom)
        row = probe.probe_one(SPEC, "google/gemini-2.5-pro",
                              base_args(provider="openrouter-direct"))
        assert row is not None
        assert row["provider"] == "openrouter-direct"
        assert row["model"] == "google/gemini-2.5-pro"
        assert len(direct.calls) == 1
        # PROBE_SYSTEM is passed straight through as the system message; the slug
        # (already carrying "/") reaches the API unchanged.
        assert direct.calls[0]["system"] == probe.PROBE_SYSTEM
        assert direct.calls[0]["model"] == "google/gemini-2.5-pro"

    def test_bare_id_slug_prefixed_for_api_but_row_keeps_requested_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        direct = ScriptedDirect([PROBE_PAYLOAD])
        monkeypatch.setattr(probe, "run_direct", direct)
        row = probe.probe_one(SPEC, "claude-opus-4-6",
                              base_args(provider="openrouter-direct"))
        assert row is not None
        # the native API needs the slug form...
        assert direct.calls[0]["model"] == "anthropic/claude-opus-4-6"
        # ...but the row (and thus the (qid, model) resume key) keeps what was requested
        assert row["model"] == "claude-opus-4-6"

    def test_repair_retry_works_through_direct_transport(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad = fenced({"recall": "maybe", "confidence": 0.5})  # invalid recall value
        good = fenced({"recall": "yes", "confidence": 0.9, "basis": "remembered it"})
        direct = ScriptedDirect([bad, good])
        monkeypatch.setattr(probe, "run_direct", direct)
        row = probe.probe_one(SPEC, "google/gemini-2.5-pro",
                              base_args(provider="openrouter-direct"))
        assert row is not None
        assert len(direct.calls) == 2  # first invalid, repaired on retry
        assert row["recall"] == "yes"
        # cost accumulates across both direct calls (0.02 each)
        assert row["cost_usd"] == pytest.approx(0.04)

    def test_resume_key_unaffected_by_direct_provider(self) -> None:
        done_rows = [
            {"qid": "btf2:q1", "model": "google/gemini-2.5-pro",
             "provider": "openrouter-direct"},
            {"qid": "btf2:q1", "model": "claude-opus-4-6", "provider": "subscription"},
        ]
        done = {(row["qid"], row["model"]) for row in done_rows}
        assert ("btf2:q1", "google/gemini-2.5-pro") in done
        assert len(done) == 2
