"""Capture a finished/running MiniBench wave from the Metaculus API into this repo's
analysis formats.

Pages through every post in a MiniBench tournament project (any status -- a wave is often
still mid-resolution when this runs), fetches full per-question detail via the POST id from
that listing -- never the question id, since a group post's question id is not its post id
and fetching by qid silently returns the WRONG post -- and joins the result against our own
forecast journal (bot/journal/forecasts.jsonl) to mark which questions we actually
forecast. Writes two files consumed by bench/analysis/*.py:

- ``<out-dir>/minibench-<slug-date>-census.json``: one entry per question with everything
  needed for post-hoc scoring/diagnostics (scaling, our forecast, Metaculus score_data).
- ``<out-dir>/minibench-<slug-date>-resolutions.json``: the scorers' ``{qid: outcome}``
  format (see bench/analysis/minibench_counterfactuals.py), skipping null/annulled/
  ambiguous resolutions.

Usage:
    python bench/fetch_minibench_wave.py --tournament minibench-2026-07-27
    python bench/fetch_minibench_wave.py --tournament minibench-2026-07-27 \
        --journal bot/journal/forecasts.jsonl --out-dir bench/analysis
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.metaculus import MetaculusClient  # noqa: E402

DEFAULT_JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
DEFAULT_OUT_DIR = ROOT / "bench" / "analysis"
DETAIL_SLEEP_SECONDS = 0.3
SCORE_DATA_KEYS = (
    "peer_score", "spot_peer_score", "baseline_score", "spot_baseline_score", "coverage",
)
SKIP_RESOLUTIONS = {None, "", "annulled", "ambiguous"}


def fetch_all_posts(client: MetaculusClient, tournament: str) -> list[dict[str, Any]]:
    """Every post in ``tournament``, any status.

    The listing's embedded question payload is stale/incomplete (no resolution, no
    score data even for resolved questions) -- it is used only to enumerate post ids for
    the per-post detail fetches below.
    """
    posts: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = client._request(
            "GET", "/posts/",
            params={"tournaments": tournament, "limit": 100, "offset": offset},
        )
        batch: list[dict[str, Any]] = (page or {}).get("results", [])
        posts.extend(batch)
        offset += len(batch)
        if not batch or not (page or {}).get("next"):
            break
    return posts


def project_slugs(detail: dict[str, Any]) -> list[str]:
    """Tournament-ish project slugs for a post.

    MiniBench waves are modeled as a ``question_series`` project, not a ``tournament``
    one, so both keys are checked; ``default_project`` is included too since it is the
    only tournament-like project on some posts. Deduplicated and sorted.
    """
    projects = detail.get("projects") or {}
    slugs: set[str] = set()
    for key in ("tournament", "question_series"):
        for project in projects.get(key) or []:
            slug = project.get("slug") or project.get("name")
            if slug:
                slugs.add(str(slug))
    default = projects.get("default_project") or {}
    default_slug = default.get("slug") or default.get("name")
    if default_slug:
        slugs.add(str(default_slug))
    return sorted(slugs)


def load_journal(journal_path: Path) -> dict[int, dict[str, Any]]:
    """The latest journal row per Metaculus question id (by ``forecast_at``)."""
    latest: dict[int, dict[str, Any]] = {}
    if not journal_path.exists():
        return latest
    for line in journal_path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        qid = (row.get("source") or {}).get("question_id")
        if qid is None:
            continue
        existing = latest.get(qid)
        if existing is None or str(row.get("forecast_at") or "") >= str(
            existing.get("forecast_at") or ""
        ):
            latest[qid] = row
    return latest


def our_forecast(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """The probability/percentiles/etc. we actually submitted, per the journal row."""
    if row is None:
        return None
    out: dict[str, Any] = {
        "forecast_at": row.get("forecast_at"),
        "question_type": row.get("question_type"),
    }
    for key in ("probability", "percentiles", "probabilities", "options"):
        if key in row:
            out[key] = row[key]
    return out


def census_entry(
    question: dict[str, Any],
    post_id: int,
    slugs: list[str],
    journal_row: dict[str, Any] | None,
) -> dict[str, Any]:
    scaling_raw = question.get("scaling") or {}
    scaling: dict[str, Any] = {
        "range_min": scaling_raw.get("range_min"),
        "range_max": scaling_raw.get("range_max"),
        "zero_point": scaling_raw.get("zero_point"),
        "open_lower": question.get("open_lower_bound"),
        "open_upper": question.get("open_upper_bound"),
    }
    if "cdf_size" in scaling_raw:
        scaling["cdf_size"] = scaling_raw["cdf_size"]

    my = question.get("my_forecasts") or {}
    my_latest_raw = my.get("latest") or {}
    my_latest = (
        {"start_time": my_latest_raw.get("start_time"),
         "forecast_values": my_latest_raw.get("forecast_values")}
        if my_latest_raw else None
    )
    score_data_raw = my.get("score_data") or {}
    score_data = (
        {key: score_data_raw.get(key) for key in SCORE_DATA_KEYS}
        if score_data_raw else None
    )

    return {
        "qid": question.get("id"),
        "post_id": post_id,
        "title": question.get("title"),
        "type": question.get("type"),
        "status": question.get("status"),
        "resolution": question.get("resolution"),
        "actual_resolve_time": question.get("actual_resolve_time"),
        "scheduled_close_time": question.get("scheduled_close_time"),
        "projects": slugs,
        "scaling": scaling,
        "options": question.get("options"),
        "my_forecasts": {"latest": my_latest, "score_data": score_data},
        "forecast": journal_row is not None,
        "our": our_forecast(journal_row),
    }


def resolution_outcome(entry: dict[str, Any]) -> tuple[str, Any] | None:
    """``(qid_str, outcome)`` in the scorers' format, or None to skip this entry."""
    resolution = entry.get("resolution")
    if resolution in SKIP_RESOLUTIONS:
        return None
    qid_str = str(entry["qid"])
    qtype = entry.get("type")
    if qtype == "binary":
        if resolution not in ("yes", "no"):
            return None
        return qid_str, 1 if resolution == "yes" else 0
    if qtype in ("numeric", "discrete"):
        scaling = entry.get("scaling") or {}
        if resolution == "below_lower_bound":
            lo = scaling.get("range_min")
            return None if lo is None else (qid_str, lo - max(1.0, abs(lo) * 0.01))
        if resolution == "above_upper_bound":
            hi = scaling.get("range_max")
            return None if hi is None else (qid_str, hi + max(1.0, abs(hi) * 0.01))
        try:
            return qid_str, float(resolution)
        except (TypeError, ValueError):
            return None
    if qtype == "multiple_choice":
        return qid_str, str(resolution)
    return None


