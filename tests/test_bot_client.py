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
