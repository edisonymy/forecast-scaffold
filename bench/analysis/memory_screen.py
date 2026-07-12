"""Memory-claim prefilter for one or more benchmark result files.

Mechanically shortlist rows whose reasoning may assert the question's outcome as
remembered fact (weights leakage surfacing mid-forecast). Apply the screen uniformly to
every arm, then read and judge the candidates. This is a pastcast-only artifact: a live
bot cannot remember an unresolved outcome.

With no paths, the CLI retains the original base/premortem/skeptic behavior. For a
multi-run result file, pass ``--run 0`` so only the pre-registered run is screened::

    python bench/analysis/memory_screen.py RESULTS.jsonl --run 0
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LEGACY_ARMS = ("base", "premortem", "skeptic")

PATTERNS = re.compile(
    r"already (occurred|happened|taken place|resolved|been "
    r"(announced|adopted|decided|published|signed|held|issued|launched|confirmed|cut))"
    r"|\bI (recall|remember)\b"
    r"|event (has|had) (already )?occurred"
    r"|high confidence as this"
    r"|(this|the) (event|outcome) (is|was) (already )?(known|certain|settled)"
    r"|from memory\b",
    re.IGNORECASE,
)

ResultRow = dict[str, Any]
Candidate = tuple[ResultRow, re.Match[str]]


def load_jsonl(path: Path) -> list[ResultRow]:
    """Load non-empty JSON objects from *path*."""
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def run_index(row: ResultRow) -> int:
    """Return the recorded run, treating a legacy missing/null run as run zero."""
    raw = row.get("run")
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid run value {raw!r} for qid {row.get('qid')!r}") from exc


def screened_rows(rows: Iterable[ResultRow], run: int | None = None) -> list[ResultRow]:
    """Materialize rows selected by the optional run filter."""
    return [row for row in rows if run is None or run_index(row) == run]


def find_candidates(rows: Iterable[ResultRow], run: int | None = None) -> tuple[int, list[Candidate]]:
    """Return ``(rows_screened, regex_hits)`` for importers and tests."""
    selected = screened_rows(rows, run)
    candidates: list[Candidate] = []
    for row in selected:
        match = PATTERNS.search(row.get("reasoning") or "")
        if match:
            candidates.append((row, match))
    return len(selected), candidates


def format_report(label: str, rows: Iterable[ResultRow], run: int | None = None) -> str:
    """Render one screen report without reading any files or printing."""
    total, candidates = find_candidates(rows, run)
    run_note = "" if run is None else f", run={run} only"
    lines = [f"\n=== {label}: {len(candidates)} candidate(s) of {total} rows{run_note} ==="]
    for row, match in candidates:
        lines.append(
            f"--- {row.get('qid', '<missing qid>')}  "
            f"p={row.get('probability')}  match={match.group(0)!r}"
        )
        excerpt = (row.get("reasoning") or "")[:400].replace("\n", " ")
        lines.append(f"    {excerpt}")
    return "\n".join(lines)


def legacy_paths() -> list[Path]:
    """Return the original three arm paths, including only files that exist."""
    paths = [ROOT / "bench/results" / f"btf2-loop1-adm.{arm}.results.jsonl"
             for arm in LEGACY_ARMS]
    return [path for path in paths if path.exists()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path,
                        help="result JSONL path(s); defaults to the three legacy arm files")
    parser.add_argument("--run", type=int,
                        help="screen only this run index (for tranche1, use --run 0)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = args.paths or legacy_paths()
    for path in paths:
        rows = load_jsonl(path)
        print(format_report(str(path), rows, args.run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
