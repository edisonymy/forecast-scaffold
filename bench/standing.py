#!/usr/bin/env python3
"""Where does the bot stand? Leaderboard rank per tournament, read-only.

The resolutions overlay knows our own score on every question but not which tournament a
question belongs to, nor what anyone else scored — so "+10 a question" cannot be told apart
from "rank 150". This asks the platform directly, for every bot tournament that started in
the last ``--since-days`` days:

  * the leaderboard: our rank, score, coverage, the leaders, the prize line, and where
    Metaculus's own single-model baseline bots (``metac-*``) rank — the cleanest public
    read on which underlying model is strongest right now;
  * the tournament's questions joined to the overlay: how many we never forecast, and our
    mean spot peer score by question type.

Needs ``METACULUS_TOKEN`` and egress to metaculus.com, so it runs in CI
(list-forecasts.yml), not in an authoring session.

Usage:
    python bench/standing.py                      # recent bot tournaments in the public list
    python bench/standing.py --slugs minibench-2026-11-02,minibench-2026-10-19
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.metaculus import MetaculusClient, MetaculusError  # noqa: E402

OVERLAY = ROOT / "bot" / "journal" / "resolutions.jsonl"
BOT_TOURNAMENT_RE = re.compile(r"(?i)minibench|futureeval|market.?pulse")
ALL_STATUSES = ["open", "closed", "resolved"]


def summarize(leaderboard: dict[str, Any], username: str, *, top: int = 5) -> dict[str, Any]:
    """One leaderboard reduced to the numbers a standing report needs.

    Entries without a user (aggregate rows such as the community prediction) are dropped;
    ``per_q`` divides by the entry's own contribution count, so it compares bots that
    answered different numbers of questions."""
    entries = sorted((e for e in leaderboard.get("entries") or [] if e.get("user")),
                     key=lambda e: e.get("rank") or 10**9)

    def row(entry: dict[str, Any]) -> dict[str, Any]:
        n = entry.get("contribution_count") or 0
        score = entry.get("score") or 0.0
        return {"username": entry["user"].get("username"), "rank": entry.get("rank"),
                "score": score, "n": n, "per_q": score / n if n else None,
                "coverage": entry.get("coverage"), "prize": entry.get("prize") or 0}

    rows = [row(e) for e in entries]
    paid = [r["score"] for r in rows if r["prize"] > 0]
    return {
        "participants": len(rows),
        "finalized": leaderboard.get("finalized"),
        "max_coverage": leaderboard.get("max_coverage"),
        "me": next((r for r in rows if r["username"] == username), None),
        "top": rows[:top],
        "prize_line": min(paid) if paid else None,
        "baselines": [r for r in rows if str(r["username"]).startswith("metac-")][:12],
    }


def recent_bot_tournaments(projects: list[dict[str, Any]], now: datetime,
                           since_days: int) -> list[dict[str, Any]]:
    """Bot tournaments that started inside the window, newest first."""
    floor = (now - timedelta(days=since_days)).isoformat()
    hits = [p for p in projects
            if BOT_TOURNAMENT_RE.search(f"{p.get('slug')} {p.get('name')}")
            and floor <= str(p.get("start_date") or "") <= now.isoformat()]
    return sorted(hits, key=lambda p: str(p.get("start_date")), reverse=True)


def load_overlay(path: Path) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[int(row["question_id"])] = row  # append-only file: latest wins
    return rows


def tournament_questions(client: MetaculusClient, slug: str) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = client._request("GET", "/posts/", params=[
            ("tournaments", slug), ("limit", 100), ("offset", offset), ("with_cp", "false"),
            *(("statuses", status) for status in ALL_STATUSES)]) or {}
        batch = page.get("results") or []
        for post in batch:
            questions.extend(MetaculusClient.questions_of(post))
        offset += len(batch)
        if not batch or not page.get("next"):
            return questions


def own_split(questions: list[dict[str, Any]], overlay: dict[int, dict[str, Any]],
              ) -> dict[str, Any]:
    """Coverage and per-type mean spot peer score of our own forecasts in one tournament."""
    by_type: dict[str, list[float]] = defaultdict(list)
    missed = 0
    for question in questions:
        ours = overlay.get(int(question["id"]))
        if ours is None:
            # A group's later windows are listed long before they open: only a question
            # that has already closed without a forecast of ours is a miss.
            missed += (question.get("status") in ("closed", "resolved")
                       and not (question.get("my_forecasts") or {}).get("latest"))
        elif ours.get("spot_peer_score") is not None:
            by_type[str(question.get("type"))].append(ours["spot_peer_score"])
    return {"questions": len(questions), "missed": missed,
            "by_type": {t: {"n": len(s), "mean": statistics.fmean(s), "sum": sum(s)}
                        for t, s in sorted(by_type.items())}}


def _line(row: dict[str, Any]) -> str:
    per_q = f"{row['per_q']:+.1f}/q" if row["per_q"] is not None else "n/a"
    return (f"#{row['rank']} {row['username']}  {row['score']:+.1f} over {row['n']} "
            f"({per_q})")


def report(slug: str, name: str, board: dict[str, Any] | None, split: dict[str, Any]) -> str:
    out = [f"\n=== {slug} — {name} ==="]
    if board:
        me = board["me"]
        out.append(f"participants {board['participants']}  finalized={board['finalized']}  "
                   f"max coverage {board['max_coverage']}  prize line {board['prize_line']}")
        out.append("US:   " + (f"{_line(me)}  coverage {me['coverage']}" if me
                               else "not on this leaderboard"))
        out += [f"top:  {_line(r)}" for r in board["top"]]
        out += [f"base: {_line(r)}" for r in board["baselines"]]
    out.append(f"questions {split['questions']}  never forecast by us {split['missed']}")
    out += [f"  {qtype:16s} n={s['n']:3d}  mean {s['mean']:+6.1f}  sum {s['sum']:+8.1f}"
            for qtype, s in split["by_type"].items()]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--slugs", default="",
                        help="comma-separated slugs to add to the discovered ones")
    parser.add_argument("--since-days", type=int, default=75)
    parser.add_argument("--overlay", type=Path, default=OVERLAY)
    args = parser.parse_args(argv)

    client = MetaculusClient()
    if not client.token:
        print("METACULUS_TOKEN not set", file=sys.stderr)
        return 1
    username = (client._request("GET", "/users/me/") or {}).get("username", "")
    projects = client.tournaments()
    chosen = recent_bot_tournaments(projects, datetime.now(UTC), args.since_days)
    for slug in (s.strip() for s in args.slugs.split(",")):
        if slug and slug not in {p.get("slug") for p in chosen}:
            try:  # not every project is in the public list (MiniBench waves are not)
                chosen.append(client._request("GET", f"/projects/tournaments/{slug}/"))
            except MetaculusError as exc:
                print(f"slug {slug!r} not found: {exc}")
    print(f"account {username!r}; {len(chosen)} tournament(s): "
          + ", ".join(str(p.get("slug")) for p in chosen))
    overlay = load_overlay(args.overlay)
    for project in chosen:
        slug, board = str(project.get("slug")), None
        try:
            boards = client._request("GET", f"/leaderboards/project/{project['id']}/") or []
            if boards:
                board = summarize(boards[0] if isinstance(boards, list) else boards, username)
            split = own_split(tournament_questions(client, slug), overlay)
        except MetaculusError as exc:
            print(f"\n=== {slug} === FAILED: {exc}")
            continue
        print(report(slug, str(project.get("name")), board, split))
    return 0


if __name__ == "__main__":
    sys.exit(main())
