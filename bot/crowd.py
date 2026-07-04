"""Read the HUMAN community prediction with a personal (non-bot) account token.

Metaculus deliberately hides the human community prediction from bot accounts on all
public (non-tournament) questions — the bot token gets ``latest: null`` even after
forecasting, and the anonymous API, the legacy api2, and the download-data endpoint
are all closed (403). The only self-serve programmatic path is a token from a normal
human account, which sees whatever the website shows.

Set ``METACULUS_CP_TOKEN`` to such a token (Metaculus -> account settings -> API token,
on your PERSONAL account, not the bot's). Two rules, by design:

* OFFLINE MEASUREMENT ONLY — benchmarks and reviewing the bot's track record. This
  module is intentionally never imported by ``run_bot``: the human crowd value must
  not reach the forecasting agent, and a bot competing in a tournament must not use
  the human crowd at all (that is exactly what Metaculus's firewall enforces).
* The token is withheld from the agent subprocess (see ``_SECRETS_TO_HIDE``).

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
