"""Read the community prediction on the questions where Metaculus's API exposes it.

Metaculus's API omits aggregation data (the Community Prediction) on almost every
question. Per the docs, CP ships on only ``~50`` curated questions; separately, bot
tournaments (Bot Testing Area etc.) expose CP on their own questions. Everywhere else a
bot token gets ``latest: null`` even after forecasting, and the anonymous API, the legacy
api2, and the download-data endpoint are all closed (403). An empirical sweep of ~1000
open public questions with a bot token found CP on 0 of them outside bot tournaments.

So there is **no reliable API path to the human crowd on arbitrary public questions.**
This helper still has two uses:

* With the bot token, it reads CP inside a bot tournament (post-hoc track-record review).
* With ``METACULUS_CP_TOKEN`` set to a PERSONAL-account token, it *may* reach more than a
  bot token does (the website clearly renders CP the API withholds) — but this is
  UNVERIFIED and is not guaranteed to widen API access beyond the ~50 curated questions.
  For a dependable crowd-labelled benchmark on public questions, use ``bench/`` instead
  (ForecastBench freeze values + live Manifold/Polymarket prices — no Metaculus needed).

By design, OFFLINE MEASUREMENT ONLY. This module is intentionally never imported by
``run_bot`` (the crowd value must not reach the forecasting agent, and a tournament bot
must not consult the human crowd — exactly what Metaculus's firewall enforces), and the
token is withheld from the agent subprocess (see ``_SECRETS_TO_HIDE``).

CLI:
    python bot/crowd.py 44239 43597 ...
prints one line per post: post_id, community prediction, forecaster count.
"""

from __future__ import annotations

import os
import sys
import time

from metaculus import MetaculusClient


def crowd_client() -> MetaculusClient | None:
    """A client on the personal-account token, or None when it is not configured."""
    token = os.environ.get("METACULUS_CP_TOKEN", "")
    return MetaculusClient(token=token) if token else None


def human_cp(client: MetaculusClient, post_id: int) -> tuple[float | None, int | None]:
    """(community prediction, forecaster count) for a post's question, if visible."""
    detail = client.post_detail(post_id)
    question = detail.get("question") or {}
    cp = MetaculusClient.community_prediction(question)
    return cp, detail.get("nr_forecasters")


def main(argv: list[str]) -> int:
    client = crowd_client()
    if client is None:
        print("METACULUS_CP_TOKEN is not set (needs a PERSONAL account token, not the bot's)")
        return 1
    if not argv:
        print("usage: python bot/crowd.py <post_id> [<post_id> ...]")
        return 2
    for i, raw in enumerate(argv):
        if i:
            time.sleep(2)  # be polite; this endpoint is rate-limited
        cp, n = human_cp(client, int(raw))
        shown = f"{cp:.3f}" if cp is not None else "hidden/none"
        print(f"{raw}\t{shown}\tn={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
