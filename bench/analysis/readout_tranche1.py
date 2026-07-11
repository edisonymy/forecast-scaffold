"""Pre-registered readout of the 3-arm research A/B ("tranche1"): plain ReAct vs
our-method (high) vs angle-diverse (angles), opus-4-6, corpus+vault research,
bench/sets/btf2-loop1-adm.jsonl.

PRE-REGISTERED RULES (docs/roadmap-v05.md, set BEFORE looking at results):
- Primary statistic: paired bootstrap CI on per-question Brier deltas (tail-sensitive);
  win-rate is secondary. n=40 resolves only effects >= ~0.015 — do NOT adjudicate
  0.005-sized differences from this tranche.
- Promote plain or angles over high if paired delta <= -0.008 with 90% bootstrap CI
  excluding 0; if |delta| < 0.005 treat as unresolved and run the substrate recall audit
  before any further research-mechanics interpretation.
- Run bench/analysis/memory_screen.py FIRST over the tranche results file and exclude
  any confirmed memory-claim rows from all arms pairwise.
"""
import json
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
import contamination_probe as cp  # noqa: E402

MEMORY_LEAK = {"btf2:516f111d-d70e-5198-95dc-5d38c0d9d789"}  # + any memory_screen finds
ARMS = ("plain", "high", "angles")


def load(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


qrows = load(ROOT / "bench/sets/btf2-loop1.jsonl")
res = {q["id"]: float(q["resolution"]) for q in qrows if q.get("resolution") is not None}
teacher = {q["id"]: float(q["crowd"]["value"]) for q in qrows
           if isinstance(q.get("crowd"), dict) and q["crowd"].get("value") is not None}
probe = load(ROOT / "bench/results/btf2-loop1-adm.probe.jsonl")
flagged = {r["qid"] for r in probe if "opus" in r.get("model", "") and cp.contaminated(r)}
flagged |= MEMORY_LEAK

rows = load(ROOT / "bench/results/btf2-loop1-adm.tranche1.results.jsonl")
arm = {a: {} for a in ARMS}
cost = {a: 0.0 for a in ARMS}
for r in rows:
    a, q = r.get("tier"), r.get("qid")
    if a in arm and r.get("probability") is not None and q not in flagged and q in res:
        arm[a][r["qid"]] = float(r["probability"])
        cost[a] += r.get("cost_usd") or 0
for a in ARMS:
    print(f"{a:7s} {len(arm[a])} scorable rows, ${cost[a]:.2f}")


def brier(p, y):
    return (p - y) ** 2


def boot_ci(ds, iters=10000, lo=5, hi=95):
    rnd = random.Random(7)
    n = len(ds)
    means = sorted(st.mean(rnd.choices(ds, k=n)) for _ in range(iters))
    return means[int(iters * lo / 100)], means[int(iters * hi / 100)]


def murphy(probs):
    pairs = [(p, res[q]) for q, p in probs.items()]
    n = len(pairs)
    ybar = st.mean(y for _, y in pairs)
    bins = {}
    for p, y in pairs:
        bins.setdefault(min(int(p * 10), 9), []).append((p, y))
    rel = sum(len(b) * (st.mean(p for p, _ in b) - st.mean(y for _, y in b)) ** 2
              for b in bins.values()) / n
    resol = sum(len(b) * (st.mean(y for _, y in b) - ybar) ** 2 for b in bins.values()) / n
    return rel, resol


print(f"\n{'arm':7s} {'n':>3s} {'Brier':>7s} {'REL':>7s} {'RES':>7s}  vs teacher")
for a in ARMS:
    qs = sorted(set(arm[a]) & set(teacher))
    if not qs:
        continue
    bs = st.mean(brier(arm[a][q], res[q]) for q in arm[a])
    rel, resol = murphy(arm[a])
    dt = st.mean(brier(arm[a][q], res[q]) - brier(teacher[q], res[q]) for q in qs)
    print(f"{a:7s} {len(arm[a]):3d} {bs:7.4f} {rel:7.4f} {resol:7.4f}  {dt:+.4f}")

print("\nPAIRED (negative = first arm better; bootstrap 90% CI primary)")
import itertools

for a, b in itertools.combinations(ARMS, 2):
    common = sorted(set(arm[a]) & set(arm[b]))
    if len(common) < 5:
        print(f"{a} vs {b}: insufficient overlap ({len(common)})")
        continue
    ds = [brier(arm[a][q], res[q]) - brier(arm[b][q], res[q]) for q in common]
    lo, hi = boot_ci(ds)
    wins = sum(1 for d in ds if d < 0)
    print(f"{a:7s} vs {b:7s}: mean {st.mean(ds):+.4f}  CI90 [{lo:+.4f},{hi:+.4f}]  "
          f"(n={len(common)}, {a} wins {wins}/{len(ds)})")
print("\nReminder: run memory_screen.py on the tranche file; exclude hits and re-run.")
