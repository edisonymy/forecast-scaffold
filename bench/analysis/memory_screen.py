"""Memory-claim prefilter: mechanically shortlist rows whose reasoning may assert the
question's outcome as remembered fact (weights leakage surfacing mid-forecast). Applied
uniformly to every arm; flagged rows are then read and judged. Pastcast-only artifact —
a live bot cannot remember an unresolved outcome."""
import io
import json
import re

from pathlib import Path as _P
ROOT = str(_P(__file__).resolve().parents[2])
ARMS = ("base", "premortem", "skeptic")

PATTERNS = re.compile(
    r"already (occurred|happened|taken place|resolved|been "
    r"(announced|adopted|decided|published|signed|held|issued|launched|confirmed|cut))"
    r"|\bI (recall|remember)\b"
    r"|event (has|had) (already )?occurred"
    r"|high confidence as this"
    r"|(this|the) (event|outcome) (is|was) (already )?(known|certain|settled)"
    r"|from memory\b",
    re.IGNORECASE,
)

for arm in ARMS:
    try:
        rows = [json.loads(line) for line in
                io.open(f"{ROOT}/bench/results/btf2-loop1-adm.{arm}.results.jsonl",
                        encoding="utf-8") if line.strip()]
    except FileNotFoundError:
        continue
    hits = [(r, PATTERNS.search(r.get("reasoning") or "")) for r in rows]
    hits = [(r, m) for r, m in hits if m]
    print(f"\n=== {arm}: {len(hits)} candidate(s) of {len(rows)} rows ===")
    for r, m in hits:
        print(f"--- {r['qid']}  p={r['probability']}  match={m.group(0)!r}")
        print("   ", (r.get("reasoning") or "")[:400].replace("\n", " "))
