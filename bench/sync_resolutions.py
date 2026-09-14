"""Sync platform resolutions and our own scores for every journaled Metaculus forecast.

The bot journal (``bot/journal/forecasts.jsonl``) is written BEFORE submission and never
learns how a question resolved: every row stays ``status: open`` forever, so the
calibration layer, the Platt gate and any same-template lookup have nothing to read.
Rewriting the journal in place would race the 10-minute CI commits (append-only JSONL
merges cleanly; in-place rewrites do not), so resolutions live in a separate append-only
OVERLAY file, ``bot/journal/resolutions.jsonl`` — one row per question id, latest wins.

What the overlay carries per question (all from the platform record, via the bot token):
  status / resolution_raw            the platform's own status and resolution string
  outcome                            normalized: bool (binary), float (numeric/discrete,
                                     out-of-range encoded as the string it came as),
                                     option label (MC), None when annulled/ambiguous
  spot_peer_score, spot_baseline_score, peer_score, baseline_score, coverage
                                     our own scores — the API DOES return these for our
                                     own forecasts (only the community aggregate is hidden
                                     on bot tournaments)
  pit                                where the outcome fell in OUR submitted CDF
                                     (continuous questions only; from
                                     my_forecasts.latest.forecast_values)
  forecast_values_n                  length of the submitted vector (sanity)

Rate limits: the posts endpoint 429s at roughly one request per second sustained; the
sync sleeps ``--sleep`` seconds between calls and backs off on 429. First sync of ~400
questions takes ~20 minutes; later syncs only touch questions not yet final.

Usage:
    python bench/sync_resolutions.py                 # sync (incremental)
    python bench/sync_resolutions.py --readout       # sync, then print the season tables
    python bench/sync_resolutions.py --no-sync --readout
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics as st
import sys
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "src"))

from metaculus import MetaculusClient  # noqa: E402

from forecast_scaffold.core import _scale_location  # noqa: E402

JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
OVERLAY = ROOT / "bot" / "journal" / "resolutions.jsonl"
FINAL_STATUSES = ("resolved", "annulled")
CONTINUOUS = ("numeric", "discrete", "date")


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def load_journal(path: Path) -> dict[int, dict[str, Any]]:
    """Latest journal row per Metaculus question id (dry runs excluded)."""
    latest: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = row.get("source") or {}
            if not isinstance(src, dict) or src.get("platform") != "metaculus":
                continue
            if row.get("dry_run"):
                continue
            qid = src.get("question_id")
            if qid is None:
                continue
            prev = latest.get(int(qid))
            if prev is None or str(row.get("forecast_at")) > str(prev.get("forecast_at")):
                latest[int(qid)] = row
    return latest


def load_overlay(path: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[int(row["question_id"])] = row  # latest line wins
    return out


def post_id_of(row: dict[str, Any]) -> int | None:
    src = row.get("source") or {}
    if src.get("post_id") is not None:
        return int(src["post_id"])
    url = str(src.get("url") or "")
    for part in url.split("/"):
        if part.isdigit():
            return int(part)
    return None


def normalize_outcome(qtype: str, resolution: Any) -> tuple[Any, bool]:
    """(outcome, annulled). Binary -> bool; continuous -> float when parseable, else the
    platform string (``above_upper_bound`` etc.); MC -> the label; annulled -> (None, True)."""
    if resolution is None:
        return None, False
    s = str(resolution)
    if s.lower() in ("annulled", "ambiguous"):
        return None, True
    if qtype == "binary":
        if s.lower() == "yes":
            return True, False
        if s.lower() == "no":
            return False, False
        return None, True
    if qtype in CONTINUOUS:
        try:
            return float(s), False
        except ValueError:
            return s, False
    return s, False


def pit_of(fv: list[float] | None, outcome: Any, scaling: dict[str, Any] | None) -> float | None:
    """Probability-integral transform of the outcome under the submitted CDF."""
    if not fv or scaling is None or not isinstance(outcome, (int, float)):
        return None
    lo, hi = scaling.get("range_min"), scaling.get("range_max")
    if lo is None or hi is None:
        return None
    try:
        u = _scale_location(float(outcome), float(lo), float(hi), scaling.get("zero_point"))
    except (ValueError, ZeroDivisionError):
        return None
    u = min(max(u, 0.0), 1.0)
    i = min(int(u * (len(fv) - 1)), len(fv) - 1)
    return float(fv[i])


def fetch_with_backoff(client: MetaculusClient, post_id: int, attempts: int = 6) -> dict[str, Any]:
    for i in range(attempts):
        try:
            return client.post_detail(post_id)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and i + 1 < attempts:
                time.sleep(20.0 * (i + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def sync(journal: Path, overlay: Path, *, sleep: float, limit: int, verbose: bool) -> int:
    rows = load_journal(journal)
    have = load_overlay(overlay)
    todo = [
        (qid, row) for qid, row in sorted(rows.items())
        if have.get(qid, {}).get("status") not in FINAL_STATUSES
    ]
    if limit > 0:
        todo = todo[:limit]
    final = sum(1 for r in have.values() if r.get("status") in FINAL_STATUSES)
    print(f"journal questions: {len(rows)}; final in overlay: {final}; to fetch: {len(todo)}")
    client = MetaculusClient()
    written = 0
    tally = collections.Counter()
    with overlay.open("a", encoding="utf-8") as out:
        for n, (qid, row) in enumerate(todo, 1):
            post_id = post_id_of(row)
            if post_id is None:
                tally["no post id"] += 1
                continue
            try:
                post = fetch_with_backoff(client, post_id)
            except Exception as exc:  # noqa: BLE001 — one question must not stop the sync
                print(f"  {qid}: fetch failed ({exc})", flush=True)
                tally["fetch failed"] += 1
                continue
            question = None
            for q in MetaculusClient.questions_of(post or {}):
                if int(q.get("id", -1)) == qid:
                    question = q
                    break
            if question is None:
                print(f"  {qid}: not found in post {post_id}", flush=True)
                tally["not found"] += 1
                continue
            qtype = str(question.get("type") or row.get("question_type") or "")
            outcome, annulled = normalize_outcome(qtype, question.get("resolution"))
            status = str(question.get("status") or "")
            if annulled and status == "resolved":
                status = "annulled"
            mf = question.get("my_forecasts") or {}
            sd = mf.get("score_data") or {}
            fv = (mf.get("latest") or {}).get("forecast_values")
            entry = {
                "question_id": qid,
                "post_id": post_id,
                "record_id": row.get("id"),
                "question_type": qtype,
                "title": str(post.get("title") or "")[:120],
                "status": status,
                "resolution_raw": question.get("resolution"),
                "outcome": outcome,
                "annulled": annulled,
                "resolved_at": question.get("actual_resolve_time"),
                "close_time": (question.get("actual_close_time")
                               or question.get("scheduled_close_time")),
                "spot_peer_score": sd.get("spot_peer_score"),
                "spot_baseline_score": sd.get("spot_baseline_score"),
                "peer_score": sd.get("peer_score"),
                "baseline_score": sd.get("baseline_score"),
                "coverage": sd.get("coverage"),
                "pit": (pit_of(fv, outcome, question.get("scaling"))
                        if qtype in CONTINUOUS else None),
                "forecast_values_n": len(fv) if fv else 0,
                "synced_at": _now_iso(),
            }
            # Append only on change: a closed-but-unresolved question is re-fetched every
            # run, and re-appending an identical row would grow the overlay by dozens of
            # lines per run for nothing (latest-line-wins reads stay correct either way).
            prev = have.get(qid)
            if prev is not None and all(
                prev.get(k) == entry[k]
                for k in ("status", "resolution_raw", "spot_peer_score", "spot_baseline_score")
            ):
                tally["unchanged"] += 1
                if verbose:
                    print(f"  [{n}/{len(todo)}] {qid} unchanged ({status})", flush=True)
                time.sleep(sleep)
                continue
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            out.flush()
            written += 1
            tally["written"] += 1
            if verbose or n % 25 == 0:
                print(f"  [{n}/{len(todo)}] {qid} {qtype} {status} -> "
                      f"{question.get('resolution')!r}", flush=True)
            time.sleep(sleep)
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(tally.items())) or "nothing to do"
    print(f"wrote {written} overlay row(s) to {overlay} ({summary})", flush=True)
    return written


# ----------------------------------------------------------------------------- readout


def _fmt_block(label: str, groups: dict[Any, list[float]]) -> None:
    for key, vals in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if not vals:
            continue
        print(f"  {label}={str(key):22s} n={len(vals):3d} sum={sum(vals):8.1f} "
              f"mean={st.mean(vals):6.1f} median={st.median(vals):6.1f} "
              f"neg={sum(1 for v in vals if v < 0):3d} min={min(vals):7.1f} max={max(vals):6.1f}")


def readout(journal: Path, overlay: Path, *, since: str | None) -> None:
    rows = load_journal(journal)
    have = load_overlay(overlay)
    joined = []
    for qid, res in have.items():
        row = rows.get(qid)
        if row is None or res.get("spot_peer_score") is None:
            continue
        if since and str(row.get("forecast_at")) < since:
            continue
        joined.append((row, res))
    print(f"\nscored questions: {len(joined)}  total spot peer: "
          f"{sum(r['spot_peer_score'] for _, r in joined):+.1f}  "
          f"mean/q: {st.mean(r['spot_peer_score'] for _, r in joined) if joined else 0:+.2f}")
    for label, key in (
        ("type", lambda row, res: res.get("question_type")),
        ("effort", lambda row, res: row.get("effort")),
        ("model", lambda row, res: row.get("model")),
        ("version", lambda row, res: row.get("scaffold_version")),
        ("month", lambda row, res: str(res.get("close_time") or "")[:7]),
    ):
        groups: dict[Any, list[float]] = collections.defaultdict(list)
        for row, res in joined:
            groups[key(row, res)].append(float(res["spot_peer_score"]))
        print(f"BY {label.upper()}")
        _fmt_block(label, groups)
    # binary calibration buckets
    bins = [(row, res) for row, res in joined
            if res.get("question_type") == "binary" and isinstance(res.get("outcome"), bool)
            and isinstance(row.get("probability"), (int, float))]
    if bins:
        buckets: dict[int, list[tuple[float, int, float]]] = collections.defaultdict(list)
        for row, res in bins:
            p = float(row["probability"])
            buckets[min(int(p * 10), 9)].append(
                (p, int(res["outcome"]), float(res["spot_peer_score"])))
        brier = st.mean((p - y) ** 2 for v in buckets.values() for p, y, _ in v)
        print(f"BINARY n={len(bins)} Brier={brier:.4f} base rate yes="
              f"{st.mean(y for v in buckets.values() for _, y, _ in v):.2f}")
        for k in sorted(buckets):
            v = buckets[k]
            print(f"  {k / 10:.1f}-{(k + 1) / 10:.1f}: n={len(v):3d} "
                  f"p={st.mean(x[0] for x in v):.2f} yes={st.mean(x[1] for x in v):.2f} "
                  f"score={st.mean(x[2] for x in v):6.1f}")
    pits = [float(res["pit"]) for _, res in joined if res.get("pit") is not None]
    if pits:
        print(f"CONTINUOUS PIT n={len(pits)} in 25-75: {sum(1 for p in pits if 0.25 <= p <= 0.75)} "
              f"in 10-90: {sum(1 for p in pits if 0.10 <= p <= 0.90)} below p10: "
              f"{sum(1 for p in pits if p < 0.10)} above p90: {sum(1 for p in pits if p > 0.90)} "
              f"above median: {sum(1 for p in pits if p > 0.5)}")
    worst = sorted(joined, key=lambda t: float(t[1]["spot_peer_score"]))[:10]
    print("WORST 10")
    for _row, res in worst:
        print(f"  {res['question_id']} {res['question_type']:15s} "
              f"sp={float(res['spot_peer_score']):7.1f} "
              f"res={str(res.get('resolution_raw'))[:12]:12s} {res.get('title', '')[:60]}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--overlay", default=str(OVERLAY))
    ap.add_argument("--sleep", type=float, default=2.5)
    ap.add_argument("--limit", type=int, default=0,
                    help="max questions to fetch this run (0 = all)")
    ap.add_argument("--no-sync", action="store_true")
    ap.add_argument("--readout", action="store_true")
    ap.add_argument("--since", default=None,
                    help="readout: only rows forecast at/after this ISO date")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    journal, overlay = Path(args.journal), Path(args.overlay)
    if not args.no_sync:
        sync(journal, overlay, sleep=args.sleep, limit=args.limit, verbose=args.verbose)
    if args.readout:
        readout(journal, overlay, since=args.since)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
