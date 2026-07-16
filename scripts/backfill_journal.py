"""Backfill journal rows for submitted-but-unjournaled tournament forecasts.

The journal is written before submission, so a missing row means the *commit* was lost
(e.g. the 2026-07-12 git fragmentation incident dropped six MiniBench rows). This tool
reconstructs a minimal, clearly-labelled stub from the platform's own record of our
forecast: Metaculus' ``my_forecasts.latest`` carries the submitted values and the
server-side submission timestamp, so the numbers and their pre-close provenance are
authoritative even though the local reasoning is gone.

Stubs carry ``"backfilled": true`` (ignored by ForecastRecord.from_dict) and say so in
``reasoning``. They are scorable records of what we submitted — nothing more.

Usage: python scripts/backfill_journal.py [--tournament minibench] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "src"))

from metaculus import MetaculusClient  # noqa: E402

from forecast_scaffold.core import SCAFFOLD_VERSION, SCHEMA_VERSION  # noqa: E402

JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
PERCENTILES = (10, 25, 50, 75, 90)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _descale(x: float, scaling: dict) -> float:
    lo, hi = scaling.get("range_min"), scaling.get("range_max")
    if lo is None or hi is None:
        return float(x)
    zp = scaling.get("zero_point")
    if zp is not None:
        d = (hi - zp) / (lo - zp)
        return lo + (hi - lo) * (d**x - 1) / (d - 1)
    return lo + x * (hi - lo)


def _cdf_percentiles(cdf: list[float], scaling: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    n = len(cdf) - 1
    for pct in PERCENTILES:
        target = pct / 100.0
        loc = next((i / n for i, v in enumerate(cdf) if v >= target), 1.0)
        out[str(pct)] = round(_descale(loc, scaling), 6)
    return out


def fetch_all_posts(client: MetaculusClient, tournament: str) -> list[dict]:
    posts: list[dict] = []
    while True:
        page = client._request(  # noqa: SLF001 - read-only reuse of the bot transport
            "GET", "/posts/",
            params={"tournaments": tournament, "limit": 100, "offset": len(posts),
                    "with_cp": "true"},
        )
        batch = page.get("results", []) if page else []
        posts.extend(batch)
        if not batch or not (page or {}).get("next"):
            break
    return posts


def build_stub(post: dict, question: dict, tournament: str) -> dict | None:
    mine = (question.get("my_forecasts") or {}).get("latest") or {}
    values = mine.get("forecast_values") or []
    start = mine.get("start_time")
    if not values or start is None:
        return None
    qtype = str(question.get("type") or "binary")
    forecast_at = _iso(start)
    stub: dict = {
        "question": str(question.get("title") or "")[:300],
        "id": f"{forecast_at[:10]}-backfill-{question.get('id')}",
        "schema_version": SCHEMA_VERSION,
        "scaffold_version": SCAFFOLD_VERSION,
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "forecast_at": forecast_at,
        "status": "open",
        "dry_run": False,
        "question_type": "numeric" if qtype in ("discrete", "date") else qtype,
        "resolution_criterion": str(question.get("resolution_criteria") or "")[:500],
        "resolve_by": str(question.get("scheduled_resolve_time") or "")[:10] or None,
        "source": {
            "platform": "metaculus",
            "question_id": question.get("id"),
            "url": f"https://www.metaculus.com/questions/{post.get('id')}/",
        },
        "provider": "subscription",
        "reasoning": (
            f"[backfilled {datetime.now(UTC).date()} from Metaculus my_forecasts: the "
            f"original journal commit was lost; values and timestamp are the platform's "
            f"record of our live {tournament} submission. No local reasoning survives.]"
        ),
        "backfilled": True,
    }
    if qtype == "binary":
        if len(values) != 2:
            return None
        stub["probability"] = float(values[1])
    elif qtype == "multiple_choice":
        stub["options"] = list(question.get("options") or [])
        stub["probabilities"] = [float(v) for v in values]
    else:
        scaling = question.get("scaling") or {}
        stub["percentiles"] = _cdf_percentiles([float(v) for v in values], scaling)
        stub["submitted_cdf"] = [round(float(v), 7) for v in values]
        stub["scaling"] = {k: scaling.get(k) for k in
                           ("range_min", "range_max", "zero_point")}
    return stub


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tournament", default="minibench")
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    journal_qids = set()
    with args.journal.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                qid = (row.get("source") or {}).get("question_id")
                if qid is not None:
                    journal_qids.add(int(qid))

    client = MetaculusClient()
    stubs: list[dict] = []
    for post in fetch_all_posts(client, args.tournament):
        for question in MetaculusClient.questions_of(post):
            qid = question.get("id")
            if qid is None or int(qid) in journal_qids:
                continue
            stub = build_stub(post, question, args.tournament)
            if stub is not None:
                stubs.append(stub)

    for stub in stubs:
        shape = ("p=" + f"{stub['probability']:.4f}" if "probability" in stub
                 else "percentiles" if "percentiles" in stub else "mc")
        print(f"backfill qid {stub['source']['question_id']} {shape} "
              f"at {stub['forecast_at'][:16]} {stub['question'][:60]!r}")
    if args.dry_run:
        print(f"dry-run: {len(stubs)} stub(s) NOT written")
        return 0
    with args.journal.open("a", encoding="utf-8") as handle:
        for stub in stubs:
            handle.write(json.dumps(stub, ensure_ascii=False) + "\n")
    print(f"appended {len(stubs)} stub(s) to {args.journal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
