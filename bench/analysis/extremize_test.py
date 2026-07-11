"""Free post-processing test: does logit-extremization p' = sigma(d * logit(p)) close the
refinement gap? Honest split: fit d on the 47 tranche-1 qids, evaluate the chosen d on the
105 fresh qids only. Run for base and skeptic arms; teacher for reference."""
import json
import math
import statistics as st
import sys

import os as _os; sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from pathlib import Path as _P

import contamination_probe as cp  # noqa: E402

ROOT = str(_P(__file__).resolve().parents[2])
MEMORY_LEAK = {"btf2:516f111d-d70e-5198-95dc-5d38c0d9d789"}


def load(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


qrows = load(f"{ROOT}/bench/sets/btf2-loop1.jsonl")
res = {q["id"]: float(q["resolution"]) for q in qrows if q.get("resolution") is not None}
teacher = {q["id"]: float(q["crowd"]["value"]) for q in qrows
           if isinstance(q.get("crowd"), dict) and q["crowd"].get("value") is not None}
probe = load(f"{ROOT}/bench/results/btf2-loop1-adm.probe.jsonl")
flagged = {r["qid"] for r in probe if "opus" in r.get("model", "") and cp.contaminated(r)}
flagged |= MEMORY_LEAK

# tranche-1 qids = first 47 of the loop set (the previously-admissible block)
tranche1 = {q["id"] for q in qrows[:47]}


def extremize(p, d):
    p = min(max(p, 1e-6), 1 - 1e-6)
    lo = math.log(p / (1 - p)) * d
    return 1 / (1 + math.exp(-lo))


def brier_mean(probs, qids, d=1.0):
    return st.mean((extremize(probs[q], d) - res[q]) ** 2 for q in qids)


for arm in ("base", "skeptic"):
    rows = load(f"{ROOT}/bench/results/btf2-loop1-adm.{arm}.results.jsonl")
    probs = {r["qid"]: float(r["probability"]) for r in rows
             if r.get("probability") is not None and r["qid"] not in flagged}
    train = sorted(set(probs) & tranche1 & set(res))
    test = sorted((set(probs) & set(res)) - tranche1)
    grid = [round(1.0 + 0.1 * i, 1) for i in range(0, 16)]
    scores = [(brier_mean(probs, train, d), d) for d in grid]
    best_train, best_d = min(scores)
    # test-set evaluation of the TRAIN-chosen d only (plus d=1 reference)
    b1 = brier_mean(probs, test, 1.0)
    bd = brier_mean(probs, test, best_d)
    # paired diff on test
    ds = [(extremize(probs[q], best_d) - res[q]) ** 2 - (probs[q] - res[q]) ** 2 for q in test]
    se = st.stdev(ds) / math.sqrt(len(ds)) if len(ds) > 1 else 0.0
    wins = sum(1 for x in ds if x < 0)
    print(f"\n{arm}: best d on train(n={len(train)}) = {best_d} "
          f"(train Brier {brier_mean(probs, train, 1.0):.4f} -> {best_train:.4f})")
    print(f"  TEST (n={len(test)}): d=1.0 Brier {b1:.4f} -> d={best_d} Brier {bd:.4f}  "
          f"paired diff {st.mean(ds):+.4f} +/-{se:.4f} (wins {wins}/{len(ds)})")
    tb = st.mean((teacher[q] - res[q]) ** 2 for q in test if q in teacher)
    print(f"  teacher on same test qids: {tb:.4f}")
    # sensitivity: show the test curve so we can see if train's d was near test's optimum
    curve = [(d, round(brier_mean(probs, test, d), 4)) for d in (1.0, 1.2, 1.4, 1.6, 1.8, 2.0)]
    print(f"  test curve (post-hoc, for context only): {curve}")
