"""One-way sync of the canonical core into each skill's vendored copy.

Skills must be standalone (a claude.ai bundle can't reach ``src/``), so every skill that
needs the tool carries a byte-identical copy of ``src/forecast_scaffold/core.py`` at
``scripts/fsj.py``. This script copies; ``--check`` verifies byte equality (CI gate).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "src" / "forecast_scaffold" / "core.py"
TARGETS = [
    ROOT / "skills" / "forecast" / "scripts" / "fsj.py",
    ROOT / "skills" / "calibrate" / "scripts" / "fsj.py",
]


def main(argv: list[str]) -> int:
    check = "--check" in argv
    source = CANONICAL.read_bytes()
    stale: list[Path] = []
    for target in TARGETS:
        if target.exists() and target.read_bytes() == source:
            continue
        if check:
            stale.append(target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source)
            print(f"synced {target.relative_to(ROOT)}")
    if check:
        if stale:
            for target in stale:
                print(f"STALE: {target.relative_to(ROOT)}", file=sys.stderr)
            print("fix with: python scripts/vendor_sync.py", file=sys.stderr)
            return 1
        print("vendored copies in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
