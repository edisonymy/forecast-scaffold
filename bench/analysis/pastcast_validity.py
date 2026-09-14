"""Audit whether a pastcast result file exercised credible research mechanics.

This is deliberately not a score readout.  It inspects provenance, tool-use telemetry,
and the optional substrate-recall proxy before anyone interprets Brier differences.  In
particular, ``n_searches``/``n_full_reads`` in legacy rows are attempts, not proof that
evidence was returned; only the semantic fields added in v0.4.22 distinguish successful
reads, unavailable captures, and errors.

The substrate input is also diagnostic: its relevant set is every published
question-to-URL edge, not teacher-cited or load-bearing pages.  Any-hit recall over
thousands of URLs must never be described as load-bearing evidence recall.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

Json = dict[str, Any]


def load_jsonl(path: Path) -> list[Json]:
    """Load non-empty JSONL objects from *path*."""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_index(row: Json) -> int:
    """Legacy missing/null run fields mean run zero."""
    raw = row.get("run")
    return 0 if raw is None else int(raw)


def _values(rows: Iterable[Json], key: str) -> list[str]:
    return sorted({str(row[key]) for row in rows if row.get(key) not in (None, "")})


def _mean(values: list[float]) -> float | None:
    return st.mean(values) if values else None


def _median(values: list[float]) -> float | None:
    return st.median(values) if values else None


def summarize_results(rows: list[Json], *, run: int = 0) -> Json:
    """Summarize provenance and research activity without reading probabilities."""
    selected = [row for row in rows if run_index(row) == run]
    tiers: dict[str, Json] = {}
    for tier, tier_rows_iter in _group_rows(selected, "tier"):
        tier_rows = list(tier_rows_iter)
        telemetry = [row for row in tier_rows if row.get("n_searches") is not None]
        searches = [float(row["n_searches"]) for row in telemetry]
        reads = [float(row["n_full_reads"]) for row in telemetry]
        semantic = [
            row for row in telemetry
            if row.get("semantic_telemetry_complete") is True
        ]
        semantic_incomplete = sum(
            row.get("semantic_telemetry_complete") is False for row in telemetry
        )
        tiers[tier] = {
            "rows": len(tier_rows),
            "telemetry_rows": len(telemetry),
            "search_active_rows": sum(value > 0 for value in searches),
            "read_active_rows": sum(value > 0 for value in reads),
            "mean_search_attempts": _mean(searches),
            "median_search_attempts": _median(searches),
            "mean_read_attempts": _mean(reads),
            "median_read_attempts": _median(reads),
            "semantic_rows": len(semantic),
            "semantic_incomplete_rows": semantic_incomplete,
            "successful_reads": sum(
                int(row.get("n_full_reads_succeeded") or 0) for row in semantic
            ) if semantic else None,
            "unavailable_reads": sum(
                int(row.get("n_full_reads_unavailable") or 0) for row in semantic
            ) if semantic else None,
            "tool_errors": sum(
                int(row.get("n_tool_errors") or 0) for row in semantic
            ) if semantic else None,
        }
    provenance = {
        key: _values(selected, key)
        for key in ("model", "provider", "leakfree", "scaffold_version")
    }
    return {
        "run": run,
        "rows": len(selected),
        "ignored_other_runs": len(rows) - len(selected),
        "unique_qids": len({row.get("qid") for row in selected}),
        "provenance": provenance,
        "heterogeneous_fields": [
            key for key, values in provenance.items() if len(values) > 1
        ],
        "tiers": tiers,
    }


def _group_rows(rows: list[Json], key: str) -> Iterable[tuple[str, list[Json]]]:
    grouped: dict[str, list[Json]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key) or "<missing>"), []).append(row)
    return sorted(grouped.items())


def summarize_substrate(rows: list[Json]) -> Json:
    """Summarize the question-source-set proxy without upgrading what it measures."""
    tested = [row for row in rows if row.get("wayback_readable") is not None]
    failures = Counter(str(row.get("failure_reason") or "discovered") for row in rows)
    return {
        "questions": len(rows),
        "global_top25": sum(bool(row.get("global_discoverable_top25")) for row in rows),
        "global_top8": sum(bool(row.get("global_discoverable_top8")) for row in rows),
        "scoped_top25": sum(
            bool(row.get("qid_scoped_discoverable_top25")) for row in rows
        ),
        "mean_cutoff_eligibility": st.mean(
            float(row["production_cutoff_eligible_rate"]) for row in rows
        ) if rows else None,
        "median_relevant_urls": st.median(
            int(row["source_urls"]) for row in rows
        ) if rows else None,
        "readable": sum(row.get("wayback_readable") is True for row in tested),
        "readability_tested": len(tested),
        "failure_classes": dict(sorted(failures.items())),
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render(summary: Json, substrate: Json | None = None) -> str:
    """Render a compact capability/provenance diagnostic."""
    lines = [
        "pastcast validity diagnostic (mechanics/provenance; NOT a score readout)",
        f"run={summary['run']} rows={summary['rows']} unique_qids={summary['unique_qids']} "
        f"ignored_other_runs={summary['ignored_other_runs']}",
    ]
    provenance = summary["provenance"]
    for key in ("model", "provider", "leakfree", "scaffold_version"):
        lines.append(f"{key}: {', '.join(provenance[key]) or 'missing'}")
    heterogeneous = summary["heterogeneous_fields"]
    lines.append(
        "provenance homogeneity: "
        + ("FAIL (mixed " + ", ".join(heterogeneous) + ")" if heterogeneous else "pass")
    )
    lines += [
        "",
        "tier telemetry (attempts are not evidence-return success)",
        "tier       rows telemetry active-search active-read mean-search median-search "
        "mean-read median-read semantic-complete",
    ]
    for tier, item in summary["tiers"].items():
        lines.append(
            f"{tier:10s} {item['rows']:4d} {item['telemetry_rows']:9d} "
            f"{item['search_active_rows']:13d} {item['read_active_rows']:11d} "
            f"{_fmt(item['mean_search_attempts']):>11s} "
            f"{_fmt(item['median_search_attempts']):>13s} "
            f"{_fmt(item['mean_read_attempts']):>9s} "
            f"{_fmt(item['median_read_attempts']):>11s} "
            f"{item['semantic_rows']:8d}"
        )
    if all(item["semantic_rows"] == 0 for item in summary["tiers"].values()):
        lines.append(
            "semantic evidence-return telemetry: unavailable in these legacy rows; "
            "do not equate read attempts with readable pages"
        )
    incomplete = sum(
        item["semantic_incomplete_rows"] for item in summary["tiers"].values()
    )
    if incomplete:
        lines.append(
            f"semantic evidence-return telemetry: {incomplete} incomplete row(s); "
            "their success/unavailable totals remain unknown"
        )

    if substrate is not None:
        n = substrate["questions"]
        eligibility = substrate["mean_cutoff_eligibility"]
        lines += [
            "",
            "substrate proxy (question-source-set any-hit; NOT load-bearing recall)",
            f"global discoverable @25: {substrate['global_top25']}/{n}",
            f"global discoverable @8: {substrate['global_top8']}/{n}",
            f"question-scoped discoverable @25: {substrate['scoped_top25']}/{n}",
            f"mean cutoff eligibility: "
            f"{_fmt(eligibility * 100 if eligibility is not None else None, 1)}%",
            f"median published relevant-set size: "
            f"{_fmt(substrate['median_relevant_urls'], 1)} URLs/question",
            f"Wayback readable: {substrate['readable']}/{substrate['readability_tested']}",
            "failure classes: " + ", ".join(
                f"{key}={value}" for key, value in substrate["failure_classes"].items()
            ),
        ]
    lines += [
        "",
        "boundary: inspect contamination and memory claims separately before scoring; "
        "this diagnostic cannot certify model-weight cleanliness or numeric-forecast support",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="benchmark result JSONL")
    parser.add_argument("--run", type=int, default=0)
    parser.add_argument(
        "--substrate-details",
        type=Path,
        help="optional substrate_recall --details-out JSONL",
    )
    parser.add_argument("--json", action="store_true", help="emit the summaries as JSON")
    args = parser.parse_args(argv)

    summary = summarize_results(load_jsonl(args.results), run=args.run)
    substrate = (
        summarize_substrate(load_jsonl(args.substrate_details))
        if args.substrate_details else None
    )
    if args.json:
        print(json.dumps({"results": summary, "substrate": substrate}, indent=2))
    else:
        print(render(summary, substrate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
