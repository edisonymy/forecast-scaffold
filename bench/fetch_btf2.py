"""Build a frozen "pastcasting" question set from FutureSearch's public BTF-2 dataset
(Bench-to-the-Future 2, HuggingFace ``BTF-2/BTF-2``), for offline reasoning-layer eval.

Unlike ``fetch_set.py``'s still-open markets, BTF-2 questions are already RESOLVED —
that is the point: pastcasting freezes the agent's effective "now" at ``present_date``
and hands it a compiled research dossier as the only evidence, so web access can be
(and, in ``run_bench.py``, is) disabled entirely. The true outcome and FutureSearch's
own SOTA forecast are fetched too, but stored ONLY under ``resolution``/``crowd`` for
offline scoring — never in any field that reaches the agent prompt. See the leak-hygiene
note on ``build_spec`` below.

Data comes from HuggingFace's datasets-server JSON API (no auth, no dependency beyond
stdlib): first ``/splits`` to discover the config/split names, then paginated ``/rows``.

The output is one JSON object per line, in the same set-file format ``bench/run_bench.py``
and ``bench/fetch_set.py`` already produce; sets are gitignored.

Usage:
    python bench/fetch_btf2.py --n 200 --out bench/sets/btf2-200.jsonl
    python bench/fetch_btf2.py --n 50 --seed 1 --out bench/sets/btf2-dev.jsonl --refresh
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

UA = {"User-Agent": "forecast-scaffold-bench/0.1 (+https://github.com/edisonymy/forecast-scaffold)"}
API_BASE = "https://datasets-server.huggingface.co"
DATASET = "BTF-2/BTF-2"
PAGE_SIZE = 100
RAW_CACHE = Path(__file__).resolve().parent / "sets" / "btf2_raw.json"

# Fields we require to build a usable, leak-safe row. Verified against the first live
# page in the smoke test; if the upstream schema renames these, fetch_btf2 fails loudly
# below rather than silently emitting empty/garbage specs.
REQUIRED_FIELDS = (
    "question_id",
    "question",
    "resolution_criteria",
    "present_date",
    "research_summary",
    "resolution",
    "sota_forecast_probability",
)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _get_with_retries(url: str, attempts: int = 3) -> bytes:
    """GET with retry-with-backoff; transient datasets-server hiccups shouldn't kill a
    ~15-page fetch."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return _get(url)
        except (urllib.error.URLError, TimeoutError) as exc:  # noqa: PERF203
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(2**attempt)
    raise RuntimeError(f"GET failed after {attempts} attempts: {url}") from last_exc


def discover_split() -> tuple[str, str]:
    """Return (config, split) for the main/default config and its largest split.

    "Main/default" is judged by content shape, not just size: BTF-2/BTF-2 also ships a
    ``scraped_pages`` config (raw scraped web pages keyed by question_id/url/date_scraped
    — auxiliary crawl data, not questions) that is far larger by row count than the
    actual question set. We only rank configs that expose the fields we need.
    """
    url = f"{API_BASE}/splits?" + urllib.parse.urlencode({"dataset": DATASET})
    payload = json.loads(_get_with_retries(url).decode("utf-8"))
    splits = payload.get("splits") or []
    if not splits:
        raise RuntimeError(f"no splits reported for {DATASET}: {payload!r}")

    candidates: list[tuple[int, str, str]] = []
    for entry in splits:
        config = str(entry.get("config") or "")
        split = str(entry.get("split") or "")
        if not config or not split:
            continue
        probe_url = f"{API_BASE}/rows?" + urllib.parse.urlencode({
            "dataset": DATASET, "config": config, "split": split, "offset": 0, "length": 1,
        })
        probe = json.loads(_get_with_retries(probe_url).decode("utf-8"))
        fields = {f["name"] for f in probe.get("features") or []}
        if "question_id" not in fields or "question" not in fields:
            continue  # not a question-shaped config (e.g. scraped_pages)
        candidates.append((int(probe.get("num_rows_total") or 0), config, split))

    if not candidates:
        raise RuntimeError(
            f"no question-shaped config/split found for {DATASET}; splits={splits!r}"
        )
    _, config, split = max(candidates)
    return config, split


