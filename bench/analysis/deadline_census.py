"""Validate and read out the frozen BTF-2 deadline-router census.

This is preregistration scaffolding, not an experiment result.  The census was made from
question text, resolution criteria, and background only.  It excludes the standard
Opus contamination-probe flags and the known ECB memory-claim question.

PRE-REGISTERED GATE (frozen before running the paid A/B):
net paired Brier; tagged development delta >= +0.015 and non-deadline controls degrade
<0.003 promote; controls >=0.005 kill; +/-0.002 contamination guard on non-fired
questions; motivating 10 never enter promote decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CENSUS = ROOT / "bench/analysis/deadline-census.jsonl"
DEFAULT_SET = ROOT / "bench/sets/btf2-loop1-adm.jsonl"
DEFAULT_PROBE = ROOT / "bench/results/btf2-loop1-adm.probe.jsonl"
EXPECTED_CENSUS_SIZE = 152

PREREGISTERED_GATE = (
    "net paired Brier; tagged development delta >= +0.015 and non-deadline controls "
    "degrade <0.003 promote; controls >=0.005 kill; +/-0.002 contamination guard on "
    "non-fired questions; motivating 10 never enter promote decision."
)

MEMORY_CLAIM_EXCLUSIONS = {
    "btf2:516f111d-d70e-5198-95dc-5d38c0d9d789",
}

# Frozen Opus probe exclusions used to construct btf2-loop1-adm.  Keeping them here
# lets the checked-in census remain mechanically testable when gitignored bench data is
# absent (for example, in CI).  The CLI additionally reads the live probe file and takes
# the union, so a regenerated probe cannot silently re-admit a flagged question.
FROZEN_OPUS_PROBE_EXCLUSIONS = {
    "btf2:0db029c2-c88e-5a55-9b5e-9c0711a3ed54",
    "btf2:1919502f-f417-58f9-bdfa-4215873a58c6",
    "btf2:29af2b33-fec3-58b0-a166-bf613d6491a0",
    "btf2:34d37df5-83f6-57dc-8c6b-badfefd2fa29",
    "btf2:55e237ae-48b0-55b2-9eae-07cf334ffbc6",
    "btf2:6bb3672e-e286-54b7-8151-1a65e7f549ed",
    "btf2:7f8cd5ae-f409-510d-a9e0-8d8eb32c0c11",
    "btf2:83deb2d0-7e69-5307-91d8-b5154746a13a",
    "btf2:94e6af9c-31e0-58b0-9a0b-c3bcf0985830",
    "btf2:b44671aa-2062-5c30-b661-83ffddfcd57c",
    "btf2:bbff54cb-87a7-51dc-8b16-af56444fcf12",
    "btf2:dee5e781-8275-5581-81af-0c96bf1d5e82",
    "btf2:dfb11e5e-1c85-5177-b355-fc7994b92388",
    "btf2:ea22eb9c-0410-5719-87e1-27b488ee6c0f",
}

MOTIVATING_HOLDOUTS = {
    "btf2:c64ecb5a-054c-52a2-8a90-d77ec8dfe928",
    "btf2:07854f30-34e8-5f2d-b4be-7db1c250da14",
    "btf2:99dd2609-88ad-502e-952c-0f1659aec783",
    "btf2:bd6fa0a6-5e00-5a6c-a6ba-3b137776700d",
    "btf2:9fd6053d-c076-5a5c-8859-99cad6b59903",
    "btf2:67f82374-974c-5cf0-a62a-44464de3186e",
    "btf2:9c4cbcdb-3de5-52ff-8a1a-06150c5df316",
    "btf2:653561e8-06ed-5881-9191-e1430d1109d6",
    "btf2:95847275-38a8-5e0d-97c7-295583b753d4",
    "btf2:c8843e8c-db7b-511d-a9f3-ea84a1cfa9c3",
}

REQUIRED_KEYS = {
    "qid",
    "institutional_action_by_deadline",
    "basis",
    "holdout_motivator",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load non-empty JSONL rows from *path*."""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def probe_exclusions(
    rows: list[dict[str, Any]], model_match: str = "opus"
) -> set[str]:
    """Return confident-correct recall flags for the selected model family."""
    needle = model_match.casefold()
    return {
        str(row["qid"])
        for row in rows
        if needle in str(row.get("model", "")).casefold()
        and bool(row.get("correct"))
        and float(row.get("confidence", 0.0)) >= 0.75
    }


