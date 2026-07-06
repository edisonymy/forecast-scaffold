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


def test_scaffold_version_matches_plugin_manifest() -> None:
    """core.SCAFFOLD_VERSION is the methodology version stamped into every record;
    it must never drift from the plugin's published version — or from the pip package
    metadata (`pip install -e .` in every workflow reads pyproject.toml)."""
    import json
    from pathlib import Path

    from forecast_scaffold.core import SCAFFOLD_VERSION

    manifest = json.loads(
        (Path(__file__).parents[1] / ".claude-plugin" / "plugin.json").read_text()
    )
    assert manifest["version"] == SCAFFOLD_VERSION
    with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as fh:
        pyproject = tomllib.load(fh)
    assert pyproject["project"]["version"] == SCAFFOLD_VERSION
