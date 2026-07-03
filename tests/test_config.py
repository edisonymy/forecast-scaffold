"""config/forecast.toml is the user-facing template; core.DEFAULTS is the single source of
numeric truth. They must be exactly equal so no constant ever lives in two places."""

from __future__ import annotations

import tomllib
from pathlib import Path

from forecast_scaffold.core import DEFAULTS

TEMPLATE = Path(__file__).resolve().parents[1] / "config" / "forecast.toml"


def test_template_mirrors_defaults_exactly() -> None:
    with TEMPLATE.open("rb") as fh:
        template = tomllib.load(fh)
    assert template == DEFAULTS
