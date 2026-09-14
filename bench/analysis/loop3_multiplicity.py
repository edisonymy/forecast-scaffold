"""Loop-3 verdict: same-model multiplicity. Pools (geo-mean-odds) of plain resamples
(base, r2, r3) vs spine-diverse members (base, premortem, skeptic), each vs single-run
base, paired on the 152 admissible questions (opus flags + ECB leak excluded)."""
import json
import math
import statistics as st
import sys

import os as _os; sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from pathlib import Path as _P

import contamination_probe as cp  # noqa: E402

ROOT = str(_P(__file__).resolve().parents[2])


def load(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


qrows = load(f"{ROOT}/bench/sets/btf2-loop1.jsonl")
res = {q["id"]: float(q["resolution"]) for q in qrows if q.get("resolution") is not None}
teacher = {q["id"]: float(q["crowd"]["value"]) for q in qrows
           if isinstance(q.get("crowd"), dict) and q["crowd"].get("value") is not None}
probe = load(f"{ROOT}/bench/results/btf2-loop1-adm.probe.jsonl")
flagged = {r["qid"] for r in probe if "opus" in r.get("model", "") and cp.contaminated(r)}
flagged.add("btf2:516f111d-d70e-5198-95dc-5d38c0d9d789")  # ECB memory leak

arms = {}
for tag in ("base", "base-r2", "base-r3", "premortem", "skeptic"):
    rows = load(f"{ROOT}/bench/results/btf2-loop1-adm.{tag}.results.jsonl")
    arms[tag] = {r["qid"]: float(r["probability"]) for r in rows
                 if r.get("probability") is not None and r["qid"] not in flagged
                 and r["qid"] in res}
    print(f"{tag:10s} {len(arms[tag])} rows")


def gmo(ps):
    ps = [min(max(p, 1e-6), 1 - 1e-6) for p in ps]
    return 1 / (1 + math.exp(-st.mean(math.log(p / (1 - p)) for p in ps)))


def brier(p, y):
    return (p - y) ** 2


def murphy_rel_res(pairs):
    n = len(pairs)
    ybar = st.mean(y for _, y in pairs)
    bins: dict[int, list] = {}
    for p, y in pairs:
        bins.setdefault(min(int(p * 10), 9), []).append((p, y))
    rel = sum(len(b) * (st.mean(p for p, _ in b) - st.mean(y for _, y in b)) ** 2
              for b in bins.values()) / n
    resol = sum(len(b) * (st.mean(y for _, y in b) - ybar) ** 2 for b in bins.values()) / n
    return rel, resol


common = sorted(set.intersection(*(set(v) for v in arms.values())))
print(f"\ncommon admissible questions: {len(common)}")

pools = {
    "single (base)": {q: arms["base"][q] for q in common},
    "resample x3": {q: gmo([arms["base"][q], arms["base-r2"][q], arms["base-r3"][q]])
                    for q in common},
    "spines x3": {q: gmo([arms["base"][q], arms["premortem"][q], arms["skeptic"][q]])
                  for q in common},
    "all five": {q: gmo([arms[t][q] for t in
                         ("base", "base-r2", "base-r3", "premortem", "skeptic")])
                 for q in common},
    "teacher": {q: teacher[q] for q in common if q in teacher},
}

# member-average references (the exchangeability-fair baseline for each pool)
mem_resample = st.mean(st.mean(brier(arms[t][q], res[q]) for q in common)
                       for t in ("base", "base-r2", "base-r3"))
mem_spines = st.mean(st.mean(brier(arms[t][q], res[q]) for q in common)
                     for t in ("base", "premortem", "skeptic"))
print(f"\nmember-average Brier: resamples {mem_resample:.4f} | spines {mem_spines:.4f}")

print(f"\n{'pool':16s} {'Brier':>7s} {'REL':>7s} {'RES':>7s}  paired vs base ±se (wins/losses)")
base_probs = pools["single (base)"]
for name, probs in pools.items():
    qids = sorted(set(probs) & set(res))
    bs = st.mean(brier(probs[q], res[q]) for q in qids)
    rel, resol = murphy_rel_res([(probs[q], res[q]) for q in qids])
    if name == "single (base)":
        print(f"{name:16s} {bs:7.4f} {rel:7.4f} {resol:7.4f}  —")
        continue
    ds = [brier(probs[q], res[q]) - brier(base_probs[q], res[q]) for q in qids
          if q in base_probs]
    se = st.stdev(ds) / math.sqrt(len(ds)) if len(ds) > 1 else 0.0
    wins = sum(1 for d in ds if d < 0)
    losses = sum(1 for d in ds if d > 0)
    print(f"{name:16s} {bs:7.4f} {rel:7.4f} {resol:7.4f}  {st.mean(ds):+.4f} ±{se:.4f} "
          f"({wins}/{losses})")

# disagreement diagnostics: do spines generate the diversity resampling lacks?
print("\nmember disagreement (mean |dp| across pairs):")
import itertools

for a, b in itertools.combinations(("base", "base-r2", "base-r3"), 2):
    g = [abs(arms[a][q] - arms[b][q]) for q in common]
    print(f"  {a} vs {b}: {st.mean(g):.3f}")
for a, b in itertools.combinations(("base", "premortem", "skeptic"), 2):
    g = [abs(arms[a][q] - arms[b][q]) for q in common]
    print(f"  {a} vs {b}: {st.mean(g):.3f}")
