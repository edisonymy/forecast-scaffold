"""Unit tests for run_bot's seasonal tournament slug auto-discovery [ADDED 2026-09-03].

Metaculus starts a new "FutureEval Bot Tournament" every January, May and September under
a slug that does not exist until they create it, so a --tournament roster edited after the
fact means zero coverage for the first days of every season. discover_seasonal_slugs and
merged_tournament_slugs are the pure, network-free halves of that feature — see
MetaculusClient.tournaments (tests/test_bot_client.py) for the transport side, and
run_bot.main for how the two are wired together around collect_open_posts.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

import run_bot  # noqa: E402


class TestDiscoverSeasonalSlugs:
    NOW = datetime(2026, 9, 3, tzinfo=UTC)

    def test_selects_only_the_active_seasonal_tournament(self) -> None:
        projects: list[dict[str, Any]] = [
            {  # active: started, still forecastable -> the one we want
                "id": 1,
                "slug": "summer-futureeval-2026",
                "name": "Summer 2026 FutureEval Bot Tournament",
                "start_date": "2026-06-01T00:00:00Z",
                "forecasting_end_date": "2026-09-30T00:00:00Z",
                "close_date": "2026-10-07T00:00:00Z",
            },
            {  # ended: both end dates are already in the past
                "id": 2,
                "slug": "spring-aib-2026",
                "name": "Spring 2026 FutureEval Bot Tournament",
                "start_date": "2026-02-01T00:00:00Z",
                "forecasting_end_date": "2026-05-31T00:00:00Z",
                "close_date": "2026-06-07T00:00:00Z",
            },
            {  # upcoming: start_date is still in the future
                "id": 3,
                "slug": "fall-futureeval-2026",
                "name": "Fall 2026 FutureEval Bot Tournament",
                "start_date": "2026-10-01T00:00:00Z",
                "forecasting_end_date": "2027-01-31T00:00:00Z",
                "close_date": "2027-02-07T00:00:00Z",
            },
            {  # unrelated standing tournament: name doesn't match, dates irrelevant
                "id": 4,
                "slug": "metaculus-cup",
                "name": "Metaculus Cup",
                "start_date": "2026-01-01T00:00:00Z",
                "forecasting_end_date": "2026-12-31T00:00:00Z",
                "close_date": "2027-01-07T00:00:00Z",
            },
            {  # practice round: matches the naming AND would be date-active, but excluded
                "id": 5,
                "slug": "summer-futureeval-2026-practice",
                "name": "Summer 2026 FutureEval Bot Tournament Practice Round",
                "start_date": "2026-06-01T00:00:00Z",
                "forecasting_end_date": "2026-09-30T00:00:00Z",
                "close_date": "2026-10-07T00:00:00Z",
            },
            {  # seasonal name but no end date at all -> can't confirm it's still open
                "id": 6,
                "slug": "mystery-futureeval",
                "name": "Mystery FutureEval Bot Tournament",
            },
        ]
        assert run_bot.discover_seasonal_slugs(projects, self.NOW) == [
            "summer-futureeval-2026"
        ]

    def test_a_season_past_its_forecasting_cutoff_is_dropped(self) -> None:
        """[AMENDED 2026-09-09] Summer 2026 stopped accepting forecasts on Sep 6 but does
        not close until Nov 5. Keying on the later date kept a dead slug in the roster for
        two months — an API call per tick, and a dead slug that looks like a healthy one."""
        summer = {
            "id": 1,
            "slug": "summer-futureeval-2026",
            "name": "Summer 2026 FutureEval Bot Tournament",
            "start_date": "2026-05-18T00:00:00Z",
            "forecasting_end_date": "2026-09-06T00:00:00Z",
            "close_date": "2026-11-05T00:00:00Z",
        }
        before = datetime(2026, 9, 5, tzinfo=UTC)
        after = datetime(2026, 9, 9, tzinfo=UTC)
        assert run_bot.discover_seasonal_slugs([summer], before) == ["summer-futureeval-2026"]
        assert run_bot.discover_seasonal_slugs([summer], after) == []

    def test_close_date_is_the_fallback_when_there_is_no_forecasting_cutoff(self) -> None:
        project = {
            "id": 2,
            "slug": "winter-futureeval-2027",
            "name": "Winter 2027 FutureEval Bot Tournament",
            "start_date": "2026-09-01T00:00:00Z",
            "close_date": "2026-12-31T00:00:00Z",
        }
        assert run_bot.discover_seasonal_slugs(
            [project], datetime(2026, 9, 9, tzinfo=UTC)) == ["winter-futureeval-2027"]

    def test_sorted_by_start_date_descending(self) -> None:
        projects = [
            {
                "slug": "older",
                "name": "Fall 2025 AI Forecasting Benchmark Tournament",
                "start_date": "2025-09-01T00:00:00Z",
                "close_date": "2026-12-31T00:00:00Z",
            },
            {
                "slug": "newer",
                "name": "Summer 2026 FutureEval Bot Tournament",
                "start_date": "2026-06-01T00:00:00Z",
                "close_date": "2026-12-31T00:00:00Z",
            },
        ]
        assert run_bot.discover_seasonal_slugs(projects, self.NOW) == ["newer", "older"]

    def test_missing_start_date_has_no_lower_bound(self) -> None:
        projects = [{
            "slug": "no-start-date",
            "name": "Summer 2026 FutureEval Bot Tournament",
            "close_date": "2026-12-31T00:00:00Z",
        }]
        assert run_bot.discover_seasonal_slugs(projects, self.NOW) == ["no-start-date"]

    def test_empty_project_list_returns_empty(self) -> None:
        assert run_bot.discover_seasonal_slugs([], self.NOW) == []


class TestMergedTournamentSlugs:
    """Discovery must only ADD coverage: configured slugs always lead and survive."""

    def test_preserves_configured_order_and_dedupes(self) -> None:
        merged = run_bot.merged_tournament_slugs(
            "season, minibench", ["minibench", "summer-futureeval-2026"]
        )
        assert merged == "season,minibench,summer-futureeval-2026"

    def test_no_discovered_slugs_leaves_configured_list_unchanged(self) -> None:
        assert run_bot.merged_tournament_slugs("season,minibench", []) == "season,minibench"

    def test_blank_configured_falls_back_to_discovered_only(self) -> None:
        merged = run_bot.merged_tournament_slugs("", ["summer-futureeval-2026"])
        assert merged == "summer-futureeval-2026"

    def test_discovered_slug_already_configured_is_not_duplicated(self) -> None:
        merged = run_bot.merged_tournament_slugs(
            "summer-futureeval-2026", ["summer-futureeval-2026"]
        )
        assert merged == "summer-futureeval-2026"
