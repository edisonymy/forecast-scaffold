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


def build_criteria(q: dict) -> str:
    """The resolution contract for the brief.

    ForecastBench market questions vary: Metaculus/Manifold carry real text in
    ``market_info_resolution_criteria``, but Polymarket/INFER set it to the literal
    string "N/A" and put the binding terms inside ``background``. Handing the agent
    "Resolution criteria: N/A" makes it forecast the headline instead of the contract
    (observed in the 2026-07-04 baseline), so point at the background explicitly.
    """
    stripped = FOUND_AT.sub("", str(q.get("resolution_criteria") or "")).strip()
    market = str(q.get("market_info_resolution_criteria") or "").strip()
    if market.upper() in ("", "N/A", "NONE"):
        market = ""
    combined = (stripped + "\n" + market).strip()
    if not combined:
        return ("(The binding resolution terms are stated inside the Background section "
                "below — read them adversarially as the contract.)")
    return combined[:4000]


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


def forecastbench_specs(args: argparse.Namespace) -> list[dict]:
    """Sample market questions from a ForecastBench question set (multi-source, curated)."""
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

    specs: list[dict] = []
    dropped_extreme = dropped_stale = 0
    for q in picked:
        crowd_value = float(q["freeze_datetime_value"])
        crowd_at = str(q.get("freeze_datetime") or "")
        crowd_src = f"forecastbench {q['source']} freeze"
        if args.refresh_crowd and q["source"] in ("manifold", "polymarket"):
            live = live_crowd(q["source"], str(q["id"]))
            time.sleep(1)
            if live is None:
                # A market we can't confirm live carries stale-freeze risk: the 2026-07-04
                # baseline's worst "miss" was a question SCOTUS had already decided — the
                # bot was right and the 3-week-old freeze was wrong. Drop, don't guess.
                dropped_stale += 1
                continue
            if not 0.03 <= live <= 0.97:
                dropped_extreme += 1  # trading at an extreme = effectively resolved
                continue
            crowd_value, crowd_at, crowd_src = live, _now_iso(), f"{q['source']} live"
        specs.append({
            "id": f"{q['source']}:{q['id']}",
            "source": q["source"],
            "question": q.get("question", ""),
            "background": str(q.get("background") or "")[:8000],
            "criteria": build_criteria(q),
            "resolve_by": str(q.get("market_info_close_datetime") or "")[:10] or None,
            "crowd": {"value": crowd_value, "at": crowd_at, "source": crowd_src},
            "url": q.get("url", ""),  # for humans reviewing results; never shown to the agent
        })
    if dropped_stale or dropped_extreme:
        print(f"  dropped: {dropped_stale} unconfirmable-live, "
              f"{dropped_extreme} at-extreme (effectively resolved)")
    stale = sum(1 for s in specs if "freeze" in s["crowd"]["source"])
    if stale:
        print(f"  note: {stale} question(s) keep freeze-time crowd values (no live source "
              f"for metaculus/infer) — treat their per-question gaps with suspicion")
    return specs


def manifold_specs(args: argparse.Namespace) -> list[dict]:
    """Sample liquid, still-open binary markets directly from Manifold's public API.

    Fresh questions on demand between the biweekly ForecastBench drops; the crowd target
    is the live market probability. Filtered to ``--min-traders`` unique bettors so the
    price reflects a real crowd, and to open markets so the outcome is not researchable.
    (Manifold is play-money and creator-resolved — noisier ground truth than ForecastBench's
    curated multi-source set, but far larger and free to pull. Prefer ForecastBench as the
    headline benchmark; use this for cheap, frequent iteration.)
    """
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    listing = json.loads(_get(
        "https://api.manifold.markets/v0/search-markets?" + urllib.parse.urlencode({
            "term": "", "sort": "liquidity", "filter": "open",
            "contractType": "BINARY", "limit": max(200, args.n * 6),
        })
    ).decode("utf-8"))
    eligible = [
        m for m in listing
        if not m.get("isResolved")
        and (m.get("closeTime") or now_ms + 1) > now_ms
        and (m.get("uniqueBettorCount") or 0) >= args.min_traders
        and 0.02 <= float(m.get("probability", -1)) <= 0.98
    ]
    rng = random.Random(args.seed)
    pool = sorted(eligible, key=lambda m: str(m["id"]))
    picked = rng.sample(pool, min(args.n, len(pool)))
    print(f"manifold: {len(listing)} listed, {len(eligible)} eligible "
          f"(>= {args.min_traders} bettors, open, non-extreme), taking {len(picked)}")

    specs: list[dict] = []
    for m in picked:
        full = json.loads(_get(f"https://api.manifold.markets/v0/market/{m['id']}").decode("utf-8"))
        time.sleep(0.5)
        close_ms = full.get("closeTime")
        resolve_by = (
            datetime.fromtimestamp(close_ms / 1000, tz=UTC).date().isoformat()
            if close_ms else None
        )
        criteria = ("Resolves per the market creator's stated conditions on Manifold. "
                    "Description:\n" + str(full.get("textDescription") or "")).strip()
        specs.append({
            "id": f"manifold:{m['id']}",
            "source": "manifold",
            "question": full.get("question", m.get("question", "")),
            "background": "",
            "criteria": criteria[:4000],
            "resolve_by": resolve_by,
            "crowd": {"value": float(full.get("probability", m["probability"])),
                      "at": _now_iso(), "source": "manifold live"},
            "url": m.get("url", ""),  # for humans reviewing results; never shown to the agent
        })
    return specs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source_of", default="forecastbench",
                        choices=("forecastbench", "manifold"),
                        help="where questions come from (default forecastbench)")
    parser.add_argument("--date", default="latest",
                        help='forecastbench set date like 2026-06-21, or "latest"')
    parser.add_argument("--n", type=int, default=40, help="total questions to sample")
    parser.add_argument("--seed", type=int, default=7, help="deterministic sampling seed")
    parser.add_argument("--sources", default=",".join(MARKET_SOURCES),
                        help="forecastbench sub-sources (comma-separated)")
    parser.add_argument("--min-traders", type=int, default=30,
                        help="manifold: minimum unique bettors for a market to qualify")
    parser.add_argument("--out", required=True)
    parser.add_argument("--refresh-crowd", action="store_true",
                        help="forecastbench: update Manifold/Polymarket crowd values live")
    args = parser.parse_args(argv)

    specs = manifold_specs(args) if args.source_of == "manifold" else forecastbench_specs(args)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for spec in specs:
            fh.write(json.dumps(spec, ensure_ascii=False) + "\n")
    print(f"wrote {len(specs)} questions -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
