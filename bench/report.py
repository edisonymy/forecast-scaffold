"""Score a benchmark run: how close does each tier land to the teachers, per dollar?

Teachers: the crowd value frozen in the set file, and the ``high`` tier's own forecast
on the same question. Students: every tier present in the results. Binary only.

Usage:
    python bench/report.py bench/sets/2026-07-04.jsonl
    (reads bench/results/<setname>.results.jsonl, writes .report.md next to it)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "bench" / "results"
EPS = 1e-3  # clamp probabilities into [0.001, 0.999] before logit/KL


def _clamp(p: float) -> float:
    return min(max(p, EPS), 1.0 - EPS)


def logit(p: float) -> float:
    p = _clamp(p)
    return math.log(p / (1.0 - p))


def kl_bernoulli(q: float, p: float) -> float:
    """KL(teacher q || student p) in nats."""
    q, p = _clamp(q), _clamp(p)
    return q * math.log(q / p) + (1.0 - q) * math.log((1.0 - q) / (1.0 - p))


def gap_stats(pairs: list[tuple[float, float]]) -> dict[str, float]:
    """pairs = [(teacher, student), ...] -> the four gap metrics."""
    n = len(pairs)
    abs_dp = [abs(t - s) for t, s in pairs]
    return {
        "n": n,
        "mean_abs_dp": sum(abs_dp) / n,
        "rms_dp": math.sqrt(sum(d * d for d in abs_dp) / n),
        "mean_kl": sum(kl_bernoulli(t, s) for t, s in pairs) / n,
        "mean_abs_dlogit": sum(abs(logit(t) - logit(s)) for t, s in pairs) / n,
    }


def fmt_row(label: str, stats: dict[str, float], cost: float) -> str:
    return (f"| {label} | {stats['n']} | {stats['mean_abs_dp']:.3f} | {stats['rms_dp']:.3f} "
            f"| {stats['mean_kl']:.3f} | {stats['mean_abs_dlogit']:.2f} | ${cost:.2f} |")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which cannot print Δ; the report file is UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("set_file")
    args = parser.parse_args(argv)

    set_path = Path(args.set_file)
    results_path = RESULTS_DIR / f"{set_path.stem}.results.jsonl"
    if not results_path.exists():
        print(f"no results at {results_path}; run bench/run_bench.py first")
        return 1
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    # Latest row wins per (qid, tier): reruns supersede earlier attempts.
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        latest[(row["qid"], row["tier"])] = row
    by_tier: dict[str, list[dict]] = defaultdict(list)
    for row in latest.values():
        by_tier[row["tier"]].append(row)
    high_by_qid = {row["qid"]: row["probability"] for row in by_tier.get("high", [])}

    lines: list[str] = []
    versions = sorted({row.get("scaffold_version", "?") for row in latest.values()})
    models = sorted({row.get("model", "?") for row in latest.values()})
    providers = sorted({row.get("provider", "?") for row in latest.values()})
    lines.append(f"# Benchmark report: {set_path.stem}")
    lines.append("")
    lines.append(f"scaffold_version {', '.join(versions)} · model {', '.join(models)} "
                 f"· provider {', '.join(providers)}")
    lines.append("")
    lines.append("## vs crowd (teacher = market/community probability in the set)")
    lines.append("")
    lines.append("| tier | n | mean \\|Δp\\| | RMS Δp | mean KL | mean \\|Δlogit\\| | total cost |")
    lines.append("|---|---|---|---|---|---|---|")
    for tier in ("low", "medium", "high", "auto"):
        tier_rows = by_tier.get(tier)
        if not tier_rows:
            continue
        pairs = [(float(r["crowd"]["value"]), float(r["probability"])) for r in tier_rows
                 if r.get("crowd") and r["crowd"].get("value") is not None]
        if not pairs:
            continue
        cost = sum(r.get("cost_usd") or 0.0 for r in tier_rows)
        lines.append(fmt_row(tier, gap_stats(pairs), cost))
    lines.append("")

    header = "| tier | n | mean \\|Δp\\| | RMS Δp | mean KL | mean \\|Δlogit\\| | total cost |"
    if high_by_qid:
        lines.append("## vs high tier (teacher = this scaffold's own best effort)")
        lines.append("")
        lines.append(header)
        lines.append("|---|---|---|---|---|---|---|")
        for tier in ("low", "medium", "auto"):
            tier_rows = by_tier.get(tier)
            if not tier_rows:
                continue
            pairs = [(high_by_qid[r["qid"]], float(r["probability"])) for r in tier_rows
                     if r["qid"] in high_by_qid]
            if not pairs:
                continue
            cost = sum(r.get("cost_usd") or 0.0 for r in tier_rows)
            lines.append(fmt_row(tier, gap_stats(pairs), cost))
        lines.append("")

    if by_tier.get("auto"):
        resolved = Counter(row.get("effort", "?") for row in by_tier["auto"])
        lines.append("## auto triage routing")
        lines.append("")
        for effort, count in resolved.most_common():
            lines.append(f"- {effort}: {count}")
        lines.append("")

    report = "\n".join(lines)
    out = RESULTS_DIR / f"{set_path.stem}.report.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
