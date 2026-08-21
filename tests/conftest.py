"""Session-wide test guards.

The tournament research run now OPTIONALLY starts from AskNews articles (bot/asknews.py),
appended inside forecast_question. On a dev machine where the operator's real keyfile
(~/.asknews/key[.txt]) exists, that would make LIVE API calls inside every forecast_question
test. So default AskNews OFF for the whole suite via its documented kill switch: no test
touches the network unless it explicitly opts in (the asknews tests clear this and stub
urllib). This also keeps every pre-existing research-run assertion byte-identical, since a
disabled news_section() returns "" and brief + "" == brief.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
import run_manifold  # noqa: E402  — needs the bot/ path above


@pytest.fixture(autouse=True)
def _asknews_dark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASKNEWS_DISABLE", "1")
    monkeypatch.delenv("ASKNEWS_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _no_inherited_metered_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear metered/gateway auth env for every test [ADDED 2026-08-21].

    ``run_manifold`` refuses to run when any of these is set (it is subscription-only), so a
    developer shell that exports one — ANTHROPIC_BASE_URL is the common case — made every
    run()-level test exit 2 locally while passing in CI. That is the worst failure shape: it
    looks like inert environmental noise, so it gets filtered out of "did my change break
    anything", and it silently removed ~25 tests from local coverage. A real bug (a bet
    ``fill`` dropped by build_record) reached CI because of exactly that blind spot.

    Tests that exercise the guard set these vars deliberately via monkeypatch, which still
    works — this only stops the AMBIENT shell from deciding what the suite covers."""
    for name in run_manifold.METERED_AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
