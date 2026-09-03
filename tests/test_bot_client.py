"""Unit tests for the stdlib Metaculus client's transport behavior (all HTTP mocked)."""

from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

import metaculus  # noqa: E402
import run_bot  # noqa: E402
from metaculus import MetaculusClient  # noqa: E402


class TestOpenPostsPagination:
    """The already-forecasted filter runs client-side AFTER the fetch: stopping at one
    page would silently hide new wave questions once more than a pageful is open."""

    def test_follows_next_until_limit(self) -> None:
        client = MetaculusClient(token="t")
        calls: list[dict[str, Any]] = []

        def fake_request(method: str, path: str, *, params: Any = None,
                         body: Any = None) -> Any:
            calls.append(dict(params))
            batch = [{"id": params["offset"] + i} for i in range(params["limit"])]
            return {"results": batch, "next": "cursor"}

        client._request = fake_request  # type: ignore[method-assign]
        posts = client.open_posts("tourn", limit=150)
        assert len(posts) == 150
        assert [c["offset"] for c in calls] == [0, 100]
        assert [c["limit"] for c in calls] == [100, 50]

    def test_stops_when_api_reports_no_next_page(self) -> None:
        client = MetaculusClient(token="t")
        client._request = (  # type: ignore[method-assign]
            lambda *a, **k: {"results": [{"id": 1}, {"id": 2}], "next": None}
        )
        assert len(client.open_posts("tourn", limit=100)) == 2

    def test_stops_on_empty_batch(self) -> None:
        client = MetaculusClient(token="t")
        client._request = (  # type: ignore[method-assign]
            lambda *a, **k: {"results": [], "next": "cursor"}
        )
        assert client.open_posts("tourn") == []


class TestCollectOpenPosts:
    """--tournament may be a comma-separated list (season + MiniBench); the union must
    dedupe cross-listed posts and leave single-slug behaviour unchanged."""

    @staticmethod
    def _client(by_slug: dict[str, list[dict[str, Any]]]) -> MetaculusClient:
        client = MetaculusClient(token="t")
        client.open_posts = (  # type: ignore[method-assign]
            lambda slug, *, limit=100: list(by_slug.get(slug, []))
        )
        return client

    def test_single_slug_unchanged(self) -> None:
        client = self._client({"season": [{"id": 1}, {"id": 2}]})
        assert run_bot.collect_open_posts(client, "season", 100) == [{"id": 1}, {"id": 2}]

    def test_union_dedupes_cross_listed_posts(self) -> None:
        client = self._client({
            "season": [{"id": 1}, {"id": 2}],
            "minibench": [{"id": 2}, {"id": 3}],  # id 2 is cross-listed
        })
        posts = run_bot.collect_open_posts(client, "season, minibench", 100)
        assert [p["id"] for p in posts] == [1, 2, 3]

    def test_blank_slugs_ignored(self) -> None:
        client = self._client({"season": [{"id": 1}]})
        assert run_bot.collect_open_posts(client, "season,,  ", 100) == [{"id": 1}]

    def test_unknown_slug_is_isolated_not_fatal(self, capsys: Any) -> None:
        # A pre-entered next-quarter round names its slug before Metaculus creates the
        # tournament; that slug erroring must cost only its own batch, never the run.
        client = MetaculusClient(token="t")

        def open_posts(slug: str, *, limit: int = 100) -> list[dict[str, Any]]:
            if slug == "market-pulse-26q4":
                raise RuntimeError("404 tournament not found")
            return [{"id": 1}]

        client.open_posts = open_posts  # type: ignore[method-assign]
        posts = run_bot.collect_open_posts(client, "season,market-pulse-26q4", 100)
        assert [p["id"] for p in posts] == [1]
        out = capsys.readouterr().out
        assert "market-pulse-26q4" in out and "skipping this slug" in out


class TestTransientRetry:
    def test_retry_after_header_is_honored_and_capped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A 429 whose Retry-After exceeds the old fixed backoff used to exhaust all
        # attempts pointlessly; a huge one must not stall the hourly cron either.
        sleeps: list[float] = []
        monkeypatch.setattr(metaculus.time, "sleep", lambda s: sleeps.append(s))
        attempts = {"n": 0}

        class FakeResponse:
            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *args: Any) -> bool:
                return False

            def read(self) -> bytes:
                return b'{"ok": true}'

        def fake_urlopen(request: Any, timeout: int = 60) -> FakeResponse:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise urllib.error.HTTPError(
                    "url", 429, "too many", {"Retry-After": "120"}, io.BytesIO(b""))
            return FakeResponse()

        monkeypatch.setattr(metaculus.urllib.request, "urlopen", fake_urlopen)
        client = MetaculusClient(token="t")
        assert client._request("GET", "/posts/") == {"ok": True}
        assert sleeps == [30.0, 30.0]  # honored but capped

    def test_cloudflare_origin_errors_are_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(metaculus.time, "sleep", lambda s: None)
        attempts = {"n": 0}

        class FakeResponse:
            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *args: Any) -> bool:
                return False

            def read(self) -> bytes:
                return b"{}"

        def fake_urlopen(request: Any, timeout: int = 60) -> FakeResponse:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise urllib.error.HTTPError("url", 522, "origin timeout", {},
                                             io.BytesIO(b""))
            return FakeResponse()

        monkeypatch.setattr(metaculus.urllib.request, "urlopen", fake_urlopen)
        client = MetaculusClient(token="t")
        assert client._request("GET", "/posts/") == {}
        assert attempts["n"] == 2


class TestTournaments:
    """MetaculusClient.tournaments() backs seasonal slug auto-discovery in run_bot."""

    def test_hits_the_public_tournaments_endpoint(self) -> None:
        client = MetaculusClient(token="t")
        calls: list[dict[str, Any]] = []

        def fake_request(method: str, path: str, *, params: Any = None,
                         body: Any = None) -> Any:
            calls.append({"method": method, "path": path, "params": params})
            return [{"id": 1, "slug": "summer-futureeval-2026"}]

        client._request = fake_request  # type: ignore[method-assign]
        result = client.tournaments()
        assert result == [{"id": 1, "slug": "summer-futureeval-2026"}]
        assert calls == [
            {"method": "GET", "path": "/projects/tournaments/", "params": {"limit": 300}}
        ]

    def test_custom_limit_is_forwarded(self) -> None:
        client = MetaculusClient(token="t")
        seen: dict[str, Any] = {}

        def fake_request(method: str, path: str, *, params: Any = None,
                         body: Any = None) -> Any:
            seen.update(params or {})
            return []

        client._request = fake_request  # type: ignore[method-assign]
        client.tournaments(limit=50)
        assert seen == {"limit": 50}

    def test_tolerates_a_paginated_dict_response(self) -> None:
        # The endpoint returns a bare list today; read defensively in case that shifts.
        client = MetaculusClient(token="t")
        client._request = (  # type: ignore[method-assign]
            lambda *a, **k: {"results": [{"id": 2}]}
        )
        assert client.tournaments() == [{"id": 2}]

    def test_none_response_is_an_empty_list(self) -> None:
        client = MetaculusClient(token="t")
        client._request = lambda *a, **k: None  # type: ignore[method-assign]
        assert client.tournaments() == []
