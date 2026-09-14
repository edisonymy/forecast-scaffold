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


def test_marketplace_entry_mirrors_the_plugin_manifest() -> None:
    """The catalog entry is what a surface renders *before* it fetches the plugin, so it
    carries the version and display name too. Claude Code resolves plugin.json first and
    silently ignores a marketplace-entry version that disagrees, which would show one
    version in the catalog and install another — pin them together here."""
    import json
    from pathlib import Path

    manifests = Path(__file__).parents[1] / ".claude-plugin"
    manifest = json.loads((manifests / "plugin.json").read_text())
    marketplace = json.loads((manifests / "marketplace.json").read_text())

    entries = [p for p in marketplace["plugins"] if p["name"] == manifest["name"]]
    assert len(entries) == 1, "exactly one catalog entry names this plugin"
    entry = entries[0]
    assert entry["version"] == manifest["version"]
    assert entry["displayName"] == manifest["displayName"]
