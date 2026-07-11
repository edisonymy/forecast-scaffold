import io
import json
import math
import statistics as st
import sys

import os as _os; sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import contamination_probe as cp  # noqa: E402

from pathlib import Path as _P
ROOT = str(_P(__file__).resolve().parents[2])


def load(path):
    return [json.loads(line) for line in io.open(path, encoding="utf-8") if line.strip()]


res, teacher = {}, {}
for path in (f"{ROOT}/bench/sets/2026-07-05-btf2.jsonl", f"{ROOT}/bench/sets/lf-audit-15.jsonl"):
    for q in load(path):
        if q.get("resolution") is not None:
            res[q["id"]] = float(q["resolution"])
        crowd = q.get("crowd")
        if isinstance(crowd, dict) and crowd.get("value") is not None:
            teacher[q["id"]] = float(crowd["value"])
        elif isinstance(crowd, int | float):
            teacher[q["id"]] = float(crowd)

clean = load(f"{ROOT}/bench/results/lf-audit-15.lf-opus46.results.jsonl")
orig = load(f"{ROOT}/bench/results/2026-07-05-btf2.opus46.results.jsonl")
audit_qids = {q["id"] for q in load(f"{ROOT}/bench/sets/lf-audit-15.jsonl")}
probe = load(f"{ROOT}/bench/results/2026-07-05-btf2.probe.jsonl")
union_flagged = {r["qid"] for r in probe if cp.contaminated(r)}
probed = {r["qid"] for r in probe}
admissible = probed - union_flagged


def probs_of(rows, tier, qids):
    return {r["qid"]: float(r["probability"]) for r in rows
            if r["tier"] == tier and r["qid"] in qids and r["qid"] in res}


def brier(p, y):
    return (p - y) ** 2


def murphy(probs):
    """Murphy decomposition, 10 equal bins. Returns (brier, reliability, resolution, uncertainty)."""
    pairs = [(p, res[q]) for q, p in probs.items()]
    n = len(pairs)
    ybar = st.mean(y for _, y in pairs)
    unc = ybar * (1 - ybar)
    bins = {}
    for p, y in pairs:
        k = min(int(p * 10), 9)
        bins.setdefault(k, []).append((p, y))
    rel = sum(len(b) * (st.mean(p for p, _ in b) - st.mean(y for _, y in b)) ** 2
              for b in bins.values()) / n
    resol = sum(len(b) * (st.mean(y for _, y in b) - ybar) ** 2 for b in bins.values()) / n
    bs = st.mean(brier(p, y) for p, y in pairs)
    return bs, rel, resol, unc


def vs_teacher(probs, label):
    qids = sorted(set(probs) & set(teacher))
    ds = [brier(probs[q], res[q]) - brier(teacher[q], res[q]) for q in qids]
    n = len(ds)
    se = st.stdev(ds) / math.sqrt(n) if n > 1 else 0.0
    wins = sum(1 for d in ds if d < 0)
    bs, rel, resol, unc = murphy(probs)
    print(f"  {label:28s} Brier {bs:.4f}  cal(REL) {rel:.4f}  refine(RES) {resol:.4f}  "
          f"unc {unc:.4f}  | vs teacher {st.mean(ds):+.4f} ±{se:.4f} (n={n}, wins {wins}/{n})")


for name, rows, qids in [
    ("CLEAN RUN — 15 admissible, timevault", clean, audit_qids),
    ("ORIGINAL — 47 admissible, native", orig, admissible),
    ("ORIGINAL — all 85, native", orig, {r["qid"] for r in orig}),
]:
    print(f"\n{name}")
    for tier in ("high", "zero"):
        p = probs_of(rows, tier, qids)
        if p:
            vs_teacher(p, tier)
    tq = {q: teacher[q] for q in teacher if q in qids and q in res}
    bs, rel, resol, unc = murphy(tq)
    print(f"  {'teacher (FutureSearch)':28s} Brier {bs:.4f}  cal(REL) {rel:.4f}  refine(RES) {resol:.4f}  unc {unc:.4f}")

# ---- power analysis ----
print("\nPOWER (paired two-sided alpha=.05, power=.80: MDE = 2.802*sd_d/sqrt(n))")
for label, rows, qids in [
    ("clean n=15 (high-zero)", clean, audit_qids),
    ("native n=47 (high-zero)", orig, admissible),
]:
    h, z = probs_of(rows, "high", qids), probs_of(rows, "zero", qids)
    common = sorted(set(h) & set(z))
    ds = [brier(h[q], res[q]) - brier(z[q], res[q]) for q in common]
    sd = st.stdev(ds)
    n = len(ds)
    mde = 2.802 * sd / math.sqrt(n)
    print(f"  {label:26s} sd_d={sd:.4f}  MDE={mde:.4f}")
    for eff in (0.012, 0.02, 0.03):
        need = math.ceil((2.802 * sd / eff) ** 2)
        print(f"      to detect {eff:.3f}: need n≈{need}")

# teacher-vs-tier power too (different sd)
h = probs_of(clean, "high", audit_qids)
ds = [brier(h[q], res[q]) - brier(teacher[q], res[q]) for q in sorted(set(h) & set(teacher))]
print(f"  clean high-vs-teacher      sd_d={st.stdev(ds):.4f}  "
      f"MDE={2.802 * st.stdev(ds) / math.sqrt(len(ds)):.4f}")

# how many more questions COULD exist
try:
    raw = json.load(io.open(f"{ROOT}/bench/sets/btf2_raw.json", encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("questions", raw.get("data", []))
    print(f"\nbtf2_raw.json: {len(items)} entries total")
except Exception as e:
    print("btf2_raw:", e)
