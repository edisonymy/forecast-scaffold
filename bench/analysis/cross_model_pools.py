"""Loop-2 analysis: cross-model ensembles on frozen dossiers. Each pool is scored only on
questions admissible for EVERY member (per-model probe flags + the opus ECB memory leak),
paired against opus-base on the same questions."""
import json
import math
import statistics as st
import sys

import os as _os; sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from pathlib import Path as _P

import contamination_probe as cp  # noqa: E402

ROOT = str(_P(__file__).resolve().parents[2])


def load(path):
    try:
        return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    except FileNotFoundError:
        return []


qrows = load(f"{ROOT}/bench/sets/btf2-loop1.jsonl")
res = {q["id"]: float(q["resolution"]) for q in qrows if q.get("resolution") is not None}
teacher = {q["id"]: float(q["crowd"]["value"]) for q in qrows
           if isinstance(q.get("crowd"), dict) and q["crowd"].get("value") is not None}

probe = load(f"{ROOT}/bench/results/btf2-loop1-adm.probe.jsonl")
probe += load(f"{ROOT}/bench/results/btf2-loop1.probe.jsonl")
FLAGS = {"opus": {"btf2:516f111d-d70e-5198-95dc-5d38c0d9d789"},  # ECB memory leak
         "haiku": set(), "gemini": set()}
for r in probe:
    m = r.get("model", "")
    key = "opus" if "opus" in m else "haiku" if "haiku" in m else "gemini" if "gemini" in m else None
    if key and cp.contaminated(r):
        FLAGS[key].add(r["qid"])

ARMS = {"opus": "base", "haiku": "base-haiku", "gemini": "base-gemini"}
probs: dict[str, dict[str, float]] = {}
for name, tag in ARMS.items():
    rows = load(f"{ROOT}/bench/results/btf2-loop1-adm.{tag}.results.jsonl")
    probs[name] = {r["qid"]: float(r["probability"]) for r in rows
                   if r.get("probability") is not None}
    cost = sum(r.get("cost_usd") or 0 for r in rows)
    print(f"{name:8s} {len(probs[name])} rows, {len(FLAGS[name])} flagged qids, ${cost:.2f}")


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


def admissible_for(members):
    qids = set(res)
    for m in members:
        qids &= set(probs[m]) - FLAGS[m]
    return qids


def report(members, name):
    qids = sorted(admissible_for(members) & set(probs["opus"]) - FLAGS["opus"])
    if not qids:
        print(f"{name}: no scorable questions yet")
        return
    pool = {q: gmo([probs[m][q] for m in members]) for q in qids}
    bs = st.mean(brier(pool[q], res[q]) for q in qids)
    rel, resol = murphy_rel_res([(pool[q], res[q]) for q in qids])
    opus_bs = st.mean(brier(probs["opus"][q], res[q]) for q in qids)
    ds = [brier(pool[q], res[q]) - brier(probs["opus"][q], res[q]) for q in qids]
    se = st.stdev(ds) / math.sqrt(len(ds)) if len(ds) > 1 else 0.0
    wins = sum(1 for d in ds if d < 0)
    losses = sum(1 for d in ds if d > 0)
    dt = [brier(pool[q], res[q]) - brier(teacher[q], res[q]) for q in qids if q in teacher]
    set_ = st.stdev(dt) / math.sqrt(len(dt)) if len(dt) > 1 else 0.0
    tb = st.mean(brier(teacher[q], res[q]) for q in qids if q in teacher)
    twins = sum(1 for d in dt if d < 0)
    print(f"\n{name}: n={len(qids)}  Brier {bs:.4f}  REL {rel:.4f}  RES {resol:.4f}")
    print(f"   opus alone on same qids: {opus_bs:.4f} | teacher: {tb:.4f}")
    print(f"   vs opus:    {st.mean(ds):+.4f} ±{se:.4f}  (wins {wins}, losses {losses})")
    print(f"   vs teacher: {st.mean(dt):+.4f} ±{set_:.4f}  (pool wins {twins}/{len(dt)})")


print("\n=== solo levels (each on its own admissible set ∩ opus-admissible) ===")
for m in ("opus", "haiku", "gemini"):
    qids = sorted(admissible_for([m]) & (set(probs["opus"]) - FLAGS["opus"]))
    if not qids:
        continue
    bs = st.mean(brier(probs[m][q], res[q]) for q in qids)
    dt = [abs(probs[m][q] - teacher[q]) for q in qids if q in teacher]
    print(f"{m:8s} n={len(qids):3d}  Brier {bs:.4f}  mean|p-teacher| {st.mean(dt):.3f}")

report(["opus", "haiku"], "opus+haiku (1 family)")
report(["opus", "gemini"], "opus+gemini (2 families)")
report(["haiku", "gemini"], "haiku+gemini (no opus)")
report(["opus", "haiku", "gemini"], "opus+haiku+gemini")

# error decorrelation: correlation of per-question Brier between members
print("\n=== member error correlation (Pearson r of per-question Brier) ===")
import itertools

for a, b in itertools.combinations(("opus", "haiku", "gemini"), 2):
    qids = sorted(admissible_for([a, b]))
    if len(qids) < 10:
        continue
    xa = [brier(probs[a][q], res[q]) for q in qids]
    xb = [brier(probs[b][q], res[q]) for q in qids]
    r = st.correlation(xa, xb)
    print(f"{a}-{b}: r={r:.3f} (n={len(qids)})")
