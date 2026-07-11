"""Session-wide test guards.

The tournament research run now OPTIONALLY starts from AskNews articles (bot/asknews.py),
appended inside forecast_question. On a dev machine where the operator's real keyfile
(~/.asknews/key[.txt]) exists, that would make LIVE API calls inside every forecast_question
test. So default AskNews OFF for the whole suite via its documented kill switch: no test
touches the network unless it explicitly opts in (the asknews tests clear this and stub
urllib). This also keeps every pre-existing research-run assertion byte-identical, since a
disabled news_section() returns "" and brief + "" == brief.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _asknews_dark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASKNEWS_DISABLE", "1")
    monkeypatch.delenv("ASKNEWS_API_KEY", raising=False)
