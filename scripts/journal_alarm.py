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
  (b) an open tournament question still has NO forecast from this bot account
      ``grace_minutes`` (6h) after it opened, or within ``imminent_minutes`` of closing —
      catches a workflow that reports green while doing nothing (2026-09-21: 2.5h of
      green ticks, 0 forecasts, questions missed).
      [CHANGED 2026-09-22] This used to be "open questions + journal silent for N hours",
      which never fired (anonymous reads 403 -> "unknown") and, once authenticated,
      would have fired all day: standing forecasts make journal quiet normal.

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


#: open_question_count's verdict when Metaculus rejects the token (401/403). The bot uses the
#: same token, so it is blind too — a total outage that must alarm, not read as "unknown".
AUTH_REJECTED = -2


def open_question_count(
    slugs: list[str],
    *,
    grace_minutes: float = 360.0,
    imminent_minutes: float = 90.0,
    now: datetime | None = None,
) -> int:
    """Open questions this bot account has NOT forecast that look like a coverage failure:
    closing within ``imminent_minutes`` (about to be missed), or open for more than
    ``grace_minutes`` (a healthy bot works through even a big wave well inside that).

    Needs the bot's ``METACULUS_TOKEN`` (Metaculus 403s anonymous ``/posts/`` reads) and
    ``with_cp=true`` — without it the API omits ``my_forecasts`` and EVERY question would
    look unforecast. A question with no parsable ``open_time`` counts as old (fail loud).
    Each slug is isolated: a slug that does not exist yet (HTTP 400/404, e.g. a
    pre-entered next-quarter round) is skipped; any other failure makes that slug
    unknown. Returns -1 ("unknown") only when no slug could be read at all.
    """
    now = now or datetime.now(UTC)
    token = os.environ.get("METACULUS_TOKEN", "")
    total = 0
    read_any = False
    for raw_slug in slugs:
        slug = raw_slug.strip()
        if not slug:
            continue
        query = urllib.parse.urlencode(
            {"tournaments": slug, "statuses": "open", "limit": 100, "with_cp": "true"}
        )
        request = urllib.request.Request(f"{BASE_URL}/posts/?{query}")
        request.add_header("User-Agent", USER_AGENT)
        if token:
            request.add_header("Authorization", f"Token {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            count = 0
            for post in payload.get("results") or []:
                if post.get("question"):
                    questions = [post["question"]]
                else:
                    group = post.get("group_of_questions") or {}
                    questions = group.get("questions") or []
                count += sum(
                    1 for q in questions
                    if isinstance(q, dict) and q.get("status") == "open"
                    and not (q.get("my_forecasts") or {}).get("latest")
                    and (_opened_minutes_ago(q, now) > grace_minutes
                         or _closes_in_minutes(q, post, now) < imminent_minutes)
                )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return AUTH_REJECTED  # a dead token blinds the bot too: alarm, never "unknown"
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError,
                TypeError, AttributeError):
            # HTTPError included: a 400/404 slug that does not exist yet has nothing to
            # cover, and any other failure leaves just this slug unknown.
            continue
        read_any = True
        total += count
    return total if read_any else -1


def _closes_in_minutes(question: dict, post: dict, now: datetime) -> float:
    stamp = question.get("scheduled_close_time") or post.get("scheduled_close_time")
    try:
        closes = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if closes.tzinfo is None:
        closes = closes.replace(tzinfo=UTC)
    return (closes - now).total_seconds() / 60.0


def _opened_minutes_ago(question: dict, now: datetime) -> float:
    stamp = question.get("open_time")
    try:
        opened = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=UTC)
    return (now - opened).total_seconds() / 60.0


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
                "--json", "updatedAt",
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
    # updatedAt = completion time: a long (85-min) tick started 2h ago is not an outage.
    stamp = rows[0].get("updatedAt") if isinstance(rows[0], dict) else None
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
    silence_hours: float = 3.0,
    run_gap_hours: float = 3.0,
) -> tuple[bool, str]:
    """Decide whether the bot looks broken, and why. ``newest_at``/``silence_hours`` are
    kept for the report line only; coverage is judged by ``open_count`` (see
    open_question_count).

    ``open_count == -1`` means the Metaculus check itself failed ("unknown") — in that
    case only the run-age tripwire (a) can raise the alarm; a failed API probe must
    never be treated as "zero open questions, all quiet".
    """
    if run_age_h is not None and run_age_h > run_gap_hours:
        return True, f"no successful bot run for {run_age_h:.1f}h"

    if open_count == AUTH_REJECTED:
        return True, "Metaculus rejects METACULUS_TOKEN (401/403): the bot cannot see questions"
    if open_count != -1 and open_count > 0:
        # open_count already means "open, never forecast, past the grace window" — any
        # such question is a coverage failure whatever the journal says.
        return True, f"{open_count} open question(s) still have no forecast from the bot"

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
    parser.add_argument("--silence-hours", type=float, default=3.0)
    parser.add_argument("--grace-minutes", type=float, default=360.0)
    parser.add_argument("--imminent-minutes", type=float, default=90.0)
    parser.add_argument("--run-gap-hours", type=float, default=3.0)
    args = parser.parse_args(argv)

    slugs = (
        [s.strip() for s in args.tournaments.split(",") if s.strip()]
        if args.tournaments is not None
        else _default_tournaments()
    )

    newest_at = newest_forecast_at(args.journal)
    open_count = open_question_count(
        slugs, grace_minutes=args.grace_minutes, imminent_minutes=args.imminent_minutes
    )
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
