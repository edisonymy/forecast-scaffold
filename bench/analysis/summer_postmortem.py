"""Summer 2026 FutureEval post-mortem: where did the points go, and when were they lost?"""
from __future__ import annotations
import json
import os
import statistics as st
import urllib.request
import collections
import datetime as dt
from pathlib import Path

ROOT = Path(r"C:\Users\Edison Yi\Documents\code\forecast-scaffold")
tok = os.environ.get("METACULUS_TOKEN", "")
H = {"User-Agent": "forecast-scaffold-bot/0.1", "Accept": "application/json", "Authorization": f"Token {tok}"}


def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60))


# --- leaderboard now ---
lb = get("https://www.metaculus.com/api/leaderboards/project/33022/")[0]
rows = [r for r in lb["entries"] if r.get("user")]
rows.sort(key=lambda r: r.get("rank") or 9999)
me = next(r for r in rows if r["user"]["username"] == "edisonymy-bot")
winners = [r for r in rows if (r.get("prize") or 0) > 0]
print(f"LEADERBOARD (finalized={lb.get('finalized')}): rank {me['rank']}/{len(rows)}  score {me['score']:+.1f}  "
      f"coverage {me['coverage']:.0f}/{lb['max_coverage']:.0f}  n={me['contribution_count']}  "
      f"per-q {me['score']/me['contribution_count']:+.2f}")
print(f"  prize winners {len(winners)}, prize line {min(r['score'] for r in winners):+.1f}; "
      f"top-3 per-q: " + ", ".join(f"{r['user']['username']} {r['score']/r['contribution_count']:+.1f}" for r in rows[:3]))
print(f"  our prize (if any): {me.get('prize')}")

# --- summer question ids ---
qids, off = set(), 0
while True:
    d = get(f"https://www.metaculus.com/api/posts/?tournaments=summer-futureeval-2026&limit=100&offset={off}&with_cp=false")
    res = d.get("results", [])
    for p in res:
        qs = [p["question"]] if p.get("question") else (p.get("group_of_questions") or {}).get("questions", [])
        for q in qs:
            qids.add(int(q["id"]))
    if not d.get("next") or not res:
        break
    off += 100
print(f"summer questions: {len(qids)}")

# --- journal + overlay ---
journal = {}
for line in (ROOT / "bot" / "journal" / "forecasts.jsonl").read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    r = json.loads(line)
    src = r.get("source") or {}
    if src.get("platform") != "metaculus" or r.get("dry_run"):
        continue
    q = src.get("question_id")
    if q in qids:
        prev = journal.get(q)
        if prev is None or r["forecast_at"] > prev["forecast_at"]:
            journal[q] = r
overlay = {}
for line in (ROOT / "bot" / "journal" / "resolutions.jsonl").read_text(encoding="utf-8").splitlines():
    if line.strip():
        r = json.loads(line)
        overlay[int(r["question_id"])] = r
scored = [(journal[q], overlay[q]) for q in journal if q in overlay and overlay[q].get("spot_peer_score") is not None]
print(f"our summer forecasts: {len(journal)}; scored: {len(scored)}; total {sum(o['spot_peer_score'] for _, o in scored):+.1f}")


def block(label, key, minn=1):
    g = collections.defaultdict(list)
    for j, o in scored:
        g[key(j, o)].append(float(o["spot_peer_score"]))
    print(f"\nBY {label}")
    for k, v in sorted(g.items(), key=lambda kv: str(kv[0])):
        if len(v) < minn:
            continue
        print(f"  {str(k):24s} n={len(v):3d} sum={sum(v):8.1f} mean={st.mean(v):6.1f} median={st.median(v):6.1f} "
              f"neg={sum(1 for x in v if x<0):3d} worst={min(v):7.1f}")


def week(j, o):
    d = dt.date.fromisoformat(j["forecast_at"][:10])
    return (d - dt.timedelta(days=d.weekday())).isoformat()


block("FORECAST WEEK", week)
block("SCAFFOLD VERSION", lambda j, o: j.get("scaffold_version"))
block("MODEL", lambda j, o: j.get("model"))
block("TYPE", lambda j, o: o.get("question_type"))
block("VERSION x TYPE", lambda j, o: f"{j.get('scaffold_version')} {o.get('question_type')[:7]}", minn=3)

# catastrophes
print("\nCATASTROPHES (score < -80):")
for j, o in sorted(scored, key=lambda t: t[1]["spot_peer_score"])[:12]:
    if o["spot_peer_score"] > -80:
        break
    print(f"  {o['spot_peer_score']:7.1f} {j['forecast_at'][:10]} v{j.get('scaffold_version')} {j.get('model','')[:14]:14s} "
          f"{o['question_type'][:7]:7s} res={str(o.get('resolution_raw'))[:12]:12s} {o.get('title','')[:55]}")

# counterfactuals
tot = sum(o["spot_peer_score"] for _, o in scored)
cats = [o["spot_peer_score"] for _, o in scored if o["spot_peer_score"] < -100]
aug = [o["spot_peer_score"] for j, o in scored if j["forecast_at"] >= "2026-08-01"]
jul = [o["spot_peer_score"] for j, o in scored if j["forecast_at"] < "2026-08-01"]
print("\nCOUNTERFACTUALS")
print(f"  actual total {tot:+.1f} over {len(scored)}")
print(f"  without the {len(cats)} catastrophes (< -100): {tot - sum(cats):+.1f}  (they cost {sum(cats):+.1f})")
print(f"  July  n={len(jul)} mean {st.mean(jul):+.1f}/q   August+ n={len(aug)} mean {st.mean(aug):+.1f}/q")
print(f"  if every scored q had the August+ rate: {st.mean(aug)*len(scored):+.1f}")
print(f"  if we had covered all {lb['max_coverage']:.0f} scored q at the August+ rate: {st.mean(aug)*lb['max_coverage']:+.1f} "
      f"(prize line {min(r['score'] for r in winners):+.1f})")
print(f"  if we had covered all at our actual rate: {tot/len(scored)*lb['max_coverage']:+.1f}")
