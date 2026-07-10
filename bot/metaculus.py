"""Minimal stdlib Metaculus API client for the tournament bot.

Endpoints and payload shapes follow the Metaculus API as documented publicly and used by
the official bot template (July 2026): list posts with a tournament filter, fetch post
details, submit forecasts per question type, post comments. Auth is a
``Authorization: Token <METACULUS_TOKEN>`` header. Response shapes shift occasionally —
everything here reads defensively and this file is the only place that knows the API.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = "https://www.metaculus.com/api"


class MetaculusError(RuntimeError):
    pass


class MetaculusClient:
    def __init__(self, token: str | None = None, base_url: str = BASE_URL) -> None:
        self.token = token or os.environ.get("METACULUS_TOKEN", "")
        self.base_url = base_url.rstrip("/")

    # -- transport ---------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        # Cloudflare rejects urllib's default user agent (error 1010).
        request.add_header(
            "User-Agent",
            "forecast-scaffold-bot/0.1 (+https://github.com/edisonymy/forecast-scaffold)",
        )
        if self.token:
            request.add_header("Authorization", f"Token {self.token}")
        # Transient-fault retry: GETs are idempotent and forecast submission is
        # latest-wins per question, so both retry on 429/5xx/network blips — an hourly
        # unattended cron cannot afford one blip failing a question (which would also
        # trigger the workflow's paid-fallback rerun). Comment creation is NOT retried:
        # duplicating a public comment is worse than dropping a private one.
        retriable = method == "GET" or path == "/questions/forecast/"
        attempts = 3 if retriable else 1
        payload = ""
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                # 520/522/524 are Cloudflare origin blips — as transient as a 502 here.
                if attempt + 1 < attempts and exc.code in (429, 500, 502, 503, 504,
                                                           520, 522, 524):
                    delay = 2.0 * (attempt + 1)
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    if retry_after:
                        # Honor a parseable Retry-After (429s often carry one), capped —
                        # an hourly cron would rather fail than sleep for minutes.
                        with contextlib.suppress(ValueError):
                            delay = min(float(retry_after), 30.0)
                    time.sleep(delay)
                    continue
                raise MetaculusError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt + 1 < attempts:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise MetaculusError(f"{method} {path} -> {exc.reason}") from exc
        return json.loads(payload) if payload else None

    # -- reads -------------------------------------------------------------
    def open_posts(self, tournament: str | int, *, limit: int = 100) -> list[dict[str, Any]]:
        """Open posts in a tournament, with their nested question payloads.

        Follows pagination up to ``limit`` total: the already-forecasted filter runs
        client-side AFTER this fetch, so stopping at one page would silently hide new
        wave questions whenever more than a pageful of posts is open."""
        results: list[dict[str, Any]] = []
        while len(results) < limit:
            page = self._request(
                "GET",
                "/posts/",
                params={
                    "tournaments": tournament,
                    "statuses": "open",
                    "limit": min(limit - len(results), 100),
                    "offset": len(results),
                    "with_cp": "true",
                },
            )
            batch: list[dict[str, Any]] = page.get("results", []) if page else []
            results.extend(batch)
            if not batch or not (page or {}).get("next"):
                break
        return results[:limit]

    def post_detail(self, post_id: int) -> dict[str, Any]:
        detail: dict[str, Any] = self._request(
            "GET", f"/posts/{post_id}/", params={"with_cp": "true"}
        )
        return detail

    @staticmethod
    def questions_of(post: dict[str, Any]) -> list[dict[str, Any]]:
        """A post carries one question, or several for a group post."""
        if post.get("question"):
            return [post["question"]]
        group = post.get("group_of_questions") or {}
        questions: list[dict[str, Any]] = group.get("questions", [])
        return questions

    @staticmethod
    def community_prediction(question: dict[str, Any]) -> float | None:
        """The recency-weighted community center for a binary question, if visible."""
        try:
            # "latest" is null when a question has no forecasts yet.
            latest = question["aggregations"]["recency_weighted"]["latest"] or {}
            centers = latest.get("centers") or latest.get("forecast_values")
            value = centers[0] if centers else None
            return float(value) if value is not None else None
        except (KeyError, TypeError, IndexError, ValueError, AttributeError):
            return None

    @staticmethod
    def already_forecasted(question: dict[str, Any]) -> bool:
        my = question.get("my_forecasts") or {}
        return bool(my.get("latest"))

    @staticmethod
    def my_forecast_age_hours(
        question: dict[str, Any], now: float | None = None
    ) -> float | None:
        """Hours since this account's latest forecast on the question; None when never
        forecast — or when the API omits the timestamp, which fails safe for the
        refresh gate: an unknown age is never 'stale enough' to re-spend on."""
        latest = (question.get("my_forecasts") or {}).get("latest") or {}
        stamp = latest.get("start_time") or latest.get("created_at")
        try:
            then = float(stamp)
        except (TypeError, ValueError):
            return None
        now = time.time() if now is None else now
        return max(0.0, (now - then) / 3600.0)

    # -- writes ------------------------------------------------------------
    def submit_binary(self, question_id: int, probability: float) -> None:
        self._submit(question_id, probability_yes=probability)

    def submit_multiple_choice(self, question_id: int, by_option: dict[str, float]) -> None:
        self._submit(question_id, probability_yes_per_category=by_option)

    def submit_cdf(self, question_id: int, cdf: list[float]) -> None:
        self._submit(question_id, continuous_cdf=cdf)

    def _submit(self, question_id: int, **payload: Any) -> None:
        body = [
            {
                "question": question_id,
                "source": "api",
                "probability_yes": payload.get("probability_yes"),
                "probability_yes_per_category": payload.get("probability_yes_per_category"),
                "continuous_cdf": payload.get("continuous_cdf"),
            }
        ]
        self._request("POST", "/questions/forecast/", body=body)

    def comment(self, post_id: int, text: str, *, private: bool = True) -> None:
        self._request(
            "POST",
            "/comments/create/",
            body={
                "text": text,
                "on_post": post_id,
                "is_private": private,
                "included_forecast": True,
            },
        )
