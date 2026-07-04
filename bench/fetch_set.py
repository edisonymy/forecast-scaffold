"""Build a frozen benchmark question set from ForecastBench's public datasets.

ForecastBench (Forecasting Research Institute, CC-BY-SA 4.0) publishes ~500-question
sets biweekly; the market-sourced questions (Metaculus, Manifold, Polymarket, RAND/INFER)
carry ``freeze_datetime_value`` — the crowd probability at freeze time. That is the
benchmark's ground-truth proxy, and it requires no Metaculus token (Metaculus firewalls
bot accounts from its human crowd; this data is republished upstream).

Only questions whose market is still OPEN are kept, so the answer cannot be researched.
``--refresh-crowd`` re-reads Manifold/Polymarket prices live (their APIs are public and
anonymous) so the target is today's crowd rather than the freeze-day crowd.

The output is one JSON object per line; sets are gitignored (CC-BY-SA content).

Usage:
    python bench/fetch_set.py --n 40 --out bench/sets/2026-07-04.jsonl --refresh-crowd
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

RAW_BASE = "https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main"
UA = {"User-Agent": "forecast-scaffold-bench/0.1 (+https://github.com/edisonymy/forecast-scaffold)"}
MARKET_SOURCES = ("metaculus", "manifold", "polymarket", "infer")
# "Resolves to the outcome of the question found at <url>." — the URL is the crowd; strip it.
FOUND_AT = re.compile(r"\s*Resolves to the outcome of the question found at \S+\.?", re.IGNORECASE)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_crowd(source: str, market_id: str) -> float | None:
    """Today's price from the public Manifold/Polymarket APIs; None when unavailable."""
    try:
        if source == "manifold":
            data = json.loads(_get(f"https://api.manifold.markets/v0/market/{market_id}"))
            p = data.get("probability")
            return float(p) if p is not None else None
        if source == "polymarket":
            url = "https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(
                {"condition_ids": market_id}
            )
            markets = json.loads(_get(url))
            if markets:
                prices = json.loads(markets[0].get("outcomePrices") or "[]")
                return float(prices[0]) if prices else None
    except Exception:  # noqa: BLE001 - refresh is best-effort; freeze value remains
        return None
    return None


def latest_set_name() -> str:
    """The newest dated question-set file, per the GitHub contents API."""
    listing = json.loads(_get(
        "https://api.github.com/repos/forecastingresearch/forecastbench-datasets"
        "/contents/datasets/question_sets"
    ).decode("utf-8"))
    dated = sorted(
        entry["name"] for entry in listing
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}-llm\.json", entry.get("name", ""))
    )
    if not dated:
        raise RuntimeError("no dated question sets found upstream")
    return dated[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="latest",
                        help='question-set date like 2026-06-21, or "latest"')
    parser.add_argument("--n", type=int, default=40, help="total questions to sample")
    parser.add_argument("--seed", type=int, default=7, help="deterministic sampling seed")
    parser.add_argument("--sources", default=",".join(MARKET_SOURCES))
    parser.add_argument("--out", required=True)
    parser.add_argument("--refresh-crowd", action="store_true",
                        help="update Manifold/Polymarket crowd values from their live APIs")
    args = parser.parse_args(argv)

    name = latest_set_name() if args.date == "latest" else f"{args.date}-llm.json"
    payload = json.loads(_get(f"{RAW_BASE}/datasets/question_sets/{name}").decode("utf-8"))
    questions = payload["questions"]
    print(f"{name}: {len(questions)} questions, due {payload.get('forecast_due_date')}")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    eligible: dict[str, list[dict]] = {s: [] for s in sources}
    for q in questions:
        source = q.get("source")
        if source not in eligible:
            continue
        try:
            crowd = float(q.get("freeze_datetime_value"))
        except (TypeError, ValueError):
            continue
        if not 0.0 <= crowd <= 1.0:
            continue
        close = str(q.get("market_info_close_datetime") or "")
        if close and close[:10] <= today:
            continue  # market closed: outcome may be researchable -> leakage
        eligible[source].append(q)

    rng = random.Random(args.seed)
    per_source = max(1, args.n // len(sources))
    picked: list[dict] = []
    for source in sources:
        pool = sorted(eligible[source], key=lambda q: str(q["id"]))
        take = min(per_source, len(pool))
        picked.extend(rng.sample(pool, take))
        print(f"  {source}: {len(pool)} eligible, taking {take}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for q in picked:
            crowd_value = float(q["freeze_datetime_value"])
            crowd_at = str(q.get("freeze_datetime") or "")
            crowd_src = f"forecastbench {q['source']} freeze"
            if args.refresh_crowd and q["source"] in ("manifold", "polymarket"):
                live = live_crowd(q["source"], str(q["id"]))
                time.sleep(1)
                if live is not None:
                    crowd_value, crowd_at = live, _now_iso()
                    crowd_src = f"{q['source']} live"
            criteria = FOUND_AT.sub("", str(q.get("resolution_criteria") or "")).strip()
            market_criteria = str(q.get("market_info_resolution_criteria") or "")
            criteria = (criteria + "\n" + market_criteria).strip()
            spec = {
                "id": f"{q['source']}:{q['id']}",
                "source": q["source"],
                "question": q.get("question", ""),
                "background": str(q.get("background") or "")[:4000],
                "criteria": criteria[:4000],
                "resolve_by": str(q.get("market_info_close_datetime") or "")[:10] or None,
                "crowd": {"value": crowd_value, "at": crowd_at, "source": crowd_src},
                "url": q.get("url", ""),  # for humans reviewing results; never shown to the agent
            }
            fh.write(json.dumps(spec, ensure_ascii=False) + "\n")
    print(f"wrote {len(picked)} questions -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
