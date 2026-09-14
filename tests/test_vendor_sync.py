"""The vendored fsj.py copies must be byte-identical to the canonical core."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "src" / "forecast_scaffold" / "core.py"
VENDORED = [
    ROOT / "skills" / "forecast" / "scripts" / "fsj.py",
    ROOT / "skills" / "calibrate" / "scripts" / "fsj.py",
]


def test_vendored_copies_are_byte_identical() -> None:
    source = CANONICAL.read_bytes()
    for copy in VENDORED:
        assert copy.exists(), f"{copy} missing — run scripts/vendor_sync.py"
        assert copy.read_bytes() == source, f"{copy} stale — run scripts/vendor_sync.py"
