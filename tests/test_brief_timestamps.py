"""The q44378 post-mortem contract (v0.4.4): the brief must give the agent a clock and
the resolution timestamp, and must NOT give it the forecasting-close time — the lock time
is harness bookkeeping, and its presence as the brief's only timestamp is what shrank a
one-month event window to six days in the first scored live miss."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

import run_bot  # noqa: E402

QUESTION = {
    "title": "Will X happen in July 2026?",
    "type": "binary",
    "scheduled_close_time": "2026-07-06T21:00:00Z",
    "scheduled_resolve_time": "2026-08-05T00:00:00Z",
    "resolution_criteria": "Resolves Yes if X is listed with a date in July 2026.",
}


class TestBriefTimestamps:
    def test_brief_states_now_in_utc(self) -> None:
        brief = run_bot.build_brief({}, QUESTION, None)
        match = re.search(r"Now \(UTC\): (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", brief)
        assert match, brief

    def test_brief_states_scheduled_resolution(self) -> None:
        brief = run_bot.build_brief({}, QUESTION, None)
        assert "Scheduled resolution: 2026-08-05T00:00:00Z" in brief

    def test_brief_withholds_forecast_lock_time(self) -> None:
        brief = run_bot.build_brief({}, QUESTION, None)
        assert "2026-07-06T21:00:00Z" not in brief
        assert "Closes:" not in brief

    def test_resolve_time_falls_back_to_post(self) -> None:
        question = {k: v for k, v in QUESTION.items() if k != "scheduled_resolve_time"}
        post = {"scheduled_resolve_time": "2026-09-01T00:00:00Z"}
        brief = run_bot.build_brief(post, question, None)
        assert "Scheduled resolution: 2026-09-01T00:00:00Z" in brief


class TestWindowDiscipline:
    def test_dossier_contract_requires_event_window_line(self) -> None:
        assert "event window:" in run_bot.DOSSIER_SECTION
        assert "NEVER from the forecasting-close time" in run_bot.DOSSIER_SECTION

    def test_verify_prompt_requires_window_premise(self) -> None:
        assert "event" in run_bot.VERIFY_PROMPT and "window" in run_bot.VERIFY_PROMPT
        assert "CONTRADICTED" in run_bot.VERIFY_PROMPT

    def test_verify_dossier_appends_contract(self) -> None:
        captured: dict[str, str] = {}

        def fake_run_agent(cmd, prompt, system, timeout, provider):
            captured["prompt"] = prompt
            return '```json\n{"verification": []}\n```', 0.0, "m"

        original = run_bot.run_agent
        run_bot.run_agent = fake_run_agent
        try:
            run_bot.verify_dossier(
                "cmd", "the dossier", 60, "subscription", False,
                contract="Resolution criteria (verbatim): July 2026 only.",
            )
        finally:
            run_bot.run_agent = original
        assert "## The contract (for the event-window check only)" in captured["prompt"]
        assert "July 2026 only." in captured["prompt"]

    def test_reasoning_reference_carries_temporal_coherence_gate(self) -> None:
        text = (ROOT / "skills" / "forecast" / "references" / "reasoning.md").read_text()
        assert "Event window:" in text
        assert "Temporal coherence:" in text

    def test_question_hygiene_names_close_time_trap(self) -> None:
        text = (ROOT / "skills" / "forecast" / "references"
                / "question-hygiene.md").read_text()
        assert "close time is not the event window" in text
