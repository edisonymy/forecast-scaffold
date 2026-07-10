"""The contamination probe (v0.4.7): no-tools outcome recall, scored differentially.

The probe's validity rests on three mechanical guarantees under test here: the agent
command carries NO tool path (recall must not become lookup), the prompt frames the
question as a past event WITHOUT any as-of restriction (the model should use post-event
knowledge), and scoring/flagging is a pure function of the recorded rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
