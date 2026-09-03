"""Outage alarm for the Metaculus tournament bot.

On 2026-08-01 ``bot.yml`` silently produced nothing for a day and missed 6 questions —
the workflow's own "Alert on trouble" step only fires when a *step* fails, so a run that
reports success while quietly doing nothing (every question skipped, an early exit-0,
the GCP Cloud Scheduler kicker itself going dark) is invisible to it. This script is a
second, independent tripwire, run on its own hourly schedule rather than piggybacked on
``bot.yml``'s own dispatches, so it keeps checking even while the bot workflow is the
thing that's broken.

Two tripwires, either one raises the alarm:

  (a) no *successful* run of the tournament workflow within ``run_gap_hours`` — catches
      a broken workflow (expired token, dependency break, the external kicker going
      quiet) even before it shows up in the journal.
  (b) there are open tournament questions but the journal has gone quiet for more than
      ``silence_hours`` — catches a workflow that reports green while doing nothing.

Everything here is read-only: no network write, no journal write, no git action. The CLI
prints its reason and exits 1 to alarm, 0 otherwise, so a workflow step can gate an issue
on its exit code.

Usage: python scripts/journal_alarm.py --journal bot/journal/forecasts.jsonl \
    --tournaments minibench,summer-futureeval-2026
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
BASE_URL = "https://www.metaculus.com/api"
USER_AGENT = "forecast-scaffold-journal-alarm/0.1 (+https://github.com/edisonymy/forecast-scaffold)"


def newest_forecast_at(journal_path: str | Path) -> datetime | None:
    """Latest ``forecast_at`` across Metaculus rows in the journal, or None.

    Tolerates a missing file and malformed/partial lines (a crash mid-append should
    never make the alarm itself blow up) by simply skipping them.
    """
    newest: datetime | None = None
    try:
        with Path(journal_path).open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                source = row.get("source")
                if not isinstance(source, dict) or source.get("platform") != "metaculus":
                    continue
                stamp = row.get("forecast_at")
                if not isinstance(stamp, str):
                    continue
                try:
                    when = datetime.fromisoformat(stamp)
                except ValueError:
                    continue
                if newest is None or when > newest:
                    newest = when
    except OSError:
        return None
    return newest


def open_question_count(slugs: list[str]) -> int:
    """Number of open questions across the given tournament slugs.

    Hits the public ``/posts/`` endpoint (no token required); a ``METACULUS_TOKEN`` in
    the environment is sent along if present, but nothing here depends on it. Any
    network or parsing error returns -1 ("unknown") rather than raising — this alarm
    must survive a flaky or renamed API just as well as it survives a real outage.
    """
    token = os.environ.get("METACULUS_TOKEN", "")
    total = 0
    try:
        for raw_slug in slugs:
            slug = raw_slug.strip()
            if not slug:
                continue
            query = urllib.parse.urlencode(
                {"tournaments": slug, "statuses": "open", "limit": 100}
            )
            request = urllib.request.Request(f"{BASE_URL}/posts/?{query}")
            request.add_header("User-Agent", USER_AGENT)
            if token:
                request.add_header("Authorization", f"Token {token}")
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            for post in payload.get("results") or []:
                if post.get("question"):
                    questions = [post["question"]]
                else:
                    group = post.get("group_of_questions") or {}
                    questions = group.get("questions") or []
                total += sum(
                    1 for q in questions if isinstance(q, dict) and q.get("status") == "open"
                )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
        return -1
    return total


def last_successful_run_age_hours(workflow: str = "bot.yml") -> float | None:
    """Hours since the last successful run of ``workflow``, via ``gh run list``.

    None whenever this can't be answered confidently — ``gh`` missing/unauthenticated,
    a nonzero exit, unparsable JSON, or zero successful runs on record — so callers fall
    back to the journal-silence tripwire instead of alarming on a tooling hiccup.
    """
    try:
        result = subprocess.run(
            [
                "gh", "run", "list",
                f"--workflow={workflow}",
                "--status", "success",
                "--limit", "1",
                "--json", "createdAt",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    stamp = rows[0].get("createdAt") if isinstance(rows[0], dict) else None
    if not isinstance(stamp, str):
        return None
    try:
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(UTC) - created).total_seconds() / 3600.0)


def evaluate(
    newest_at: datetime | None,
    open_count: int,
    run_age_h: float | None,
    now: datetime,
    *,
    silence_hours: float = 6.0,
    run_gap_hours: float = 2.0,
) -> tuple[bool, str]:
    """Decide whether the bot looks broken, and why.

    ``open_count == -1`` means the Metaculus check itself failed ("unknown") — in that
    case only the run-age tripwire (a) can raise the alarm; a failed API probe must
    never be treated as "zero open questions, all quiet".
    """
    if run_age_h is not None and run_age_h > run_gap_hours:
        return True, f"no successful bot run for {run_age_h:.1f}h"

    if open_count != -1 and open_count > 0:
        if newest_at is None:
            return True, f"{open_count} open question(s) but no journal row ever"
        silence_h = (now - newest_at).total_seconds() / 3600.0
        if silence_h > silence_hours:
            return (
                True,
                f"{open_count} open question(s) but no journal row for {silence_h:.1f}h",
            )

    run_desc = "unknown" if run_age_h is None else f"{run_age_h:.1f}h ago"
    open_desc = "unknown" if open_count == -1 else str(open_count)
    return False, f"ok: last successful run {run_desc}, {open_desc} open question(s)"


def _default_tournaments() -> list[str]:
    tournament_id = os.environ.get("TOURNAMENT_ID", "")
    extra = os.environ.get("EXTRA_TOURNAMENTS", "")
    raw = f"{tournament_id},{extra},minibench"
    slugs: dict[str, None] = {}
    for slug in raw.split(","):
        slug = slug.strip()
        if slug:
            slugs.setdefault(slug, None)
    return list(slugs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument(
        "--tournaments",
        default=None,
        help="Comma-separated slugs (default: $TOURNAMENT_ID,$EXTRA_TOURNAMENTS plus minibench)",
    )
    parser.add_argument("--workflow", default="bot.yml")
    parser.add_argument("--silence-hours", type=float, default=6.0)
    parser.add_argument("--run-gap-hours", type=float, default=2.0)
    args = parser.parse_args(argv)

    slugs = (
        [s.strip() for s in args.tournaments.split(",") if s.strip()]
        if args.tournaments is not None
        else _default_tournaments()
    )

    newest_at = newest_forecast_at(args.journal)
    open_count = open_question_count(slugs)
    run_age_h = last_successful_run_age_hours(args.workflow)

    alarm, reason = evaluate(
        newest_at,
        open_count,
        run_age_h,
        datetime.now(UTC),
        silence_hours=args.silence_hours,
        run_gap_hours=args.run_gap_hours,
    )
    print(reason)
    return 1 if alarm else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
