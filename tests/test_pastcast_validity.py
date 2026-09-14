"""Capability/provenance diagnostics must not read or score probabilities."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench" / "analysis"))

import pastcast_validity as validity  # noqa: E402


def test_result_summary_exposes_unused_tools_and_mixed_versions() -> None:
    rows = [
        {
            "qid": "q1", "tier": "plain", "run": 0, "model": "m", "provider": "p",
            "leakfree": "timevault", "scaffold_version": "1", "n_searches": 0,
            "n_full_reads": 0,
        },
        {
            "qid": "q2", "tier": "plain", "run": 0, "model": "m", "provider": "p",
            "leakfree": "timevault", "scaffold_version": "2", "n_searches": 1,
            "n_full_reads": 1, "semantic_telemetry_complete": True,
            "n_full_reads_succeeded": 0, "n_full_reads_unavailable": 1,
            "n_tool_errors": 0,
        },
        {
            "qid": "q1", "tier": "plain", "run": 1, "model": "m", "provider": "p",
            "leakfree": "timevault", "scaffold_version": "2",
        },
    ]

    summary = validity.summarize_results(rows)

    assert summary["rows"] == 2
    assert summary["ignored_other_runs"] == 1
    assert summary["heterogeneous_fields"] == ["scaffold_version"]
    plain = summary["tiers"]["plain"]
    assert plain["telemetry_rows"] == 2
    assert plain["search_active_rows"] == 1
    assert plain["read_active_rows"] == 1
    assert plain["semantic_rows"] == 1
    assert plain["semantic_incomplete_rows"] == 0
    assert plain["successful_reads"] == 0
    assert plain["unavailable_reads"] == 1
    assert "FAIL (mixed scaffold_version)" in validity.render(summary)


def test_incomplete_semantic_row_is_not_coerced_to_zero_success() -> None:
    row = {
        "qid": "q1", "tier": "high", "run": 0, "model": "m", "provider": "p",
        "leakfree": "timevault", "scaffold_version": "1", "n_searches": 1,
        "n_full_reads": 1, "semantic_telemetry_complete": False,
        "n_full_reads_succeeded": None, "n_full_reads_unavailable": None,
    }

    summary = validity.summarize_results([row])
    high = summary["tiers"]["high"]

    assert high["semantic_rows"] == 0
    assert high["semantic_incomplete_rows"] == 1
    assert high["successful_reads"] is None
    assert high["unavailable_reads"] is None
    assert "1 incomplete row(s)" in validity.render(summary)


def test_substrate_summary_keeps_any_hit_and_readability_separate() -> None:
    rows = [
        {
            "global_discoverable_top25": True,
            "global_discoverable_top8": True,
            "qid_scoped_discoverable_top25": True,
            "production_cutoff_eligible_rate": 0.4,
            "source_urls": 100,
            "wayback_readable": True,
            "failure_reason": None,
        },
        {
            "global_discoverable_top25": False,
            "global_discoverable_top8": False,
            "qid_scoped_discoverable_top25": False,
            "production_cutoff_eligible_rate": 0.6,
            "source_urls": 200,
            "wayback_readable": None,
            "failure_reason": "lexical_miss",
        },
    ]

    summary = validity.summarize_substrate(rows)

    assert summary == {
        "questions": 2,
        "global_top25": 1,
        "global_top8": 1,
        "scoped_top25": 1,
        "mean_cutoff_eligibility": 0.5,
        "median_relevant_urls": 150.0,
        "readable": 1,
        "readability_tested": 1,
        "failure_classes": {"discovered": 1, "lexical_miss": 1},
    }