def fetch_rows(config: str, split: str) -> list[dict]:
    """Page through /rows until exhausted (~1,417 rows expected)."""
    rows: list[dict] = []
    offset = 0
    while True:
        url = f"{API_BASE}/rows?" + urllib.parse.urlencode({
            "dataset": DATASET, "config": config, "split": split,
            "offset": offset, "length": PAGE_SIZE,
        })
        payload = json.loads(_get_with_retries(url).decode("utf-8"))
        page_rows = payload.get("rows") or []
        if not page_rows:
            break
        rows.extend(r.get("row") or {} for r in page_rows)
        offset += len(page_rows)
        total = payload.get("num_rows_total")
        if total is not None and offset >= int(total):
            break
        if len(page_rows) < PAGE_SIZE:
            break
    return rows


def load_raw_rows(refresh: bool) -> list[dict]:
    if not refresh and RAW_CACHE.exists():
        cached = json.loads(RAW_CACHE.read_text(encoding="utf-8"))
        print(f"using cached raw rows: {RAW_CACHE} ({len(cached)} rows)")
        return cached
    config, split = discover_split()
    print(f"discovered config={config!r} split={split!r}")
    rows = fetch_rows(config, split)
    if rows:
        print(f"fetched fields: {sorted(rows[0].keys())}")
    RAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
    RAW_CACHE.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"fetched {len(rows)} raw rows -> cached at {RAW_CACHE}")
    return rows


def normalize_resolution(value: Any) -> int | None:
    """Map a binary outcome field to 1/0. Anything unrecognized -> None (caller skips
    the row rather than guessing)."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int | float) and not isinstance(value, bool):
        if value == 1:
            return 1
        if value == 0:
            return 0
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("yes", "true", "1"):
            return 1
        if normalized in ("no", "false", "0"):
            return 0
    return None


def normalize_sota_probability(value: Any) -> float | None:
    """BTF-2's ``sota_forecast_probability`` is on a 0-100 scale (verified against the
    live API: e.g. 8.0, 28.0, 96.0 — never fractional-looking values in that range), so
    it is divided by 100 here to match the 0-1 convention ``bench/report.py`` and
    ``fetch_set.py``'s ``crowd.value`` already use. A value already in [0, 1] is passed
    through unchanged, in case a future schema revision fixes the scale upstream.
    Returns None if the value can't be read as a number or falls outside [0, 100].
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= number <= 1.0:
        return number
    if 0.0 <= number <= 100.0:
        return number / 100.0
    return None


def build_spec(row: dict) -> dict | None:
    """Transform one BTF-2 row into a run_bench.py-compatible set-spec dict, or None if
    the row is missing a required field.

    LEAK HYGIENE (critical): ``resolution_explanation`` and ``sota_summary_rationale``
    are NEVER stored anywhere in the returned spec. The ``resolution`` and ``crowd``
    fields exist only for offline scoring after the run — bench/run_bench.py's
    build_bench_brief only ever reads question/criteria/background/resolve_by to build
    the agent-facing prompt, so anything not stored under those four keys can never
    reach the agent. Keeping the two rationale fields out entirely (rather than trusting
    "just don't read that key") is the belt-and-braces version of the same guarantee.
    """
    question_id = row.get("question_id")
    question = row.get("question")
    criteria = row.get("resolution_criteria")
    research_summary = row.get("research_summary")
    present_date = row.get("present_date")
    sota_prob = normalize_sota_probability(row.get("sota_forecast_probability"))
    resolution = normalize_resolution(row.get("resolution"))

    if not question_id or not question or not criteria:
        return None
    if not research_summary or not str(research_summary).strip():
        return None
    if sota_prob is None:
        return None
    if resolution is None:
        return None

    background = str(row.get("background") or "").strip()
    dossier_header = f"## Frozen research dossier (compiled {present_date})"
    combined_background = (
        f"AS-OF DATE: {present_date} — forecast as if today were this date. The frozen "
        "research dossier below is the ONLY evidence available; web access is disabled "
        f"for this run.\n\n{background}\n\n{dossier_header}\n{research_summary}"
    )

    resolve_by = row.get("resolve_by") or row.get("resolution_date") or row.get("close_date")

    return {
        "id": f"btf2:{question_id}",
        "source": "btf2",
        "question": str(question),
        "criteria": str(criteria),
        "background": combined_background,
        # Structured copy of the AS-OF instant: run_bench --leakfree pins the timevault
        # cutoff to it (a regex fallback on the background line covers older set files).
        "as_of": str(present_date) if present_date else None,
        "resolve_by": str(resolve_by)[:10] if resolve_by else None,
        "crowd": {
            "value": sota_prob,
            "source": "btf2:futuresearch-sota (frozen; never shown to agent)",
            "at": present_date,
        },
        "resolution": resolution,
    }


