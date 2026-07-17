"""Paired readout: research.md v2 arm vs tranche1's high arm (current research.md).

PREREGISTERED 2026-07-17 BEFORE THE V2 ARM PRODUCED ANY ROW (registered in chat and in
the ab/research-v2 branch commit af5d343; arms share question set, corpus, model
opus-4-6, tier high; only skills/forecast/references/research.md differs).

RULES:
- Cells: run==0 only, --dedupe first (same policy as the tranche readout), the known
  memory-leak qid excluded pairwise, common-question intersection.
- TARGET metric: Murphy resolution (RES) — v2 exists to buy refinement. Report REL too.
- GUARD metric: paired per-question Brier delta (v2 - current), 10k bootstrap CI90
  seed 7 (negative = v2 better).
- DECISION: recommend shipping v2 only if RES(v2) > RES(current) AND the Brier CI90
  upper bound < +0.008. CI90 straddling zero with RES improved -> "promising, collect
  the next wave"; do not ship. n~40 resolves only large effects — say so.
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from readout_tranche1 import (  # noqa: E402
    MEMORY_LEAK,
    boot_ci,
    brier,
    collect_run_zero,
    load,
    murphy,
)

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "bench/results/btf2-loop1-adm.tranche1.results.jsonl"
V2 = Path("C:/Users/Edison Yi/Documents/code/forecast-scaffold-ab-research-v2/"
          "bench/results/btf2-loop1-adm.ab-research-v2.results.jsonl")


def resolutions_from_set() -> dict[str, float]:
    import json
    out: dict[str, float] = {}
    with (ROOT / "bench/sets/btf2-loop1-adm.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                spec = json.loads(line)
                res = spec.get("resolution")
                if res is not None:
                    out[spec["id"]] = float(res)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, default=CURRENT)
    parser.add_argument("--v2", type=Path, default=V2)
    args = parser.parse_args(argv)

    resolutions = resolutions_from_set()
    cur_arm, cur_cost, _ = collect_run_zero(load(args.current), resolutions,
                                            MEMORY_LEAK, dedupe="first")
    v2_arm, v2_cost, _ = collect_run_zero(load(args.v2), resolutions,
                                          MEMORY_LEAK, dedupe="first")
    current = cur_arm["high"]
    v2 = v2_arm["high"]
    common = sorted(set(current) & set(v2))
    print(f"current(high): {len(current)} cells ${cur_cost['high']:.2f} | "
          f"v2: {len(v2)} cells ${v2_cost['high']:.2f} | common n={len(common)}")
    if len(common) < 10:
        print("insufficient overlap; wait for more v2 rows")
        return 1

    cur_common = {q: current[q] for q in common}
    v2_common = {q: v2[q] for q in common}
    for name, arm in (("current", cur_common), ("v2", v2_common)):
        bs = st.mean(brier(arm[q], resolutions[q]) for q in common)
        rel, res = murphy(arm, resolutions)
        print(f"{name:8s} Brier {bs:.4f}  REL {rel:.4f}  RES {res:.4f}")

    deltas = [brier(v2_common[q], resolutions[q]) - brier(cur_common[q], resolutions[q])
              for q in common]
    lo, hi = boot_ci(deltas)
    wins = sum(1 for d in deltas if d < 0)
    print(f"\nPAIRED v2 - current: mean {st.mean(deltas):+.4f}  CI90 [{lo:+.4f},{hi:+.4f}]  "
          f"(v2 wins {wins}/{len(deltas)}; negative = v2 better)")

    _, res_cur = murphy(cur_common, resolutions)
    _, res_v2 = murphy(v2_common, resolutions)
    res_gain = res_v2 - res_cur
    ship = res_gain > 0 and hi < 0.008
    promising = res_gain > 0 and not ship
    print(f"\nRES delta (target): {res_gain:+.4f}")
    if ship:
        print("DECISION RULE: SHIP — RES improved and Brier guard holds (CI90 upper "
              f"{hi:+.4f} < +0.008).")
    elif promising:
        print("DECISION RULE: PROMISING, DO NOT SHIP YET — RES improved but Brier guard "
              f"not clean (CI90 upper {hi:+.4f}); collect the next wave.")
    else:
        print("DECISION RULE: DO NOT SHIP — RES did not improve.")
    print("Caveat: n~40 resolves only large effects; arms were produced days apart "
          "across scaffold v0.4.18-0.4.22 (current) vs v0.4.22-only (v2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
