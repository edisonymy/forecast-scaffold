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
# The tiers this report's tables know how to place; anything else (experiment arms like
# plain/angles) is silently absent from them, which the NOTE banner below flags.
RECOGNIZED_TIERS = {"zero", "low", "medium", "high", "auto"}


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
    parser.add_argument("--tag", default="", help="read the matching tagged results file")
    args = parser.parse_args(argv)

    set_path = Path(args.set_file)
    suffix = f".{args.tag}" if args.tag else ""
    results_path = RESULTS_DIR / f"{set_path.stem}{suffix}.results.jsonl"
    if not results_path.exists():
        print(f"no results at {results_path}; run bench/run_bench.py first")
        return 1
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    # Latest row wins per (qid, tier, run): reruns supersede earlier attempts.
    latest: dict[tuple[str, str, int], dict] = {}
    for row in rows:
        latest[(row["qid"], row["tier"], int(row.get("run") or 0))] = row

    # Pool independent runs per (question, tier): geometric mean of odds, dropping the
    # most extreme run on each end when n >= 4 (Samotsvety's rule for independent
    # forecasters). Cost is the SUM of runs — that's what the tier actually costs.
    def pool_runs(ps: list[float]) -> float:
        if len(ps) == 1:
            return ps[0]
        ps = sorted(_clamp(p) for p in ps)
        if len(ps) >= 4:
            ps = ps[1:-1]
        mean_lo = sum(logit(p) for p in ps) / len(ps)
        return 1.0 / (1.0 + math.exp(-mean_lo))

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in latest.values():
        grouped[(r["qid"], r["tier"])].append(r)
    by_tier: dict[str, list[dict]] = defaultdict(list)
    spread_by_tier: dict[str, list[float]] = defaultdict(list)
    for (_qid, tier), runs in grouped.items():
        forecasts = [r for r in runs if r.get("probability") is not None]
        base = dict(runs[0])
        base["cost_usd"] = sum(r.get("cost_usd") or 0.0 for r in runs)
        if forecasts:
            ps = [float(r["probability"]) for r in forecasts]
            base["probability"] = pool_runs(ps)
            base["router_only"] = False
            base["n_runs"] = len(ps)
            if len(ps) > 1:
                spread_by_tier[tier].append(max(ps) - min(ps))
        by_tier[tier].append(base)
    prob_of = {(r["qid"], r["tier"]): r["probability"] for rs in by_tier.values()
               for r in rs if r.get("probability") is not None}
    cost_of = {(r["qid"], r["tier"]): r.get("cost_usd") or 0.0 for rs in by_tier.values()
               for r in rs}
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

    # This script silently pools every run and its tables drop unknown tiers. Against an
    # experiment file (tranche arms like plain/angles, or multi-run data) that yields a
    # mislabeled, non-preregistered number — say so up front. Silent for ordinary files.
    odd_tiers = sum(1 for r in rows if r.get("tier") not in RECOGNIZED_TIERS)
    nonzero_runs = sum(1 for r in rows if int(r.get("run") or 0) != 0)
    if odd_tiers or nonzero_runs:
        lines += [f"NOTE: {odd_tiers} rows in unrecognized tiers / {nonzero_runs} "
                  "nonzero runs pooled — general report, NOT the preregistered tranche "
                  "readout (see bench/analysis/readout_tranche1.py)", ""]

    # Resolution scoring — only possible when the set carries known outcomes (pastcasting
    # sets like btf2). Brier against reality outranks every distance-to-teacher proxy.
    specs = [json.loads(line) for line in set_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    outcome_of = {s["id"]: int(s["resolution"]) for s in specs
                  if s.get("resolution") is not None}
    if outcome_of:
        lines += ["## resolution scoring (real outcomes — the honest metric)", "",
                  "| tier | n | Brier | teacher Brier | Δ(tier−teacher) ±1.96se "
                  "| base-rate Brier |",
                  "|---|---|---|---|---|---|"]
        for tier, tier_rows in [(t, by_tier.get(t, [])) for t in
                                ("zero", "low", "medium", "high")] + [("auto", auto_effective)]:
            scored = [r for r in tier_rows if r.get("probability") is not None
                      and r["qid"] in outcome_of]
            if not scored:
                continue
            briers = [(float(r["probability"]) - outcome_of[r["qid"]]) ** 2 for r in scored]
            ys = [outcome_of[r["qid"]] for r in scored]
            base = sum(ys) / len(ys)
            base_brier = sum((base - y) ** 2 for y in ys) / len(ys)
            paired = [((float(r["probability"]) - outcome_of[r["qid"]]) ** 2)
                      - ((float(r["crowd"]["value"]) - outcome_of[r["qid"]]) ** 2)
                      for r in scored
                      if r.get("crowd") and r["crowd"].get("value") is not None]
            if paired:
                mean_d = st.mean(paired)
                ci = 1.96 * st.stdev(paired) / math.sqrt(len(paired)) if len(paired) > 1 else 0.0
                teacher = st.mean([(float(r["crowd"]["value"]) - outcome_of[r["qid"]]) ** 2
                                   for r in scored
                                   if r.get("crowd") and r["crowd"].get("value") is not None])
                delta = f"{mean_d:+.4f} ±{ci:.4f}"
                teacher_s = f"{teacher:.4f}"
            else:
                delta, teacher_s = "—", "—"
            lines.append(f"| {tier} | {len(scored)} | {st.mean(briers):.4f} | {teacher_s} "
                         f"| {delta} | {base_brier:.4f} |")
        lines += ["", "Negative Δ = this tier beats the teacher (for btf2 sets the teacher "
                  "is FutureSearch's proprietary SOTA agent. Per the dataset card its "
                  "forecast was made from their full frozen scraped corpus — NOT from the "
                  "research_summary digest our briefs carry, which came from a separate "
                  "open-web search. Teacher comparisons therefore confound evidence access "
                  "with reasoning: an upper bound to chase, not a controlled cell).", ""]

        # Paired per-question Brier diff between every pair of tiers — the sharpest test
        # of "does tier A actually beat tier B", since it cancels question-to-question
        # difficulty instead of comparing two unpaired tier averages. Reuses the same
        # per-tier effective rows as the table above (by_tier is already pooled across
        # runs per (qid, tier) via geometric-mean-of-odds; auto uses the router-imputed
        # auto_effective rows) — a duplicate (qid, tier) can't reach this section.
        resolved_by_tier: dict[str, dict[str, float]] = {}
        for tier, tier_rows in [(t, by_tier.get(t, [])) for t in
                                ("zero", "low", "medium", "high")] + [("auto", auto_effective)]:
            probs = {r["qid"]: float(r["probability"]) for r in tier_rows
                     if r.get("probability") is not None and r["qid"] in outcome_of}
            if probs:
                resolved_by_tier[tier] = probs

        PAIR_ORDER = ("high", "medium", "low", "zero", "auto")
        present = [t for t in PAIR_ORDER if t in resolved_by_tier]
        pair_lines: list[str] = []
        for i, tier_a in enumerate(present):
            for tier_b in present[i + 1:]:
                qids = sorted(set(resolved_by_tier[tier_a]) & set(resolved_by_tier[tier_b]))
                if not qids:
                    continue  # no overlap between these two tiers; skip silently
                diffs: list[float] = []
                wins_a = wins_b = ties = 0
                for qid in qids:
                    y = outcome_of[qid]
                    brier_a = (resolved_by_tier[tier_a][qid] - y) ** 2
                    brier_b = (resolved_by_tier[tier_b][qid] - y) ** 2
                    d = brier_a - brier_b
                    diffs.append(d)
                    if d < 0:
                        wins_a += 1
                    elif d > 0:
                        wins_b += 1
                    else:
                        ties += 1
                n = len(diffs)
                mean_d = st.mean(diffs)
                se = st.stdev(diffs) / math.sqrt(n) if n > 1 else 0.0
                pair_lines.append(
                    f"- {tier_a} - {tier_b}: mean {mean_d:+.4f} ±{se:.4f} "
                    f"(n={n}, {tier_a} wins {wins_a}/{n}, {tier_b} wins {wins_b}/{n}, "
                    f"ties {ties})"
                )
        if pair_lines:
            lines += ["## paired Brier comparison (per question, resolved outcomes only)", "",
                      "Per shared question: d = Brier(tierA) − Brier(tierB); negative means "
                      "tierA wins that question. Paired over the qids where BOTH tiers have "
                      "a forecast and a known resolution — this cancels question difficulty, "
                      "which the unpaired tier averages above cannot.", ""]
            lines += pair_lines
            lines.append("")

    lines += ["## vs crowd (teacher = market/community probability in the set)", "",
              HEADER, RULE]
    tier_rowsets = [(t, by_tier.get(t, [])) for t in ("zero", "low", "medium", "high")]
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
        for tier, tier_rows in [(t, by_tier.get(t, [])) for t in ("zero", "low", "medium")] + [
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

    if spread_by_tier:
        lines += ["## within-tier run spread (independent runs on the same question)", ""]
        for tier in ("low", "medium", "high"):
            sp = spread_by_tier.get(tier)
            if sp:
                lines.append(f"- {tier}: mean range {st.mean(sp):.3f} · max {max(sp):.2f} "
                             f"(n={len(sp)} questions)")
        lines += ["", "Pooling clips this: the reported tier numbers use the pooled value.", ""]

    src_gaps: dict[str, list[float]] = defaultdict(list)
    stale: dict[str, int] = defaultdict(int)
    for rs in by_tier.values():
        for r in rs:
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
    out = RESULTS_DIR / f"{set_path.stem}{suffix}.report.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