def sample_rows(specs: list[dict], n: int, seed: int) -> list[dict]:
    """Deterministic sample: sort by id, shuffle with a seeded RNG, take the first n."""
    pool = sorted(specs, key=lambda s: s["id"])
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[:n]


def build_usable_specs(raw_rows: list[dict]) -> list[dict]:
    if raw_rows:
        missing = [f for f in REQUIRED_FIELDS if f not in raw_rows[0]]
        if missing:
            raise RuntimeError(
                f"BTF-2 schema is missing expected field(s) {missing}; "
                f"observed fields: {sorted(raw_rows[0].keys())}"
            )

    specs: list[dict] = []
    excluded: dict[str, int] = {
        "missing_core_fields": 0,
        "missing_research_summary": 0,
        "missing_sota_probability": 0,
        "unrecognized_resolution": 0,
    }
    for row in raw_rows:
        has_core = (
            row.get("question_id") and row.get("question") and row.get("resolution_criteria")
        )
        if not has_core:
            excluded["missing_core_fields"] += 1
            continue
        if not row.get("research_summary") or not str(row.get("research_summary")).strip():
            excluded["missing_research_summary"] += 1
            continue
        if normalize_sota_probability(row.get("sota_forecast_probability")) is None:
            excluded["missing_sota_probability"] += 1
            continue
        if normalize_resolution(row.get("resolution")) is None:
            excluded["unrecognized_resolution"] += 1
            continue
        spec = build_spec(row)
        if spec is not None:
            specs.append(spec)

    print(f"usable rows: {len(specs)} / {len(raw_rows)}")
    for reason, count in excluded.items():
        if count:
            print(f"  excluded ({reason}): {count}")
    return specs


def base_rate(specs: list[dict]) -> float:
    if not specs:
        return float("nan")
    return sum(s["resolution"] for s in specs) / len(specs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=200, help="questions to sample")
    parser.add_argument("--seed", type=int, default=42, help="deterministic sampling seed")
    parser.add_argument("--out", required=True)
    parser.add_argument("--refresh", action="store_true",
                        help="force re-fetch from HuggingFace instead of using the cache")
    args = parser.parse_args(argv)

    raw_rows = load_raw_rows(args.refresh)
    usable = build_usable_specs(raw_rows)
    if not usable:
        print("no usable rows found; aborting", file=sys.stderr)
        return 1

    print(f"full usable pool base rate (resolution==1): {base_rate(usable):.3f}")
    sampled = sample_rows(usable, args.n, args.seed)
    print(f"sample base rate (resolution==1): {base_rate(sampled):.3f} (n={len(sampled)})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for spec in sampled:
            fh.write(json.dumps(spec, ensure_ascii=False) + "\n")
    print(f"wrote {len(sampled)} questions -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
