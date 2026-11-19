"""Session-wide test guards.

"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
import run_manifold  # noqa: E402  — needs the bot/ path above


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


@pytest.fixture(autouse=True)
def _no_live_market_lookup(monkeypatch):
    """bot/markets.py searches Polymarket/Manifold on every sighted brief; tests must never
    reach the network. Failing the transport makes the lookup fail-open to its 'none found'
    section, which is exactly the production behaviour when a venue is down."""
    import sys
    from pathlib import Path as _P
    bot_dir = str(_P(__file__).resolve().parents[1] / "bot")
    if bot_dir not in sys.path:
        sys.path.insert(0, bot_dir)
    import markets

    def _offline(url):
        raise OSError("network disabled in tests")

    monkeypatch.setattr(markets, "_get_json", _offline)