def partition(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Partition the frozen census into the three preregistered readout groups."""
    return {
        "tagged_development": [
            row["qid"]
            for row in rows
            if row["institutional_action_by_deadline"]
            and not row["holdout_motivator"]
        ],
        "held_out_motivating": [
            row["qid"] for row in rows if row["holdout_motivator"]
        ],
        "non_fired_controls": [
            row["qid"]
            for row in rows
            if not row["institutional_action_by_deadline"]
        ],
    }


def validate_census(
    rows: list[dict[str, Any]],
    specs: list[dict[str, Any]] | None = None,
    *,
    excluded_qids: set[str] | None = None,
    expected_size: int = EXPECTED_CENSUS_SIZE,
) -> dict[str, list[str]]:
    """Validate schema, exclusions, source coverage/order, and holdout isolation."""
    excluded = set(excluded_qids or ())
    excluded |= MEMORY_CLAIM_EXCLUSIONS | FROZEN_OPUS_PROBE_EXCLUSIONS

    if len(rows) != expected_size:
        raise ValueError(f"expected {expected_size} census rows, found {len(rows)}")

    qids: list[str] = []
    for number, row in enumerate(rows, start=1):
        if set(row) != REQUIRED_KEYS:
            raise ValueError(
                f"row {number} keys must be exactly {sorted(REQUIRED_KEYS)}"
            )
        qid = row["qid"]
        if not isinstance(qid, str) or not qid.startswith("btf2:"):
            raise ValueError(f"row {number} has invalid qid {qid!r}")
        if type(row["institutional_action_by_deadline"]) is not bool:
            raise ValueError(f"{qid}: institutional label must be boolean")
        if type(row["holdout_motivator"]) is not bool:
            raise ValueError(f"{qid}: holdout label must be boolean")
        if not isinstance(row["basis"], str) or len(row["basis"].strip()) < 12:
            raise ValueError(f"{qid}: basis must be a concise non-empty explanation")
        qids.append(qid)

    if len(set(qids)) != len(qids):
        duplicates = sorted({qid for qid in qids if qids.count(qid) > 1})
        raise ValueError(f"duplicate census qids: {duplicates}")

    leaked = sorted(set(qids) & excluded)
    if leaked:
        raise ValueError(f"excluded/contaminated qids present: {leaked}")

    if specs is not None:
        source_qids = [str(spec["id"]) for spec in specs]
        expected_qids = [qid for qid in source_qids if qid not in excluded]
        if set(qids) != set(expected_qids):
            missing = sorted(set(expected_qids) - set(qids))
            extra = sorted(set(qids) - set(expected_qids))
            raise ValueError(f"source mismatch: missing={missing}, extra={extra}")
        if qids != expected_qids:
            raise ValueError("census rows must preserve admissible-set file order")

    actual_holdouts = {
        row["qid"] for row in rows if row["holdout_motivator"]
    }
    if actual_holdouts != MOTIVATING_HOLDOUTS:
        missing = sorted(MOTIVATING_HOLDOUTS - actual_holdouts)
        extra = sorted(actual_holdouts - MOTIVATING_HOLDOUTS)
        raise ValueError(f"holdout mismatch: missing={missing}, extra={extra}")

    untagged_holdouts = sorted(
        row["qid"]
        for row in rows
        if row["holdout_motivator"]
        and not row["institutional_action_by_deadline"]
    )
    if untagged_holdouts:
        raise ValueError(f"motivating holdouts must be router-tagged: {untagged_holdouts}")

    groups = partition(rows)
    partitioned = sum((values for values in groups.values()), [])
    if len(partitioned) != len(rows) or set(partitioned) != set(qids):
        raise ValueError("readout groups do not partition the census exactly once")
    return groups


def render_readout(
    rows: list[dict[str, Any]], groups: dict[str, list[str]], *, show_qids: bool
) -> str:
    """Render a no-results census readout and the frozen gate."""
    lines = [
        "deadline router census (labels only; no forecasts)",
        f"all admissible questions: {len(rows)}",
        f"tagged development: {len(groups['tagged_development'])}",
        f"held-out motivating: {len(groups['held_out_motivating'])}",
        f"non-fired controls: {len(groups['non_fired_controls'])}",
        "motivating holdouts are diagnostic only and never enter the promote decision",
        f"preregistered gate: {PREREGISTERED_GATE}",
    ]
    if show_qids:
        for name, qids in groups.items():
            lines.append(f"\n{name} ({len(qids)}):")
            lines.extend(f"  {qid}" for qid in qids)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--set", dest="set_path", type=Path, default=DEFAULT_SET)
    parser.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    parser.add_argument("--model-match", default="opus")
    parser.add_argument("--show-qids", action="store_true")
    args = parser.parse_args(argv)

    rows = load_jsonl(args.census)
    specs = load_jsonl(args.set_path)
    excluded = MEMORY_CLAIM_EXCLUSIONS | FROZEN_OPUS_PROBE_EXCLUSIONS
    if args.probe.exists():
        excluded |= probe_exclusions(load_jsonl(args.probe), args.model_match)
    groups = validate_census(rows, specs, excluded_qids=excluded)
    print(render_readout(rows, groups, show_qids=args.show_qids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
