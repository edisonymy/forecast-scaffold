"""Manifold Markets bot: fast-feedback A/B of BLIND vs SIGHTED forecasting.

For each carefully selected binary CPMM market it runs the SAME forecast skill twice,
headlessly, in one pass:

  * BLIND   — question + creator's resolution criteria + close date, and NOTHING about the
              market price or volume. Aggregator domains (including manifold.markets) are
              tool-blocked so the agent cannot peek at the answer sheet.
  * SIGHTED — the same brief plus the live market probability, 24h volume, and bettor count,
              under the same judgment-call framing the Metaculus bot uses for crowd signals
              (adapted: on Manifold the market prices the EXACT contract, so the judgment is
              whether the crowd holds information we lack vs is herding/thin/stale).

Both forecasts are journaled (preregistration) into bot/journal/manifold.jsonl. Only the
SIGHTED forecast drives betting. Price movement toward/away from our forecast over the days
that follow is the fast calibration signal (see bot/score_manifold.py).

This module is a *consumer* of the forecast skill and of bot/run_bot.py's plumbing: no
forecasting logic lives here. It imports run_bot's agent machinery (run_agent, build_system,
validate_payload, extract_json, the blind-mode tool-blocks) rather than copying it.

Usage:
    python bot/run_manifold.py --limit 20 --tier medium
    python bot/run_manifold.py --limit 20 --tier medium --live --stake 25 --max-bets 15
Env:
    MANIFOLD_API_KEY   required to actually POST bets (only with --live); reads need no auth.
    FORECAST_JOURNAL   ignored here — the Manifold journal path is bot/journal/manifold.jsonl
                       unless overridden with --journal.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

# ruff: noqa: E402  (imports follow the sys.path bootstrap above)
import run_bot

from forecast_scaffold.core import ForecastRecord, Journal, _utc_now, clamp

DEFAULT_JOURNAL = ROOT / "bot" / "journal" / "manifold.jsonl"
MANIFOLD_API = "https://api.manifold.markets/v0"
# Key lookup: env var first, else a keyfile OUTSIDE the repo (never committable, never in
# chat transcripts). The operator writes it themselves: a one-line file holding only the key.
KEYFILE = Path.home() / ".manifold" / "key"


def _clean_manifold_key(value: str) -> str:
    """Remove transport/editor framing without ever logging the credential.

    A UTF-8 BOM survives when a key file is uploaded to a GitHub secret byte-for-byte.
    ``str.strip`` does not remove U+FEFF, and urllib then rejects the Authorization header
    because HTTP headers are Latin-1.  Normalize only leading BOMs and surrounding
    whitespace; never alter the key body.
    """
    return value.strip().lstrip("\ufeff").strip()


def manifold_api_key() -> str:
    key = os.environ.get("MANIFOLD_API_KEY", "")
    if key:
        return _clean_manifold_key(key)
    # Accept key.txt too: Notepad appends .txt and the operator should not have to care.
    for path in (KEYFILE, KEYFILE.with_suffix(".txt")):
        try:
            return _clean_manifold_key(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return ""
UA = {"User-Agent": "forecast-scaffold-manifold/0.1 "
      "(+https://github.com/edisonymy/forecast-scaffold)"}

# ---- selection policy (design decisions; see the module docstring) ---------------------
MIN_BETTORS = 25            # uniqueBettorCount floor: below this the price is not a crowd
#                             [AMENDED 2026-07-11: was 50 — signal volume over caution; the
#                             hard caps (stake/max-bets/exposure/floor) bound the thin-book risk]
CLOSE_MIN_DAYS = 3          # too soon and price movement can't teach anything before resolve
CLOSE_MAX_DAYS = 60         # too far and the days-scale movement signal is too slow
DIVERSITY_CAP = 3           # max markets sharing the same top groupSlug (one theme can't
#                             swamp a batch; correlated questions aren't independent evidence)
DAY_MS = 86_400_000
# Self-referential / meme markets: their "resolution" is social, not a fact about the world,
# so a forecast against them measures nothing. Case-insensitive; word boundaries keep "my "
# from matching "army"/"enemy" and "will i" from matching "will it".
MEME_RE = re.compile(r"this market|will i\b|\bmy |@", re.IGNORECASE)

# ---- betting policy -------------------------------------------------------------------
DIVERGENCE_THRESHOLD = 0.03   # bet only when |p_sighted - p_market| >= this
#                               [AMENDED 2026-07-20: was 0.05 (2026-07-11: was 0.08) — the
#                               operator asked for more signal volume; journal data showed a
#                               median divergence of 0.018, so 0.05 admitted only 18% of
#                               sighted forecasts vs 35% at 0.03. The hard caps
#                               (stake/max-bets/exposure/floor) still bound the risk]
MIN_BALANCE_MANA = 200.0      # decide_bet's own hard floor (a would-be bet needs SOME balance)
REFORECAST_DEDUPE_DAYS = 3    # skip re-forecasting a market whose journaled pair is newer than
#                               this many days (re-forecasting every run wastes budget)

# ---- market_read contract (REQUIRED + journaled; NO LONGER a bet gate) ------------------
# The sighted forecast must still return its judgment of the market price — a REQUIRED,
# journaled field. [AMENDED 2026-07-11] it is NO LONGER a bet gate: the journaled read is a
# preregistered hypothesis instead, so at review we can test whether informed-read bets
# underperform. The old gate starved the signal — on a liquid book the price reads "informed"
# almost by construction, so gating those out deleted most bettable divergences before any
# evidence was collected.
MARKET_READS = ("informed", "herding", "thin", "stale")

# ---- phase-machine policy (docs/manifold-policy.md; transitions are AUTOMATIC + journaled) --
DEFAULT_PHASE_FILE = ROOT / "bot" / "journal" / "manifold-phase.json"
PHASE1_BALANCE_FLOOR = 1100.0   # refuse ALL betting below 50% of the 2,200 adoption bankroll
EXPOSURE_CAP_FRAC = 0.30        # total open exposure <= 30% of live balance
MAX_BETS_PER_RUN = 10           # policy hard cap on bets placed per run
# Promotion 1->2 / kill thresholds.
PROMOTION_ALPHA = 0.05          # exact one-sided binomial p must clear this
PROMOTION_MIN_N = 50            # movement sample floor for a promote-or-kill decision
PROMOTION_MIN_RESOLVED_PAIRS = 10  # resolved pairs needed for the Brier comparison
MOVEMENT_AGE_DAYS = 7           # a divergent bet only counts once it is this old
# Phase-2 quarter-Kelly sizing.
KELLY_FRACTION = 0.25
KELLY_STAKE_CAP_FRAC = 0.05     # stake capped at 5% of balance
KELLY_STAKE_FLOOR = 10.0        # never size below 10 mana
CONVERGENCE_BAND = 0.03         # sell when the price comes within this of our forecast
REFORECAST_ADVERSE = 0.10       # re-forecast a position the market moved this far against us
ADOPTION_BANKROLL = 2200.0      # only used to size phase-2 would-be bets in an offline dry-run

# ---- model-credit policy ---------------------------------------------------------------
# Manifold is subscription-only.  The workflow runs once per hour and every invocation is
# capped at five USD-equivalent Claude credits.  ``total_cost_usd`` is the Claude CLI's
# notional usage meter even when OAuth routes the calls through a subscription; no metered
# Anthropic/API gateway credential is permitted on this path.
MAX_CREDIT_BUDGET_USD = 5.0
BUDGET_EPSILON_USD = 1e-6
METERED_AUTH_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

# The default agent command mirrors bot/run_bot.py's hardened production shape exactly:
# a JSON envelope (so cost/model record), and the same research toolset the workflows use.
DEFAULT_AGENT_CMD = ("claude -p --model claude-sonnet-5 --output-format json "
                     "--allowed-tools Read,Glob,Grep,WebSearch,WebFetch")

# The SIGHTED brief's market section — the crowd-signals judgment framing from run_bot.py's
# sighted brief (v0.4.11: REQUIRED-research-step + judgment-call + contract phrasing),
# ADAPTED for Manifold: there is no cross-platform contract to adjudicate because the market
# prices the exact same question, so the whole judgment collapses to "does the crowd know
# something I don't, or is it herding?" — which the agent is REQUIRED to answer in reasoning.
SIGHTED_MARKET_SECTION = (
    "\n\n## Market signals (Manifold)\n"
    "Current market probability: {prob}\n"
    "Volume (24h): {vol}\n"
    "Unique bettors: {bettors}\n\n"
    "This market prices the SAME contract you are forecasting: the resolution criteria "
    "above ARE this market's terms, so unlike a cross-platform market there is no "
    "contract mismatch to adjudicate — threshold, deadline, and resolution source already "
    "match on the terms that matter. Reading this price is a REQUIRED step, not an option. "
    "But whether to move toward it is YOUR judgment call, never arithmetic: the market is a "
    "valid anchor only insofar as the crowd holds information you lack. The judgment "
    "question is exactly which of two things is true — has the crowd priced in evidence you "
    "have not found (then move toward it), or is it herding, thin, or stale on a play-money "
    "book few informed traders watch (then hold your own view)? You are REQUIRED to state in "
    "your reasoning WHICH of these you concluded, and why, before you lean on or away from "
    "this number, and to return that same conclusion as a REQUIRED json field "
    '"market_read" set to exactly one of "informed" | "herding" | "thin" | "stale". Only '
    '"informed" (the crowd knows something you do not) gates OUT a bet; "herding", "thin", '
    'and "stale" are tradeable reads.'
)


# --------------------------------------------------------------------------- API helpers


def _get(url: str, timeout: int = 60) -> Any:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def search_markets(pool_size: int) -> list[dict[str, Any]]:
    """A pool of open binary markets from the public search endpoint (no auth).

    Sorted by 24h volume so the highest-activity markets lead; the caller re-ranks and
    filters, so this only needs to surface a generous candidate set.
    """
    url = f"{MANIFOLD_API}/search-markets?" + urllib.parse.urlencode({
        "term": "", "sort": "24-hour-vol", "filter": "open",
        "contractType": "BINARY", "limit": pool_size,
    })
    listing = _get(url)
    return listing if isinstance(listing, list) else []


def market_detail(market_id: str) -> dict[str, Any]:
    """Full market object: adds textDescription (the criteria) and groupSlugs (the tag)."""
    data = _get(f"{MANIFOLD_API}/market/{market_id}")
    return data if isinstance(data, dict) else {}


def get_balance(api_key: str) -> float:
    """The bot account's mana balance via GET /v0/me (needs the key)."""
    request = urllib.request.Request(
        f"{MANIFOLD_API}/me", headers={**UA, "Authorization": f"Key {api_key}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        me = json.loads(response.read().decode("utf-8"))
    return float(me.get("balance") or 0.0)


def place_bet(api_key: str, market_id: str, outcome: str, amount: float) -> dict[str, Any]:
    """POST /v0/bet. outcome is "YES" or "NO"; amount is mana. Live betting only."""
    body = json.dumps(
        {"contractId": market_id, "outcome": outcome, "amount": float(amount)}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{MANIFOLD_API}/bet", data=body, method="POST",
        headers={**UA, "Authorization": f"Key {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


# --------------------------------------------------------------------------- selection


def criteria_text(market: dict[str, Any]) -> str:
    """The creator's description — the resolution contract on Manifold — stripped.

    Empty means the market has no stated criteria to forecast against (the title alone is
    too terse to be a contract for a bot), so an empty return excludes the market.
    """
    return str(market.get("textDescription") or "").strip()


def top_tag(market: dict[str, Any]) -> str | None:
    """The market's top groupSlug, used for the diversity cap. None when untagged (untagged
    markets share no theme, so the cap does not apply to them)."""
    slugs = market.get("groupSlugs") or []
    return str(slugs[0]) if slugs else None


def eligible_lite(market: dict[str, Any], now_ms: int) -> bool:
    """Every filter that reads only fields present in the lightweight listing.

    Kept separate so the live path can cheaply pre-screen before paying for a detail fetch;
    ``select_markets`` re-applies it (idempotently) on the enriched markets.
    """
    lo = now_ms + CLOSE_MIN_DAYS * DAY_MS
    hi = now_ms + CLOSE_MAX_DAYS * DAY_MS
    close = market.get("closeTime")
    return bool(
        market.get("outcomeType") == "BINARY"
        and market.get("mechanism") == "cpmm-1"
        and not market.get("isResolved")
        and isinstance(close, int | float)
        and lo <= close <= hi
        and (market.get("uniqueBettorCount") or 0) >= MIN_BETTORS
        and not MEME_RE.search(str(market.get("question") or ""))
    )


def select_markets(
    markets: list[dict[str, Any]], limit: int, now_ms: int | None = None
) -> list[dict[str, Any]]:
    """Filter, rank by 24h volume, and take ``limit`` split half top-volume / half mid-band.

    Expects enriched market dicts (textDescription + groupSlugs present). Deterministic and
    stateless: both bands walk the same volume-sorted eligible list and share one per-tag
    diversity counter.

    Selection rule [AMENDED 2026-07-11 — signal volume over caution]:
      * sort eligible candidates by 24h volume, descending;
      * take ``top_k = limit - limit // 2`` from the TOP band (ranks 0..limit): the deep,
        liquid books (the top band gets the odd market on an odd ``limit``);
      * take ``mid_k = limit // 2`` from the MID band (ranks limit..limit*4 of the same
        list): thinner books the bot can actually move, so the batch is not all
        crowd-favourites and includes markets thin enough to beat.
    Ranks between ``top_k`` and ``limit`` are deliberately skipped to keep the two bands
    separate. The diversity cap is shared across bands; a short band simply under-fills (no
    backfill), which keeps the rule deterministic.
    """
    now_ms = now_ms if now_ms is not None else _now_ms()
    candidates = [
        m for m in markets if eligible_lite(m, now_ms) and criteria_text(m)
    ]
    candidates.sort(key=lambda m: float(m.get("volume24Hours") or 0.0), reverse=True)
    mid_k = limit // 2
    top_k = limit - mid_k
    selected: list[dict[str, Any]] = []
    tag_counts: dict[str, int] = {}

    def draw(band: list[dict[str, Any]], k: int) -> None:
        taken = 0
        for m in band:
            if taken >= k:
                break
            tag = top_tag(m)
            if tag is not None and tag_counts.get(tag, 0) >= DIVERSITY_CAP:
                continue
            selected.append(m)
            taken += 1
            if tag is not None:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    draw(candidates[:limit], top_k)             # top band: ranks 0..limit
    draw(candidates[limit:limit * 4], mid_k)    # mid band: ranks limit..limit*4
    return selected


def gather_markets(
    limit: int, now_ms: int | None = None, exclude: set[str] | None = None
) -> list[dict[str, Any]]:
    """Live selection end to end: fetch the listing, pre-screen, enrich the top candidates
    with detail (criteria + tags), then apply the full selection. Read-only, no key.

    ``exclude`` drops market ids BEFORE enrichment and selection. The run loop passes the
    fresh-pair dedupe set here [AMENDED 2026-07-20]: selection is volume-ranked and mostly
    stable hour to hour, so once the top of the ranking had been forecast, every hourly
    batch re-selected the same markets and the in-loop dedupe skipped them all — the bot
    produced zero pairs per run for days. Excluding them up front fills the batch with
    markets that can actually be forecast this run."""
    now_ms = now_ms if now_ms is not None else _now_ms()
    exclude = exclude or set()
    listing = search_markets(max(200, limit * 25))
    prescreened = [
        m for m in listing
        if eligible_lite(m, now_ms) and str(m.get("id")) not in exclude
    ]
    prescreened.sort(key=lambda m: float(m.get("volume24Hours") or 0.0), reverse=True)
    # Enrich only as many as could plausibly be needed (the diversity cap can reject some),
    # not the whole listing — a detail fetch per market is the expensive part.
    enriched: list[dict[str, Any]] = []
    for m in prescreened[: limit * 4 + 20]:
        detail = market_detail(str(m.get("id")))
        enriched.append({**m, **detail})
    return select_markets(enriched, limit, now_ms)


# --------------------------------------------------------------------------- briefs


def _close_date(market: dict[str, Any]) -> str | None:
    close = market.get("closeTime")
    if not isinstance(close, int | float):
        return None
    return datetime.fromtimestamp(close / 1000, tz=UTC).date().isoformat()


def build_manifold_brief(market: dict[str, Any], sighted: bool) -> str:
    """The agent-facing brief. The blind brief carries NO price or volume anywhere; the
    sighted brief appends the market section with the judgment framing."""
    criteria = (
        "Resolves per the market creator's stated conditions on Manifold. Description:\n"
        + criteria_text(market)
    )
    parts = [
        f"# Question: {market.get('question', '')}",
        "Type: binary",
        f"Now (UTC): {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} — anchor every "
        "elapsed/remaining-time statement to this timestamp.",
        f"Closes: {_close_date(market) or 'unknown'} — the event window itself comes from "
        "the resolution criteria text below, not from this close date.",
        "\n## Resolution criteria (verbatim — the contract)",
        criteria,
    ]
    brief = "\n".join(parts)
    if sighted:
        brief += SIGHTED_MARKET_SECTION.format(
            prob=market.get("probability"),
            vol=market.get("volume24Hours"),
            bettors=market.get("uniqueBettorCount"),
        )
    return brief


def agent_cmd_for(base_cmd: str, blind: bool) -> str:
    """base_cmd plus one --disallowed-tools belt: the always-on deny list, and (blind only)
    the aggregator-domain blocks — which include manifold.markets itself, so a blind run
    cannot fetch the very market it is forecasting."""
    disallowed = run_bot.ALWAYS_DISALLOWED + (
        "," + run_bot.BLIND_DISALLOWED if blind else ""
    )
    return f"{base_cmd} --disallowed-tools {disallowed}"


def with_credit_cap(agent_cmd: str, remaining_usd: float) -> str:
    """Return ``agent_cmd`` with exactly one native Claude per-process credit cap.

    Cumulative accounting lives in :func:`forecast_market`; this second layer makes the
    remaining allowance a hard ceiling even when one agent call runs unusually long.  A
    caller-supplied cap is replaced, never trusted or duplicated.
    """
    if not math.isfinite(remaining_usd) or remaining_usd <= 0:
        raise ValueError("remaining credit budget must be positive and finite")
    tokens = shlex.split(agent_cmd)
    cleaned: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--max-budget-usd":
            i += 2
            continue
        if token.startswith("--max-budget-usd="):
            i += 1
            continue
        cleaned.append(token)
        i += 1
    return shlex.join([*cleaned, "--max-budget-usd", f"{remaining_usd:.6f}"])


def subscription_auth_error(require_oauth: bool = False) -> str | None:
    """Explain why the Manifold agent path is not provably subscription-only, if so.

    Local runs may use Claude Code's cached subscription login.  Unattended cloud runs pass
    ``--require-subscription-auth`` and must present the long-lived setup-token explicitly.
    Any metered API/gateway setting is rejected in both cases because Claude Code otherwise
    gives some of those credentials precedence over subscription OAuth.
    """
    forbidden = [name for name in METERED_AUTH_ENV if os.environ.get(name, "").strip()]
    if forbidden:
        return "metered or gateway auth is set: " + ", ".join(forbidden)
    if require_oauth and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        return "CLAUDE_CODE_OAUTH_TOKEN is required for unattended subscription auth"
    return None


def is_subscription_session_limit_error(error: BaseException | str) -> bool:
    """True only for Claude's explicit subscription-session quota response.

    A generic 429, auth failure, timeout, or provider error must remain red.  This narrow
    recognition lets an hourly subscription-only runner defer until the next tick without
    producing an operator alert every hour while Claude has stated that the session is
    exhausted.
    """
    message = str(error).lower()
    return (
        "api_error_status" in message
        and "429" in message
        and "session limit" in message
    )


def is_claude_budget_cap_error(error: BaseException | str) -> bool:
    """True only for Claude CLI's explicit native ``--max-budget-usd`` stop.

    Claude exits nonzero with a JSON result envelope when the per-process cap supplied by
    :func:`with_credit_cap` is reached.  That is an expected, locally imposed budget stop,
    not an infrastructure failure.  Keep recognition deliberately narrow: timeouts, auth
    failures, generic provider errors, and malformed/other Claude envelopes must stay red.
    ``run_agent`` truncates diagnostic envelopes, so matching the required leading fields
    is safer than attempting to decode possibly truncated JSON.
    """
    message = str(error)
    compact = "".join(message.split())
    return (
        message.startswith("agent failed (")
        and '"type":"result"' in compact
        and '"subtype":"error_max_budget_usd"' in compact
        and '"is_error":true' in compact
    )


# --------------------------------------------------------------------------- forecasting

# Binary-only question shape for run_bot.validate_payload (Manifold markets we select are
# all binary CPMM).
_BINARY_Q = {"type": "binary"}


def normalize_market_read(payload: dict[str, Any]) -> str | None:
    """The payload's ``market_read``, lower-cased and stripped, or None if absent/blank."""
    value = str(payload.get("market_read", "")).strip().lower()
    return value or None


def validate_market_read(payload: dict[str, Any]) -> list[str]:
    """Repairable-error list for the SIGHTED contract's required ``market_read`` field.

    Missing or not one of the allowed values is a repairable error that the forecast loop
    quotes back to the agent (same pattern as run_bot's payload repair)."""
    value = normalize_market_read(payload)
    if value not in MARKET_READS:
        return [
            f'"market_read" is REQUIRED and must be exactly one of {list(MARKET_READS)}, '
            f"got {payload.get('market_read')!r}"
        ]
    return []


def forecast_market(
    market: dict[str, Any], mode: str, tier: str, args: argparse.Namespace,
    config: dict[str, Any], budget_state: dict[str, Any] | None = None,
    deadline: float | None = None,
) -> dict[str, Any] | None:
    """One blind or sighted research forecast for one market. Thin runner over run_bot's
    agent machinery (modeled on bench/run_bench.forecast_one): build the system prompt via
    build_system, run the agent, extract + validate the JSON, one repair retry. Returns the
    payload plus cost/model, or None if it never produced a valid probability.
    """
    blind = mode == "blind"
    brief = build_manifold_brief(market, sighted=not blind)
    # Research floor (both modes are research forecasts): ANNOUNCE the tier's min_sources in
    # the brief and ENFORCE it in the validate/repair loop below — reused verbatim from
    # run_bot (SOURCE_FLOOR_SECTION + distinct_source_count), so this floor matches the
    # Metaculus bot's exactly and the sources actually consulted land in the journal.
    tier_params = (config.get("tiers") or {}).get(tier) or {}
    min_sources = max(0, int(tier_params.get("min_sources", 0) or 0))
    if min_sources:
        brief += "\n" + run_bot.SOURCE_FLOOR_SECTION.format(floor=min_sources)
    base_cmd = args.agent_cmd
    system = run_bot.build_system(tier, blind, config)
    agent_cmd = agent_cmd_for(base_cmd, blind)

    budget_state = budget_state if budget_state is not None else {
        "usd": 0.0,
        "uncertain": False,
        "subscription_deferred": False,
        "budget_deferred": False,
    }
    budget = float(getattr(args, "budget", 0.0) or 0.0)
    cost = 0.0
    model = ""
    payload: dict[str, Any] = {}
    errors: list[str] = []
    probability: float | None = None
    for attempt in range(2):
        call_cmd = agent_cmd
        remaining: float | None = None
        if budget > 0:
            remaining = budget - float(budget_state.get("usd", 0.0) or 0.0)
            if remaining <= BUDGET_EPSILON_USD:
                errors = [f"credit budget ${budget:.2f} exhausted before {mode} attempt"]
                break
            call_cmd = with_credit_cap(agent_cmd, remaining)
        call_timeout = int(args.timeout)
        if deadline is not None:
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 1:
                errors = [f"wall-clock deadline reached before {mode} attempt"]
                break
            call_timeout = min(call_timeout, max(1, int(seconds_left)))
        prompt = brief if attempt == 0 else (
            brief + "\n\nYour previous output was invalid: " + "; ".join(errors)
            + "\nEmit a corrected fenced json block."
        )
        try:
            output, attempt_cost, model = run_bot.run_agent(
                call_cmd, prompt, system, call_timeout, args.provider
            )
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            if is_claude_budget_cap_error(exc):
                # Our own native cap stopped Claude. Its envelope does not provide usable
                # cost telemetry, so conservatively reserve the full remaining allowance,
                # stop all further calls, and let the next hourly run continue. Completed
                # pairs have already been journaled by run(); an in-progress half-pair is
                # intentionally discarded.
                if remaining is not None:
                    budget_state["usd"] = budget
                    budget_state["uncertain"] = True
                budget_state["budget_deferred"] = True
                errors = ["Claude native credit cap reached; deferred to next hourly run"]
                break
            if is_subscription_session_limit_error(exc):
                # Claude says this subscription session is exhausted. Reserve the entire
                # remaining allowance exactly as for any unmetered failure, stop this run,
                # and let the next hourly tick retry. This is not permission to fall back to
                # OpenRouter (the Manifold path remains subscription-only).
                if remaining is not None:
                    budget_state["usd"] = budget
                    budget_state["uncertain"] = True
                budget_state["subscription_deferred"] = True
                errors = ["Claude subscription session limit; deferred to next hourly run"]
                break
            # A failed/timed-out subprocess may already have consumed any amount up to its
            # native cap, but it returned no trustworthy meter. Reserve the ENTIRE unknown
            # remainder and stop: the next call must never be able to double-spend it.
            if remaining is not None:
                budget_state["usd"] = budget
                budget_state["uncertain"] = True
                errors = [f"{str(exc)[:220]}; unknown usage reserved to the hard cap"]
                break
            errors = [str(exc)[:300]]
            continue
        if not math.isfinite(attempt_cost) or attempt_cost < 0:
            attempt_cost = 0.0
        if budget > 0 and attempt_cost <= 0:
            # The cumulative layer cannot safely release any allowance without positive
            # Claude JSON-envelope telemetry. The native process cap still bounded this
            # call; reserve that whole remainder and stop all further calls.
            budget_state["usd"] = budget
            budget_state["uncertain"] = True
            errors = ["agent returned no positive total_cost_usd; usage reserved to hard cap"]
            break
        cost += attempt_cost
        budget_state["usd"] = float(budget_state.get("usd", 0.0) or 0.0) + attempt_cost
        if remaining is not None and attempt_cost > remaining + BUDGET_EPSILON_USD:
            budget_state["uncertain"] = True
            errors = [
                f"agent reported ${attempt_cost:.4f} above its ${remaining:.4f} native cap"
            ]
            break
        try:
            payload = run_bot.extract_json(output)
        except (RuntimeError, ValueError) as exc:
            errors = [str(exc)[:300]]
            continue
        errors = run_bot.validate_payload(payload, _BINARY_Q)
        # The sighted contract additionally REQUIRES a valid market_read (journaled as a
        # preregistered hypothesis, no longer a bet gate); the blind run is never asked.
        if not errors and not blind:
            errors = validate_market_read(payload)
        # Research floor (both modes): reject BEFORE the forecast is accepted so the repair
        # retry re-researches — same reject-before-accept point run_bot uses. Empty sources
        # (0 < min_sources) fail here with a clear message.
        if not errors and min_sources:
            found = run_bot.distinct_source_count(payload)
            if found < min_sources:
                errors = [
                    f"research run listed {found} distinct source(s); this tier requires "
                    f'at least {min_sources} — run real searches and list in "sources" '
                    "only what you actually consulted"
                ]
        if not errors:
            probability = float(payload["probability"])
            break
    if probability is None:
        print(f"    {mode} FAILED after retry: {errors}")
        return None
    return {
        "mode": mode,
        "blind": blind,
        "probability": probability,
        # market_read rides only on the sighted forecast — the blind run never saw the price.
        "market_read": None if blind else normalize_market_read(payload),
        "cost_usd": round(cost, 4),
        "model": model,
        "agent_cmd": agent_cmd,
        "reasoning": str(payload.get("reasoning", ""))[:4000],
        "reference_class": str(payload.get("reference_class", "")),
        "base_rate": run_bot._as_float(payload.get("base_rate")),
        "raw_draws": [f for d in payload.get("raw_draws") or []
                      if (f := run_bot._as_float(d)) is not None] or None,
        "sources": [str(s)[:300] for s in payload.get("sources") or [] if str(s).strip()],
        "what_would_change_my_mind": [
            str(x) for x in payload.get("what_would_change_my_mind", [])
        ],
    }


# --------------------------------------------------------------------------- betting


def decide_bet(
    p_sighted: float, p_market: float, stake: float, *,
    balance: float, already_positioned: bool,
) -> dict[str, Any] | None:
    """The betting gate as a pure function. Returns the bet {outcome, stake} or None.

    Bet when the sighted forecast diverges from the market by at least the threshold, we hold
    no open position already, and the balance clears the floor. [AMENDED 2026-07-11] the
    sighted run's ``market_read`` is NO LONGER checked here — it is journaled as a
    preregistered hypothesis, not a gate (see the MARKET_READS note above). Direction: YES
    when our forecast is higher than the market (we think it's underpriced), NO when lower.
    """
    if already_positioned:
        return None
    if balance < MIN_BALANCE_MANA:
        return None
    if abs(p_sighted - p_market) < DIVERGENCE_THRESHOLD:
        return None
    outcome = "YES" if p_sighted > p_market else "NO"
    return {"outcome": outcome, "stake": float(stake)}


def already_bet_market_ids(journal: Journal) -> set[str]:
    """Market ids we hold an actually-placed open position in (open-position guard,
    journal-based per the design — simpler and it survives a fresh checkout the way the
    journal does). A dry-run would-be bet is NOT a position: a phase-0 paper bet, or a
    phase-1 run degraded to dry-run, must not block a later live bet on the same market.
    An "unknown"-status bet (POST failed after send) does count — it may have filled."""
    ids: set[str] = set()
    for record in journal:
        src = record.source or {}
        bet = src.get("bet")
        if (src.get("platform") == "manifold" and bet and not bet.get("dry_run")
                and src.get("question_id")):
            ids.add(str(src["question_id"]))
    return ids


def recently_forecast_market_ids(
    records: list[Any], now: datetime | None = None,
    within_days: float = REFORECAST_DEDUPE_DAYS,
) -> set[str]:
    """Market ids that already have a journaled forecast pair newer than ``within_days``.

    Read from the journal the same way the open-position guard is (scan the manifold records).
    Re-forecasting the same market every run wastes budget — a live IMO market was forecast
    twice in 6 hours — so a market with a fresh pair is skipped this run. Timing is read from
    ``forecast_at`` (fallback ``created``), the same field the phase machine ages against."""
    now = now or datetime.now(UTC)
    fresh: set[str] = set()
    for record in records:
        src = record.source or {}
        if src.get("platform") != "manifold":
            continue
        qid = src.get("question_id")
        if not qid:
            continue
        age = _age_days(record.forecast_at or record.created, now)
        if age is not None and age < within_days:
            fresh.add(str(qid))
    return fresh


def open_exposure(records: list[Any], state_lookup: Any = None) -> float:
    """Total mana staked in still-open, actually-placed (non-dry-run) bets — the base the
    30%-of-balance exposure cap binds against. Nothing ever writes resolution status back to
    the Manifold journal, so the journal-status filter alone never decrements: when
    ``state_lookup(question_id)`` is given (the live path), a bet whose live market state has
    resolved is excluded — that is what actually closes a position. A lookup failure COUNTS
    the bet (fail closed: a transient API outage must not uncap exposure). With no lookup
    (offline/dry paths) behavior is journal-only, exactly as before."""
    total = 0.0
    for record in records:
        src = record.source or {}
        bet = src.get("bet")
        if not (src.get("platform") == "manifold" and bet and not bet.get("dry_run")
                and record.status not in ("resolved", "annulled")):
            continue
        if state_lookup is not None:
            try:
                if state_lookup(str(src.get("question_id"))).get("resolved"):
                    continue
            except Exception:  # noqa: BLE001 — fail closed: an unreadable market stays counted
                pass
        total += float(bet.get("stake") or 0.0)
    return total


def kelly_stake(p_us: float, p_market: float, balance: float) -> float:
    """Quarter-Kelly stake, capped at 5% of balance and floored at 10 mana (phase 2).

    Kelly fraction for YES at market m with our p is (p-m)/(1-m); NO mirrors it as (m-p)/m.
    Quarter because our p is noisy and CPMM slippage shrinks realized edge."""
    m = p_market
    kelly = (p_us - m) / (1.0 - m) if p_us > m else (m - p_us) / m
    kelly = max(kelly, 0.0)
    raw = KELLY_FRACTION * kelly * balance
    capped = min(raw, KELLY_STAKE_CAP_FRAC * balance)
    return max(capped, KELLY_STAKE_FLOOR)


def _toward(p_us: float, p_0: float, p_now: float) -> float:
    """Signed movement toward our forecast: sign(p_us - p_0) * (p_now - p_0)."""
    sign = 1.0 if p_us >= p_0 else -1.0
    return sign * (p_now - p_0)


def moved_against(p_us: float, p_0: float, p_now: float) -> float:
    """How far the market moved AGAINST our forecast since entry (positive = adverse)."""
    return -_toward(p_us, p_0, p_now)


def should_converge_exit(p_us: float, p_now: float, band: float = CONVERGENCE_BAND) -> bool:
    """Phase-2 convergence exit: the price has come within ``band`` of our forecast."""
    return abs(p_now - p_us) <= band


def should_reforecast(
    p_us: float, p_0: float, p_now: float, threshold: float = REFORECAST_ADVERSE
) -> bool:
    """Phase-2 re-forecast trigger: the market moved more than ``threshold`` against us."""
    return moved_against(p_us, p_0, p_now) > threshold


def sell_position(
    api_key: str, market_id: str, outcome: str | None = None, shares: float | None = None
) -> dict[str, Any]:
    """POST /v0/market/{marketId}/sell (verified against docs.manifold.markets/api).

    Body fields, all optional: ``outcome`` ("YES"/"NO", defaults to your held position),
    ``shares`` (defaults to your whole position). Live phase-2 convergence exits only; never
    called in dry-run or in tests."""
    body: dict[str, Any] = {}
    if outcome is not None:
        body["outcome"] = outcome
    if shares is not None:
        body["shares"] = float(shares)
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{MANIFOLD_API}/market/{market_id}/sell", data=data, method="POST",
        headers={**UA, "Authorization": f"Key {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


# ------------------------------------------------------------------- phase state machine


def load_phase(path: str | Path) -> dict[str, Any]:
    """The committed phase file, or a fresh phase-0 state when it does not exist yet."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {
        "phase": int(data.get("phase", 0)),
        "killed": bool(data.get("killed", False)),
        "history": list(data.get("history", [])),
    }


def save_phase(path: str | Path, state: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def binomial_p_value(k: int, n: int, p: float = 0.5) -> float | None:
    """Exact one-sided P(X >= k) for X ~ Binomial(n, p), via math.comb. None when n <= 0."""
    if n <= 0:
        return None
    return sum(math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i)) for i in range(k, n + 1))


def _manifold_pairs(records: list[Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Group manifold records by pair_id into {"blind": {...}, "sighted": {...}} sides, each
    carrying the forecast probability and the market price recorded at forecast time."""
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        src = record.source or {}
        if src.get("platform") != "manifold":
            continue
        pid = src.get("pair_id")
        if not pid:
            continue
        side = "blind" if record.blind else "sighted"
        crowd = record.crowd or {}
        pairs.setdefault(str(pid), {})[side] = {
            "record": record,
            "p": record.probability,
            "p_market": crowd.get("value"),
            "question_id": src.get("question_id"),
        }
    return pairs


def _valid_pairs(records: list[Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Pairs that have BOTH a blind and a sighted forecast with a probability and a recorded
    market price — the "valid pair" unit the 0->1 promotion counts."""
    valid = {}
    for pid, sides in _manifold_pairs(records).items():
        b, s = sides.get("blind"), sides.get("sighted")
        if (b and s and b["p"] is not None and s["p"] is not None
                and b["p_market"] is not None and s["p_market"] is not None):
            valid[pid] = sides
    return valid


def _bet_decisions_evaluated(records: list[Any]) -> int:
    """Sighted records that reached the bet-decision stage — a would-be/real bet was recorded
    or a market_read judgment was journaled (either proves the gate ran without error)."""
    n = 0
    for record in records:
        src = record.source or {}
        if src.get("platform") != "manifold" or record.blind:
            continue
        if src.get("bet") is not None or src.get("market_read") is not None:
            n += 1
    return n


def eval_phase0(records: list[Any]) -> dict[str, Any]:
    """0->1 evidence: valid blind/sighted pairs and bet decisions evaluated."""
    return {
        "valid_pairs": len(_valid_pairs(records)),
        "bet_decisions_evaluated": _bet_decisions_evaluated(records),
    }


def _age_days(iso_str: str | None, now: datetime) -> float | None:
    try:
        dt = datetime.fromisoformat(iso_str) if iso_str else None
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (now - dt).total_seconds() / 86400.0


def eval_phase1(
    records: list[Any], state_lookup: Any, now: datetime | None = None
) -> dict[str, Any]:
    """1->2 / kill evidence: the toward/away movement count over live divergent bets at least
    7 days old (with its exact one-sided binomial p), and the blind-vs-sighted Brier over
    resolved pairs. ``state_lookup(market_id)`` returns the score_manifold MarketState;
    injected so tests need no network."""
    now = now or datetime.now(UTC)
    toward = away = 0
    for record in records:
        src = record.source or {}
        if src.get("platform") != "manifold" or record.blind:
            continue
        bet = src.get("bet")
        # A dry-run would-be bet is not a live position and carries no live-money movement
        # evidence (mirrors open_exposure / _manage_phase2_positions).
        if not bet or bet.get("dry_run"):
            continue
        age = _age_days(record.forecast_at or record.created, now)
        if age is None or age < MOVEMENT_AGE_DAYS:
            continue
        p_us = record.probability
        p_0 = bet.get("p_market_at_bet")
        if p_0 is None:
            p_0 = (record.crowd or {}).get("value")
        if p_us is None or p_0 is None or abs(p_us - p_0) < DIVERGENCE_THRESHOLD:
            continue
        try:
            state = state_lookup(str(src.get("question_id")))
        except Exception:  # noqa: BLE001 — a fetch failure just drops that bet from the count
            continue
        if state.get("resolved"):  # "live" divergent bets only
            continue
        p_now = state.get("probability")
        if p_now is None:
            continue
        m = _toward(p_us, p_0, p_now)
        if m > 0:
            toward += 1
        elif m < 0:
            away += 1  # ties (m == 0) are dropped
    n = toward + away
    brier_blind, brier_sighted, n_resolved = _resolved_pair_briers(records, state_lookup)
    return {
        "n_movement": n,
        "moved_toward": toward,
        "moved_away": away,
        "toward_rate": (toward / n) if n else None,
        "binomial_p": binomial_p_value(toward, n) if n else None,
        "n_resolved_pairs": n_resolved,
        "brier_blind": brier_blind,
        "brier_sighted": brier_sighted,
    }


def _resolved_pair_briers(
    records: list[Any], state_lookup: Any
) -> tuple[float | None, float | None, int]:
    """Mean blind and sighted Brier over pairs whose market has resolved YES/NO."""
    blind_briers: list[float] = []
    sighted_briers: list[float] = []
    for _pid, sides in _valid_pairs(records).items():
        qid = str(sides["sighted"]["question_id"])
        try:
            state = state_lookup(qid)
        except Exception:  # noqa: BLE001
            continue
        if not state.get("resolved") or state.get("outcome") is None:
            continue
        hit = 1.0 if state["outcome"] else 0.0
        blind_briers.append((sides["blind"]["p"] - hit) ** 2)
        sighted_briers.append((sides["sighted"]["p"] - hit) ** 2)
    n = len(sighted_briers)
    if not n:
        return None, None, 0
    return sum(blind_briers) / n, sum(sighted_briers) / n, n


def evaluate_promotions(
    state: dict[str, Any], records: list[Any], state_lookup: Any,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Advance the phase state as far as the evidence allows (transitions are automatic and
    journaled per the policy). Returns the mutated state and the list of transitions applied,
    each {"to", "at", "evidence"}. A killed bot never promotes again."""
    now = now or datetime.now(UTC)
    at = now.isoformat(timespec="seconds")
    transitions: list[dict[str, Any]] = []
    if state.get("killed"):
        return state, transitions
    while True:
        phase = state.get("phase", 0)
        if phase == 0:
            ev = eval_phase0(records)
            if ev["valid_pairs"] >= 3 and ev["bet_decisions_evaluated"] >= 1:
                state["phase"] = 1
                t = {"to": 1, "at": at, "evidence": ev}
                state.setdefault("history", []).append(t)
                transitions.append(t)
                continue
            break
        if phase == 1:
            ev = eval_phase1(records, state_lookup, now)
            n, rate = ev["n_movement"], ev["toward_rate"]
            if n >= PROMOTION_MIN_N and rate is not None and rate <= 0.5:
                state["killed"] = True
                t = {"to": "killed", "at": at, "evidence": ev}
                state.setdefault("history", []).append(t)
                transitions.append(t)
                break  # betting disabled permanently; forecasting continues
            promote = (
                n >= PROMOTION_MIN_N
                and ev["binomial_p"] is not None and ev["binomial_p"] < PROMOTION_ALPHA
                and ev["n_resolved_pairs"] >= PROMOTION_MIN_RESOLVED_PAIRS
                and ev["brier_sighted"] is not None and ev["brier_blind"] is not None
                and ev["brier_sighted"] <= ev["brier_blind"]
            )
            if promote:
                state["phase"] = 2
                t = {"to": 2, "at": at, "evidence": ev}
                state.setdefault("history", []).append(t)
                transitions.append(t)
                continue
            break
        break  # phase 2 is terminal
    return state, transitions


# --------------------------------------------------------------------------- journaling


def build_record(
    market: dict[str, Any], forecast: dict[str, Any], pair_id: str, *,
    dry_run: bool, bet: dict[str, Any] | None = None,
) -> ForecastRecord:
    """One journal record for one mode's forecast. Both modes share ``pair_id`` (in the free
    ``source`` dict) and are distinguished by the existing ``blind`` field — no new record
    field is added. The market price at forecast time rides in ``crowd`` (run_bot's pattern);
    a placed/would-be bet rides in ``source.bet`` (side + stake + dry_run)."""
    market_id = str(market.get("id"))
    p_market = market.get("probability")
    source: dict[str, Any] = {
        "platform": "manifold",
        "question_id": market_id,
        "url": market.get("url", ""),
        "pair_id": pair_id,
        "mode": forecast["mode"],
    }
    # The sighted run's required market_read judgment (the trading gate) is journaled as a
    # free source field alongside mode/bet; the blind run carries none.
    if forecast.get("market_read"):
        source["market_read"] = forecast["market_read"]
    if bet is not None:
        source["bet"] = {
            "outcome": bet["outcome"], "stake": bet["stake"], "dry_run": dry_run,
            "p_market_at_bet": p_market,
        }
        # POST provenance: "placed" (bet id confirmed) or "unknown" (POST failed after send —
        # it may have filled, so readers treat it as an open position). Dry-run would-be bets
        # and pre-status journal records carry no status; absent reads as "placed".
        if bet.get("status"):
            source["bet"]["status"] = bet["status"]
    criterion = criteria_text(market) or (
        f"(no creator description) Resolves per the plain reading of: "
        f"{market.get('question', '')}"
    )
    return ForecastRecord(
        question=str(market.get("question", "untitled"))[:500],
        question_type="binary",
        resolution_criterion=criterion[:2000],
        forecast_at=_utc_now(),
        resolve_by=_close_date(market),
        source=source,
        reference_class=forecast["reference_class"],
        base_rate=forecast["base_rate"],
        probability=clamp(float(forecast["probability"]), 0.01, 0.99),
        raw_draws=forecast["raw_draws"],
        effort=forecast.get("tier"),
        model=forecast["model"] or run_bot._model_from_cmd(forecast["agent_cmd"]),
        provider=forecast.get("provider"),
        blind=forecast["blind"],
        dry_run=dry_run,
        cost_usd=forecast["cost_usd"] or None,
        crowd={
            "value": p_market,
            "source": "manifold market",
            "at": _utc_now(),
            # The blind run never saw the price; the sighted run did. Mirrors run_bot.
            "shown_to_agent": not forecast["blind"],
        } if p_market is not None else None,
        reasoning=forecast["reasoning"],
        what_would_change_my_mind=forecast["what_would_change_my_mind"],
        research=(
            {"n_searches": len(forecast["sources"]), "sources": forecast["sources"]}
            if forecast["sources"] else None
        ),
    )


# --------------------------------------------------------------------------- run loop


def _manage_phase2_positions(
    records: list[Any], markets: list[dict[str, Any]], args: argparse.Namespace, *,
    api_key: str, can_post: bool, state_lookup: Any,
) -> list[dict[str, Any]]:
    """Phase-2 open-position management, run BEFORE new markets. A position whose price has
    come within the convergence band of our forecast is sold (signal banked, capital recycled;
    a would-sell is printed in dry-run). A position the market has moved > 0.10 AGAINST is
    queued for re-forecast this run, prepended to the market list and counting toward the
    run's limit."""
    reforecast_ids: list[str] = []
    seen = {str(m.get("id")) for m in markets}
    for record in records:
        src = record.source or {}
        bet = src.get("bet")
        # The record.status filter is belt only: resolution status is never written back to
        # the Manifold journal, so the live resolved-check below is what authoritatively
        # closes a position.
        if (src.get("platform") != "manifold" or record.blind or not bet
                or bet.get("dry_run") or record.status in ("resolved", "annulled")):
            continue
        qid = str(src.get("question_id"))
        try:
            state = state_lookup(qid)
        except Exception:  # noqa: BLE001 — a fetch failure just skips that position this run
            continue
        if state.get("resolved"):
            continue
        p_now = state.get("probability")
        p_us = record.probability
        p_0 = bet.get("p_market_at_bet")
        if p_0 is None:
            p_0 = (record.crowd or {}).get("value")
        if p_now is None or p_us is None:
            continue
        if should_converge_exit(p_us, p_now):
            if can_post:
                try:
                    sell_position(api_key, qid, bet.get("outcome"))
                    print(f"  CONVERGENCE EXIT: sold {qid} "
                          f"(price {p_now:.2f} ~ forecast {p_us:.2f})")
                except Exception as exc:  # noqa: BLE001 — a failed sell must not abort the run
                    print(f"  convergence sell failed for {qid} ({exc})")
            else:
                print(f"  CONVERGENCE EXIT (dry-run): would sell {qid} "
                      f"(price {p_now:.2f} ~ forecast {p_us:.2f})")
        elif p_0 is not None and should_reforecast(p_us, p_0, p_now) and qid not in seen:
            reforecast_ids.append(qid)
            seen.add(qid)
    if not reforecast_ids:
        return markets
    extra: list[dict[str, Any]] = []
    for qid in reforecast_ids:
        try:
            detail = market_detail(qid)
        except Exception:  # noqa: BLE001
            continue
        if detail.get("id"):
            extra.append(detail)
    if extra:
        print(f"phase 2: re-forecasting {len(extra)} moved position(s) before new markets")
    return (extra + markets)[: args.limit]


def run(args: argparse.Namespace) -> int:
    if getattr(args, "provider", "subscription") != "subscription":
        print("Manifold is subscription-only; refusing a non-subscription provider")
        return 2
    budget = float(getattr(args, "budget", MAX_CREDIT_BUDGET_USD) or 0.0)
    if (not math.isfinite(budget) or budget <= 0
            or budget > MAX_CREDIT_BUDGET_USD + BUDGET_EPSILON_USD):
        print(f"credit budget must be > $0 and <= ${MAX_CREDIT_BUDGET_USD:.2f}")
        return 2
    auth_error = subscription_auth_error(
        bool(getattr(args, "require_subscription_auth", False))
    )
    if auth_error:
        print(f"subscription-auth preflight failed: {auth_error}")
        return 2
    deadline_minutes = float(getattr(args, "deadline_minutes", 0.0) or 0.0)
    deadline = (
        time.monotonic() + deadline_minutes * 60 if deadline_minutes > 0 else None
    )
    budget_state: dict[str, Any] = {
        "usd": 0.0,
        "uncertain": False,
        "subscription_deferred": False,
        "budget_deferred": False,
    }
    print(f"Claude subscription credit cap: ${budget:.2f} this run")

    config = run_bot.load_config()
    journal_path = args.journal or str(DEFAULT_JOURNAL)
    Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
    journal = Journal(journal_path)

    # ---- phase state machine: evaluate + journal promotions at the START of the run -------
    # Lazy import avoids an import cycle (score_manifold imports run_manifold at its top).
    from score_manifold import fetch_market_state

    # One cached live-state lookup shared by promotion evaluation, phase-2 position
    # management, and the exposure computation: each market is fetched at most once per run.
    state_cache: dict[str, dict[str, Any]] = {}

    def live_state(market_id: str) -> dict[str, Any]:
        if market_id not in state_cache:
            state_cache[market_id] = fetch_market_state(market_id)
        return state_cache[market_id]

    phase_path = Path(args.phase_file or DEFAULT_PHASE_FILE)
    state = load_phase(phase_path)
    records_before = list(journal)
    state, transitions = evaluate_promotions(state, records_before, live_state)
    for t in transitions:
        print(f"PHASE TRANSITION -> {t['to']} at {t['at']}: {json.dumps(t['evidence'])}")
    save_phase(phase_path, state)
    phase = int(state["phase"])
    killed = bool(state["killed"])
    print(f"phase {phase}"
          + (" — KILLED (betting disabled permanently; forecasting continues)"
             if killed else ""))

    api_key = manifold_api_key()
    # Phase 0 forces dry-run regardless of --live; a killed bot never bets again.
    can_post = bool(args.live and api_key) and phase >= 1 and not killed
    if args.live and phase == 0:
        print("phase 0 — forcing dry-run regardless of --live (zero-mana validation)")
    if args.live and killed:
        print("kill criterion met — betting stays disabled; running forecast-only")
    if args.live and not api_key and phase >= 1 and not killed:
        print("--live given but no Manifold key found (MANIFOLD_API_KEY env or "
              f"{KEYFILE}) — falling back to dry-run "
              "(would-be bets are journaled, nothing is POSTed)")
        # Machine-readable degradation marker: a --live phase>=1 run that CANNOT bet prints
        # one (the workflow alert step greps stdout for "BETTING-DISABLED"). Legitimate
        # zero-bet states — phase 0, killed, no eligible markets, no divergence — never do.
        print("BETTING-DISABLED: no-key")

    already = already_bet_market_ids(journal)
    balance: float | None = None
    if can_post:
        try:
            balance = get_balance(api_key)
            print(f"account balance: {balance:.0f} mana")
        except Exception as exc:  # noqa: BLE001 — a balance read failure just blocks betting
            print(f"could not read balance ({exc}); betting disabled this run")
            print("BETTING-DISABLED: balance-read")
            can_post = False
    # Refuse ALL betting below the 1,100-mana floor (50% of the 2,200 adoption bankroll).
    if can_post and balance is not None and balance < PHASE1_BALANCE_FLOOR:
        print(f"balance {balance:.0f} below the {PHASE1_BALANCE_FLOOR:.0f}-mana floor — "
              "refusing all betting this run")
        print("BETTING-DISABLED: below-floor")
        can_post = False

    # Re-forecast dedupe: markets whose journaled pair is < REFORECAST_DEDUPE_DAYS old are
    # excluded from selection itself (not just skipped after), so the hourly batch fills
    # with markets the run can actually forecast [AMENDED 2026-07-20 — see gather_markets].
    fresh_pairs = recently_forecast_market_ids(records_before)
    markets = gather_markets(args.limit, exclude=fresh_pairs)
    # Phase 2 manages open positions (convergence exit + re-forecast) before new markets.
    if phase == 2 and not killed:
        markets = _manage_phase2_positions(
            records_before, markets, args,
            api_key=api_key, can_post=can_post, state_lookup=live_state,
        )
    print(f"selected {len(markets)} market(s)")
    if not markets:
        print(f"credit usage accounted: $0.00 / ${budget:.2f}")
        return 0

    # Mana already at risk in open placed bets. On the live path the cached live-state
    # lookup lets resolved positions fall out of the cap (journal status never closes them);
    # offline/dry paths stay journal-only and make no network calls.
    exposure = open_exposure(records_before, state_lookup=live_state if can_post else None)
    bets_placed = 0
    for market in markets:
        if float(budget_state["usd"]) >= budget - BUDGET_EPSILON_USD:
            print(f"credit cap reached (${float(budget_state['usd']):.2f} accounted); "
                  "leaving remaining markets for the next hourly run")
            break
        if deadline is not None and time.monotonic() >= deadline:
            print("wall-clock deadline reached; leaving remaining markets for the next run")
            break
        market_id = str(market.get("id"))
        title = str(market.get("question", ""))[:70]
        if market_id in fresh_pairs:
            print(f"- {title!r} skip (fresh pair exists)")
            continue
        print(f"- {title!r} (p={market.get('probability')}, "
              f"vol24h={market.get('volume24Hours')})")
        blind_fc = forecast_market(
            market, "blind", args.tier, args, config, budget_state, deadline
        )
        if blind_fc is None:
            print("  skip: blind forecast failed; sighted call not started")
            continue
        sighted_fc = forecast_market(
            market, "sighted", args.tier, args, config, budget_state, deadline
        )
        if sighted_fc is None:
            print("  skip: sighted forecast failed")
            continue
        for fc in (blind_fc, sighted_fc):
            fc["tier"] = args.tier
            fc["provider"] = args.provider

        pair_id = f"{_utc_now()[:10]}-{uuid4().hex[:8]}"
        journal.append(build_record(market, blind_fc, pair_id, dry_run=not can_post))

        # Only the sighted forecast drives betting.
        p_sighted = sighted_fc["probability"]
        p_market = float(market.get("probability") or 0.0)
        market_read = sighted_fc.get("market_read")
        at_cap = bets_placed >= args.max_bets
        positioned = market_id in already or at_cap
        # [AMENDED 2026-07-11] market_read no longer gates the bet — it is journaled as a
        # preregistered hypothesis. The "informed" read is printed for the record only; the
        # bet proceeds so review can test whether informed-read bets underperform.
        if market_read == "informed":
            print("  market_read=informed (preregistered hypothesis: at review we test "
                  "whether informed-read bets underperform) — betting is no longer gated")
        # Phase 2 sizes each bet quarter-Kelly; phases 0/1 stake flat.
        if phase == 2:
            size_balance = balance if balance is not None else ADOPTION_BANKROLL
            stake = kelly_stake(p_sighted, p_market, size_balance)
        else:
            stake = args.stake
        bet = decide_bet(
            p_sighted, p_market, stake,
            balance=balance if balance is not None else MIN_BALANCE_MANA,
            already_positioned=positioned,
        )
        # Exposure cap (live only): total open exposure must stay <= 30% of balance.
        if (bet is not None and can_post and balance is not None
                and exposure + bet["stake"] > EXPOSURE_CAP_FRAC * balance):
            print(f"  exposure cap: {exposure:.0f}+{bet['stake']:.0f} mana exceeds "
                  f"{EXPOSURE_CAP_FRAC:.0%} of {balance:.0f} — skipping bet")
            bet = None
        if bet is not None:
            if can_post:
                try:
                    placed = place_bet(api_key, market_id, bet["outcome"], bet["stake"])
                    # POST /v0/bet returns the created bet object; no bet id in the body is
                    # no proof of a fill, so it takes the same conservative path as a raise.
                    if not (isinstance(placed, dict)
                            and (placed.get("betId") or placed.get("id"))):
                        raise RuntimeError(f"bet response has no id: {str(placed)[:200]}")
                    bet["status"] = "placed"
                    print(f"  BET {bet['outcome']} {bet['stake']:.0f} mana "
                          f"(p_us={p_sighted:.2f} vs market {p_market:.2f}, "
                          f"read={market_read})")
                except Exception as exc:  # noqa: BLE001 — a failed POST must not abort the run
                    # The POST may have filled before the failure (e.g. timeout after fill).
                    # Journaling the bet as status "unknown" — not dropping it — keeps the
                    # market position-guarded and the stake under the exposure cap, instead
                    # of risking a double stake once the dedupe window expires.
                    bet["status"] = "unknown"
                    print(f"  bet POST failed ({exc}); journaling bet with status=unknown")
                exposure += bet["stake"]
            else:
                print(f"  DRY-RUN would bet {bet['outcome']} {bet['stake']:.0f} mana "
                      f"(p_us={p_sighted:.2f} vs market {p_market:.2f}, read={market_read})")
            bets_placed += 1
            already.add(market_id)
        journal.append(
            build_record(market, sighted_fc, pair_id, dry_run=not can_post, bet=bet)
        )
        print(f"  journaled pair {pair_id} (blind {blind_fc['probability']:.2f} / "
              f"sighted {p_sighted:.2f})")

    mode = "live" if can_post else "dry-run"
    print(f"done ({mode}, phase {phase}): {len(markets)} market(s), {bets_placed} bet(s)")
    qualifier = " (unknown usage reserved)" if budget_state["uncertain"] else ""
    print(f"credit usage accounted: ${float(budget_state['usd']):.2f} / ${budget:.2f}"
          f"{qualifier}")
    if budget_state.get("budget_deferred"):
        print("BUDGET-DEFER: Claude --max-budget-usd cap reached; remaining allowance "
              "reserved; next hourly tick will retry")
        return 0
    if budget_state.get("subscription_deferred"):
        print("SUBSCRIPTION-DEFER: Claude session limit; next hourly tick will retry")
        return 0
    return 1 if budget_state["uncertain"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20,
                        help="how many markets to select and forecast (default 20)")
    parser.add_argument("--tier", default="medium", choices=["low", "medium", "high"],
                        help="effort tier (maps to config's tier machinery, like run_bot)")
    parser.add_argument("--live", action="store_true",
                        help="actually POST bets (needs MANIFOLD_API_KEY, phase >= 1, not "
                             "killed); default is a dry-run that journals would-be bets")
    parser.add_argument("--stake", type=float, default=25.0,
                        help="flat mana stake per bet in phase 1 (default 25); phase 2 sizes "
                             "quarter-Kelly and ignores this")
    parser.add_argument("--max-bets", dest="max_bets", type=int, default=MAX_BETS_PER_RUN,
                        help=f"cap on bets placed per run (default {MAX_BETS_PER_RUN})")
    parser.add_argument("--provider", default="subscription", choices=("subscription",),
                        help="fixed subscription billing path (Manifold never uses a "
                             "metered provider)")
    parser.add_argument("--budget", type=float, default=MAX_CREDIT_BUDGET_USD,
                        help=f"Claude subscription credit cap for this invocation; hard "
                             f"maximum ${MAX_CREDIT_BUDGET_USD:.2f}")
    parser.add_argument("--deadline-minutes", type=float, default=0.0,
                        help="stop starting/continuing agent calls after this wall-clock "
                             "window (0 = no deadline; cloud uses 45)")
    parser.add_argument("--require-subscription-auth", action="store_true",
                        help="require explicit CLAUDE_CODE_OAUTH_TOKEN (for unattended cloud "
                             "runs; local cached subscription login remains supported)")
    parser.add_argument("--timeout", type=int, default=1200, help="seconds per agent call")
    parser.add_argument("--agent-cmd", dest="agent_cmd", default=DEFAULT_AGENT_CMD,
                        help="headless agent command (default mirrors run_bot's)")
    parser.add_argument("--journal", default=None,
                        help=f"journal path (default {DEFAULT_JOURNAL})")
    parser.add_argument("--phase-file", dest="phase_file", default=None,
                        help=f"phase state file (default {DEFAULT_PHASE_FILE})")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