def slug_date(tournament: str) -> str:
    prefix = "minibench-"
    if tournament.startswith(prefix):
        return tournament[len(prefix):]
    return tournament


def build_census(
    client: MetaculusClient, tournament: str, journal: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    posts = fetch_all_posts(client, tournament)
    print(f"{tournament}: {len(posts)} posts")
    census: list[dict[str, Any]] = []
    for i, post in enumerate(posts):
        post_id = post["id"]
        detail = client.post_detail(post_id)
        slugs = project_slugs(detail)
        for question in MetaculusClient.questions_of(detail):
            qid = question.get("id")
            census.append(census_entry(question, post_id, slugs, journal.get(qid)))
        if (i + 1) % 10 == 0 or i + 1 == len(posts):
            print(f"  fetched {i + 1}/{len(posts)} posts")
        time.sleep(DETAIL_SLEEP_SECONDS)
    return census


def print_summary(census: list[dict[str, Any]]) -> None:
    statuses = Counter(e.get("status") for e in census)
    forecasted = [e for e in census if e.get("forecast")]
    missed = [e for e in census if not e.get("forecast")]
    scored = [e for e in census if (e.get("my_forecasts") or {}).get("score_data")]
    total_spot_peer = sum(
        e["my_forecasts"]["score_data"].get("spot_peer_score") or 0.0 for e in scored
    )
    print(f"\nquestions: {len(census)}")
    print(f"statuses: {dict(statuses)}")
    print(f"our coverage: forecast on {len(forecasted)} of {len(census)}")
    if missed:
        print(f"never forecast ({len(missed)}):")
        for e in missed:
            print(f"  qid {e['qid']} [{e.get('status')}] {str(e.get('title'))[:90]}")
    print(f"with score_data: {len(scored)}")
    print(f"total spot_peer_score over scored entries: {total_spot_peer:.2f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tournament", required=True,
                        help="Metaculus tournament/project slug, e.g. minibench-2026-07-27")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    client = MetaculusClient()
    journal = load_journal(args.journal)
    census = build_census(client, args.tournament, journal)

    date = slug_date(args.tournament)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    census_path = args.out_dir / f"minibench-{date}-census.json"
    resolutions_path = args.out_dir / f"minibench-{date}-resolutions.json"

    census_path.write_text(json.dumps(census, indent=1), encoding="utf-8")
    print(f"wrote {len(census)} entries -> {census_path}")

    resolutions: dict[str, Any] = {}
    for entry in census:
        outcome = resolution_outcome(entry)
        if outcome is not None:
            resolutions[outcome[0]] = outcome[1]
    resolutions_path.write_text(json.dumps(resolutions, indent=1), encoding="utf-8")
    print(f"wrote {len(resolutions)} resolutions -> {resolutions_path}")

    print_summary(census)
    return 0


if __name__ == "__main__":
    sys.exit(main())
