"""Paired comparison of the old (dossier + lenses) and new (parallel research) arms.

Reads the arm journals in this directory, the wave-4 community scrape (bots-only aggregate =
the field we are scored against), and, when present, the resolutions overlay for outcomes.
"""
from __future__ import annotations

import json
import math
import re
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(r"C:\Users\Edison Yi\Documents\code\forecast-scaffold")
SCRAPE = ROOT / "bench" / "analysis" / "minibench-2026-08-24-community-scrape-2026-09-03.txt"
OVERLAY = ROOT / "bot" / "journal" / "resolutions.jsonl"
sys.path.insert(0, str(ROOT / "bot"))
import priors  # noqa: E402


def num(s: str):
    s = s.strip().replace("<", "").replace(">", "").replace(",", "")
    m = re.match(r"-?[\d.]+", s)
    if not m:
        return None
    v = float(m.group())
    rest = s[m.end():m.end() + 2]
    if rest.startswith("k"):
        v *= 1000
    if rest.startswith("M"):
        v *= 1e6
    return v


def load_crowd() -> dict[frozenset, dict]:
    out = {}
    for line in SCRAPE.read_text(encoding="utf-8").splitlines():
        f = line.split("|")
        if len(f) < 4:
            continue
        key = priors.normalize_title(f[0])
        if "CHANCE" in f:
            out[key] = {"kind": "binary", "p": float(f[1].rstrip("%")) / 100, "title": f[0]}
        else:
            try:
                lo, hi = [num(x) for x in f[2].strip("()").split(" - ")]
                out[key] = {"kind": "numeric", "median": num(f[1]), "lo": lo, "hi": hi, "title": f[0]}
            except Exception:
                pass
    return out


def load_arm(names: list[str]) -> dict[int, dict]:
    rows = {}
    for n in names:
        p = HERE / n
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["source"]["question_id"]] = r
    return rows


def outcomes() -> dict[int, object]:
    out = {}
    if OVERLAY.exists():
        for line in OVERLAY.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "resolved":
                    out[int(r["question_id"])] = r.get("outcome")
    return out


def spread_of(r: dict) -> float | None:
    if r.get("question_type") == "binary":
        d = r.get("raw_draws") or []
        return (max(d) - min(d)) if len(d) > 1 else None
    rp = r.get("run_percentiles") or []
    if len(rp) > 1:
        meds = [float(x["50"]) for x in rp]
        return max(meds) - min(meds)
    return None


def fmt_est(r: dict) -> str:
    if r.get("question_type") == "binary":
        return f"{float(r['probability']):.3f}"
    if r.get("percentiles"):
        p = r["percentiles"]
        return f"{float(p['50']):.4g} [{float(p['25']):.4g}, {float(p['75']):.4g}]"
    if r.get("probabilities"):
        top = max(r["probabilities"].items(), key=lambda kv: kv[1])
        return f"{top[0][:12]} {top[1]:.2f}"
    return "?"


def logscore(p: float, y: bool) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p if y else 1 - p)


def main() -> None:
    crowd = load_crowd()
    old = load_arm(["old.jsonl", "old2.jsonl", "old3.jsonl"])
    new = load_arm(["new.jsonl", "new2.jsonl", "new3.jsonl", "branch.jsonl"])
    res = outcomes()
    qids = sorted(set(old) & set(new))
    print(f"paired questions: {len(qids)} (old {len(old)}, new {len(new)})\n")
    hdr = f"{'qid':>6} {'type':<7} {'crowd':>22} {'old':>24} {'new':>24} {'sprO':>5} {'sprN':>5} {'$old':>5} {'$new':>5} {'srcO':>4} {'srcN':>4}  title"
    print(hdr)
    costs_o, costs_n, closer_new, closer_old, dbin = [], [], 0, 0, []
    for q in qids:
        o, n = old[q], new[q]
        key = priors.normalize_title(o.get("question") or "")
        c = crowd.get(key)
        if c is None:  # fuzzy
            best = max(crowd.items(), key=lambda kv: priors.jaccard(key, kv[0]), default=(None, None))
            c = best[1] if best[0] and priors.jaccard(key, best[0]) >= 0.5 else None
        if c and c["kind"] == "binary":
            cs = f"{c['p']:.3f}"
            if o.get("question_type") == "binary":
                do = abs(math.log(o["probability"] / (1 - o["probability"])) - math.log(c["p"] / (1 - c["p"])))
                dn = abs(math.log(n["probability"] / (1 - n["probability"])) - math.log(c["p"] / (1 - c["p"])))
                closer_new += dn < do
                closer_old += do < dn
        elif c:
            cs = f"{c['median']:.4g} [{c['lo']:.4g}, {c['hi']:.4g}]"
        else:
            cs = "n/a"
        so, sn = spread_of(o), spread_of(n)
        co, cn = float(o.get("cost_usd") or 0), float(n.get("cost_usd") or 0)
        costs_o.append(co)
        costs_n.append(cn)
        srco = len((o.get("research") or {}).get("sources") or [])
        srcn = len((n.get("research") or {}).get("sources") or [])
        y = res.get(q)
        if isinstance(y, bool) and o.get("question_type") == "binary":
            dbin.append(logscore(n["probability"], y) - logscore(o["probability"], y))
        ys = "" if y is None else f"  -> resolved {y}"
        print(f"{q:>6} {o.get('question_type','')[:7]:<7} {cs:>22} {fmt_est(o):>24} {fmt_est(n):>24} "
              f"{'' if so is None else f'{so:.2f}':>5} {'' if sn is None else f'{sn:.2f}':>5} "
              f"{co:>5.2f} {cn:>5.2f} {srco:>4} {srcn:>4}  {(o.get('question') or '')[:58]}{ys}")
    print(f"\ncost/q: old ${st.mean(costs_o):.2f}  new ${st.mean(costs_n):.2f}  ratio {st.mean(costs_n) / max(st.mean(costs_o), 1e-9):.2f}x")
    print(f"binary questions closer to the crowd (logit distance): new {closer_new}, old {closer_old}")
    if dbin:
        print(f"resolved binaries n={len(dbin)}: mean log-score delta (new-old) {st.mean(dbin):+.4f}")


if __name__ == "__main__":
    main()
