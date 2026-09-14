#!/usr/bin/env python3
"""Dump every forecast this bot's Metaculus account holds — read-only, no submits.

Runs in CI, where ``METACULUS_TOKEN`` and egress to metaculus.com both exist (the
authoring agent session can reach neither). Prints one ``FORECAST {json}`` line per
question the account has a standing forecast on, with the community prediction and the
resolution alongside, so the full track record can be reconstructed off-platform.

Strategy: ask the API who we are, then list every post that user id has forecasted on
(any tournament, any status) via the ``forecaster_id`` filter; additionally sweep any
tournament slugs passed in ``TOURNAMENTS`` and union the results. De-duped by question id.
``my_forecasts`` rides along on authenticated ``/posts/`` list responses (the same field
the bot's already-forecasted filter reads), so no per-post detail fetch is needed.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlencode

from metaculus import MetaculusClient, MetaculusError

ALL_STATUSES = ["open", "closed", "resolved", "upcoming", "pending"]


def _q(path: str, **params: object) -> str:
    """Build a query string with repeated keys for list values (statuses=a&statuses=b)."""
    pairs: list[tuple[str, object]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((key, item) for item in value)
        else:
            pairs.append((key, value))
    return f"{path}?{urlencode(pairs)}"


def whoami(client: MetaculusClient) -> dict | None:
    for path in ("/users/me/", "/users/me"):
        try:
            user = client._request("GET", path)
        except MetaculusError:
            continue
        if user and user.get("id"):
            return user
    return None


def _paginate(client: MetaculusClient, base: str, key: str, value: object,
              limit: int = 2000) -> list[dict]:
    results: list[dict] = []
    offset = 0
    while len(results) < limit:
        page = client._request("GET", _q(
            base, **{key: value}, statuses=ALL_STATUSES,
            limit=min(100, limit - len(results)), offset=offset, with_cp="true",
        ))
        batch = page.get("results", []) if page else []
        if not batch:
            break
        results.extend(batch)
        offset += len(batch)
        if not (page or {}).get("next"):
            break
    return results


def _extract_forecast(question: dict) -> dict | None:
    latest = (question.get("my_forecasts") or {}).get("latest") or {}
    if not latest:
        return None
    return {
        "at": latest.get("start_time") or latest.get("created_at"),
        "forecast_values": latest.get("forecast_values"),
        "centers": latest.get("centers"),
        "slider_values": latest.get("slider_values"),
    }


def main() -> int:
    client = MetaculusClient()
    if not client.token:
        print("NO_TOKEN: METACULUS_TOKEN not set")
        return 1

    user = whoami(client)
    uid = user.get("id") if user else None
    print(f"# account: id={uid} username={(user or {}).get('username')!r}")

    posts: list[dict] = []
    seen_posts: set = set()

    if uid is not None:
        try:
            by_me = _paginate(client, "/posts/", "forecaster_id", uid)
            print(f"# forecaster_id sweep -> {len(by_me)} posts")
            for post in by_me:
                if post.get("id") not in seen_posts:
                    posts.append(post)
                    seen_posts.add(post.get("id"))
        except MetaculusError as exc:
            print(f"# forecaster_id sweep FAILED: {exc}")

    slugs = [s.strip() for s in os.environ.get("TOURNAMENTS", "").split(",") if s.strip()]
    for slug in slugs:
        try:
            in_tourn = _paginate(client, "/posts/", "tournaments", slug)
            added = sum(1 for p in in_tourn if p.get("id") not in seen_posts)
            print(f"# tournament {slug!r} -> {len(in_tourn)} posts ({added} new)")
            for post in in_tourn:
                if post.get("id") not in seen_posts:
                    posts.append(post)
                    seen_posts.add(post.get("id"))
        except MetaculusError as exc:
            print(f"# tournament {slug!r} FAILED: {exc}")

    rows: list[dict] = []
    seen_q: set = set()
    for post in posts:
        pid = post.get("id")
        for question in MetaculusClient.questions_of(post):
            forecast = _extract_forecast(question)
            if not forecast:
                continue
            qid = question.get("id")
            if qid in seen_q:
                continue
            seen_q.add(qid)
            rows.append({
                "post_id": pid,
                "question_id": qid,
                "title": question.get("title") or post.get("title"),
                "type": question.get("type"),
                "url": f"https://www.metaculus.com/questions/{pid}/",
                "my_forecast": forecast,
                "community_prediction": MetaculusClient.community_prediction(question),
                "resolution": question.get("resolution"),
                "status": question.get("status") or post.get("status"),
                "close_time": question.get("actual_close_time")
                or question.get("scheduled_close_time"),
            })

    for row in rows:
        print("FORECAST " + json.dumps(row, ensure_ascii=False))
    print(f"# TOTAL {len(rows)} forecasts across {len(posts)} posts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
