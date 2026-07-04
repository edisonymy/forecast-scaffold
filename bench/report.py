"""Score a benchmark run: how close does each tier land to the teachers, per dollar?

Teachers: the crowd value frozen in the set file, and the ``high`` tier's own forecast
on the same question. Students: every tier present in the results. Binary only.

Auto rows come in two shapes: full (probability recorded) and router-only (the triage
decision alone, ``router_only: true``). Router-only rows get their probability — and the
routed tier's cost — imputed from the paired standalone row, which is a lower-variance
measurement of the router than re-running the forecast. Full auto rows double as a
run-to-run repeatability probe against the standalone row at the same tier.

Usage:
    python bench/report.py bench/sets/2026-07-04.jsonl
    (reads bench/results/<setname>.results.jsonl, writes .report.md next to it)
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
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
    """pairs = [(teacher, student), ...] -> gap metrics + signed bias."""
    n = len(pairs)
    abs_dp = [abs(t - s) for t, s in pairs]
    return {
        "n": n,
        "mean_abs_dp": sum(abs_dp) / n,
        "median_abs_dp": st.median(abs_dp),
        "rms_dp": math.sqrt(sum(d * d for d in abs_dp) / n),
        "mean_kl": sum(kl_bernoulli(t, s) for t, s in pairs) / n,
        "mean_abs_dlogit": sum(abs(logit(t) - logit(s)) for t, s in pairs) / n,
        "bias": sum(s - t for t, s in pairs) / n,  # + = student above teacher
    }


HEADER = ("| tier | n | mean \\|Δp\\| | median \\|Δp\\| | RMS Δp | mean KL "
          "| mean \\|Δlogit\\| | bias | total cost |")
RULE = "|---|---|---|---|---|---|---|---|---|"


def fmt_row(label: str, s: dict[str, float], cost: float) -> str:
    return (f"| {label} | {s['n']} | {s['mean_abs_dp']:.3f} | {s['median_abs_dp']:.3f} "
            f"| {s['rms_dp']:.3f} | {s['mean_kl']:.3f} | {s['mean_abs_dlogit']:.2f} "
            f"| {s['bias']:+.3f} | ${cost:.2f} |")


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
    prob_of = {(r["qid"], r["tier"]): r["probability"] for r in latest.values()
               if r.get("probability") is not None}
    cost_of = {(r["qid"], r["tier"]): r.get("cost_usd") or 0.0 for r in latest.values()}
    high_by_qid = {r["qid"]: r["probability"] for r in by_tier.get("high", [])
                   if r.get("probability") is not None}

    # Effective auto rows: full rows as-is; router-only rows imputed from the routed tier.
    auto_effective: list[dict] = []
    repeat_pairs: list[tuple[str, float, float]] = []  # (routed tier, auto p, standalone p)
    for r in by_tier.get("auto", []):
        routed = str(r.get("effort", "")).split(" ")[0]
        standalone = prob_of.get((r["qid"], routed))
        if r.get("probability") is not None:
            auto_effective.append(r)
            if standalone is not None:
                repeat_pairs.append((routed, r["probability"], standalone))
        elif standalone is not None:
            imputed = dict(r)
            imputed["probability"] = standalone
            imputed["cost_usd"] = (r.get("cost_usd") or 0.0) + cost_of.get((r["qid"], routed), 0.0)
            auto_effective.append(imputed)

    def crowd_pairs(tier_rows: list[dict]) -> list[tuple[float, float]]:
        return [(float(r["crowd"]["value"]), float(r["probability"])) for r in tier_rows
                if r.get("crowd") and r["crowd"].get("value") is not None
                and r.get("probability") is not None]

    lines: list[str] = []
    versions = sorted({r.get("scaffold_version", "?") for r in latest.values()})
    models = sorted({m for r in latest.values() for m in [r.get("model") or ""] if m})
    providers = sorted({r.get("provider", "?") for r in latest.values()})
    lines += [f"# Benchmark report: {set_path.stem}", "",
              f"scaffold_version {', '.join(versions)} · model {', '.join(models)} "
              f"· provider {', '.join(providers)}", ""]

    lines += ["## vs crowd (teacher = market/community probability in the set)", "",
              HEADER, RULE]
    tier_rowsets = [(t, by_tier.get(t, [])) for t in ("low", "medium", "high")]
    tier_rowsets.append(("auto", auto_effective))
    for tier, tier_rows in tier_rowsets:
        pairs = crowd_pairs(tier_rows)
        if pairs:
            cost = sum(r.get("cost_usd") or 0.0 for r in tier_rows)
            lines.append(fmt_row(tier, gap_stats(pairs), cost))
    lines.append("")

    if high_by_qid:
        lines += ["## vs high tier (teacher = this scaffold's own best effort)", "",
                  HEADER, RULE]
        for tier, tier_rows in [(t, by_tier.get(t, [])) for t in ("low", "medium")] + [
                ("auto", auto_effective)]:
            pairs = [(high_by_qid[r["qid"]], float(r["probability"])) for r in tier_rows
                     if r["qid"] in high_by_qid and r.get("probability") is not None]
            if pairs:
                cost = sum(r.get("cost_usd") or 0.0 for r in tier_rows)
                lines.append(fmt_row(tier, gap_stats(pairs), cost))
        lines.append("")

    if by_tier.get("auto"):
        resolved = Counter(r.get("effort", "?") for r in by_tier["auto"])
        lines += ["## auto triage routing", ""]
        lines += [f"- {effort}: {count}" for effort, count in resolved.most_common()]
        lines.append("")

    if repeat_pairs:
        diffs = [abs(a - b) for _, a, b in repeat_pairs]
        lines += ["## repeatability (full auto run vs standalone run at the same tier)", "",
                  f"n={len(diffs)} · mean |Δp|={st.mean(diffs):.3f} · "
                  f"median={st.median(diffs):.3f} · max={max(diffs):.2f}",
                  "",
                  "This is the run-to-run noise floor: tier differences smaller than this "
                  "are not distinguishable from re-running the same tier.", ""]

    src_gaps: dict[str, list[float]] = defaultdict(list)
    stale: dict[str, int] = defaultdict(int)
    for r in latest.values():
        if r.get("probability") is None or not r.get("crowd"):
            continue
        src_gaps[r["source"]].append(abs(r["probability"] - r["crowd"]["value"]))
        if "freeze" in str(r["crowd"].get("source", "")):
            stale[r["source"]] += 1
    if src_gaps:
        lines += ["## by source (all tiers pooled)", "",
                  "| source | n | mean \\|Δp\\| | median \\|Δp\\| | crowd freshness |",
                  "|---|---|---|---|---|"]
        for s, g in sorted(src_gaps.items(), key=lambda kv: -st.mean(kv[1])):
            fresh = "freeze-time" if stale.get(s) else "live"
            lines.append(f"| {s} | {len(g)} | {st.mean(g):.3f} | {st.median(g):.3f} "
                         f"| {fresh} |")
        lines += ["",
                  "Freeze-time crowd values can be weeks old; a large gap on those rows "
                  "may be the crowd's staleness, not the forecast (verify before acting).",
                  ""]

    report = "\n".join(lines)
    out = RESULTS_DIR / f"{set_path.stem}.report.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
