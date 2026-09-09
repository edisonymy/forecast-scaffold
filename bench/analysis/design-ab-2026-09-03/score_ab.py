"""Score the 2026-09-03 design A/B at resolution: paired log score + continuous platform score."""
from __future__ import annotations
import json, math, statistics as st, sys
from pathlib import Path

ROOT = Path(r"C:\Users\Edison Yi\Documents\code\forecast-scaffold")
D = ROOT / "bench" / "analysis" / "design-ab-2026-09-03"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from forecast_scaffold.core import percentiles_to_cdf  # noqa: E402
from bench.analysis.minibench_numeric_tails import boot_ci, location_of, score_row  # noqa: E402


def load(names):
    out = {}
    for n in names:
        p = D / n
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["source"]["question_id"]] = r
    return out


res = {}
for line in (ROOT / "bot" / "journal" / "resolutions.jsonl").read_text(encoding="utf-8").splitlines():
    if line.strip():
        r = json.loads(line)
        if r.get("status") == "resolved":
            res[int(r["question_id"])] = r


def logscore(p, y):
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return math.log(p if y else 1 - p)


old, new = load(["old.jsonl", "old3.jsonl"]), load(["new.jsonl", "new3.jsonl"])
q = sorted(set(old) & set(new) & set(res))

bin_d, num_d, rows = [], [], []
for qid in q:
    o, n, r = old[qid], new[qid], res[qid]
    y = r.get("outcome")
    if o["question_type"] == "binary" and isinstance(y, bool):
        do, dn = logscore(o["probability"], y), logscore(n["probability"], y)
        bin_d.append(dn - do)
        rows.append((qid, "binary", o["probability"], n["probability"], y, dn - do))
    elif o.get("percentiles") and n.get("percentiles") and isinstance(y, (int, float)):
        sc = o.get("scaling") or {}
        try:
            co = percentiles_to_cdf({str(k): float(v) for k, v in o["percentiles"].items()},
                                    float(sc["range_min"]), float(sc["range_max"]),
                                    lower_open=bool(sc.get("lower_open")), upper_open=bool(sc.get("upper_open")),
                                    zero_point=sc.get("zero_point"), cdf_size=int(sc.get("cdf_size") or 201),
                                    p_below_lower=o.get("p_below_lower"), p_above_upper=o.get("p_above_upper"),
                                    interpolation="pchip")
            scn = n.get("scaling") or sc
            cn = percentiles_to_cdf({str(k): float(v) for k, v in n["percentiles"].items()},
                                    float(scn["range_min"]), float(scn["range_max"]),
                                    lower_open=bool(scn.get("lower_open")), upper_open=bool(scn.get("upper_open")),
                                    zero_point=scn.get("zero_point"), cdf_size=int(scn.get("cdf_size") or 201),
                                    p_below_lower=n.get("p_below_lower"), p_above_upper=n.get("p_above_upper"),
                                    interpolation="pchip")
        except Exception as e:
            print(f"  skip {qid}: {e}")
            continue
        so, sn = score_row(co, float(y), sc), score_row(cn, float(y), scn)
        num_d.append(sn - so)
        rows.append((qid, "numeric", so, sn, y, sn - so))

print(f"paired resolved: {len(rows)}  (binary {len(bin_d)}, continuous {len(num_d)})\n")
print(f"{'qid':>6} {'type':<8} {'old':>10} {'new':>10} {'outcome':>12} {'delta':>9}")
for qid, t, a, b, y, d in rows:
    fa = f"{a:.3f}" if t == "binary" else f"{a:+.1f}"
    fb = f"{b:.3f}" if t == "binary" else f"{b:+.1f}"
    print(f"{qid:>6} {t:<8} {fa:>10} {fb:>10} {str(y)[:12]:>12} {d:>+9.3f}")

if bin_d:
    lo, hi = boot_ci(bin_d)
    print(f"\nBINARY log-score delta (new-old): mean {st.mean(bin_d):+.4f} CI90 [{lo:+.4f}, {hi:+.4f}]  "
          f"new better on {sum(1 for d in bin_d if d > 0)}/{len(bin_d)}")
    print(f"  in spot-peer points (x50): {50*st.mean(bin_d):+.2f}/q")
if num_d:
    lo, hi = boot_ci(num_d)
    print(f"CONTINUOUS platform-score delta (new-old): mean {st.mean(num_d):+.2f}/q CI90 [{lo:+.2f}, {hi:+.2f}]  "
          f"new better on {sum(1 for d in num_d if d > 0)}/{len(num_d)}")
alld = [d for *_, d in rows]
print(f"\nheadline: {len(alld)} paired resolved questions; the two designs are "
      f"{'indistinguishable' if abs(st.mean(bin_d or [0]))<0.05 else 'different'} on this sample.")
