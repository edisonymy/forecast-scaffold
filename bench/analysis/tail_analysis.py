"""Failure-mode analysis of existing loop-1 arms: catastrophic losses (Brier >= 0.49),
extreme-claim error rates (p<=0.10 or p>=0.90), and whether pooling prunes the tail."""
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
qtext = {q["id"]: q["question"][:70] for q in qrows}
probe = load(f"{ROOT}/bench/results/btf2-loop1-adm.probe.jsonl")
flagged = {r["qid"] for r in probe if "opus" in r.get("model", "") and cp.contaminated(r)}
flagged |= MEMORY_LEAK

arms = {}
for arm in ("base", "premortem", "skeptic"):
    rows = load(f"{ROOT}/bench/results/btf2-loop1-adm.{arm}.results.jsonl")
    arms[arm] = {r["qid"]: float(r["probability"]) for r in rows
                 if r.get("probability") is not None and r["qid"] not in flagged
                 and r["qid"] in res}


def gmo(ps):
    ps = [min(max(p, 1e-6), 1 - 1e-6) for p in ps]
    return 1 / (1 + math.exp(-st.mean(math.log(p / (1 - p)) for p in ps)))


# pools as pseudo-arms
common2 = set(arms["base"]) & set(arms["skeptic"])
arms["pool(b+s)"] = {q: gmo([arms["base"][q], arms["skeptic"][q]]) for q in common2}
# teacher as pseudo-arm on base's questions
arms["teacher"] = {q: teacher[q] for q in arms["base"] if q in teacher}


def tail_stats(probs, label):
    n = len(probs)
    cat = [(q, p, res[q]) for q, p in probs.items() if (p - res[q]) ** 2 >= 0.49]
    ext = [(q, p, res[q]) for q, p in probs.items() if p <= 0.10 or p >= 0.90]
    ext_wrong = [(q, p, y) for q, p, y in ext
                 if (p <= 0.10 and y == 1) or (p >= 0.90 and y == 0)]
    briers = sorted(((p - res[q]) ** 2 for q, p in probs.items()), reverse=True)
    worst5 = st.mean(briers[:5])
    print(f"{label:12s} n={n:3d}  catastrophes(B>=0.49): {len(cat):2d}  "
          f"extreme claims: {len(ext):3d}, wrong: {len(ext_wrong):2d} "
          f"({100 * len(ext_wrong) / max(len(ext), 1):.0f}%)  worst-5 mean B: {worst5:.3f}")
    return cat


print("=== tail / failure-mode profile (152 admissible) ===")
cats = {}
for label in ("base", "premortem", "skeptic", "pool(b+s)", "teacher"):
    cats[label] = tail_stats(arms[label], label)

print("\n=== base's catastrophes, and what each arm did on them ===")
for q, _p, y in sorted(cats["base"], key=lambda t: -(t[1] - t[2]) ** 2):
    parts = [f"{a}={arms[a][q]:.2f}" for a in ("base", "skeptic", "teacher") if q in arms[a]]
    print(f"  y={y:.0f}  {'  '.join(parts)}  {qtext.get(q, q)}")
