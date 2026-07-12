"""Score the Manifold bot's blind-vs-sighted journal against live market prices.

Reads bot/journal/manifold.jsonl and, for every journaled forecast, pulls the current price
(and resolution, if any) from Manifold's public API — no auth needed. It reports, per market
and pooled:

  * movement toward us — sign(p_us - p_0) * (p_now - p_0), where p_0 is the market price at
    forecast time and p_now is today's. Positive means the market moved our way. Rows where
    our forecast barely diverged from the market (|p_us - p_0| <
    run_manifold.DIVERGENCE_THRESHOLD, currently 0.05) carry no signal and are skipped for
    this metric.
  * mark-to-market P&L for placed bets (a simple share-model proxy; ignores CPMM slippage
    and fees).
  * resolution Brier for resolved markets, BLIND and SIGHTED scored separately.

Finally it prints a blind-vs-sighted comparison table — the whole point of the A/B.

Usage:
    python bot/score_manifold.py
    python bot/score_manifold.py --journal bot/journal/manifold.jsonl --json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

# ruff: noqa: E402  (imports follow the sys.path bootstrap above)
from run_manifold import DIVERGENCE_THRESHOLD, MANIFOLD_API, UA

from forecast_scaffold.core import Journal

DEFAULT_JOURNAL = ROOT / "bot" / "journal" / "manifold.jsonl"

# State a market can be in, as the scorer needs it: current price, and (if resolved) the
# boolean outcome. ``outcome`` is None while open or on a MKT/CANCEL resolution.
MarketState = dict[str, Any]


# --------------------------------------------------------------------------- math (pure)


def movement_toward(p_us: float, p_0: float, p_now: float) -> float | None:
    """sign(p_us - p_0) * (p_now - p_0): positive when the market moved toward our forecast.

    Returns None when our forecast barely diverged from the market at forecast time
    (|p_us - p_0| < the divergence threshold) — such a row carries no directional signal.
    """
    divergence = p_us - p_0
    if abs(divergence) < DIVERGENCE_THRESHOLD:
        return None
    sign = 1.0 if divergence > 0 else -1.0
    return sign * (p_now - p_0)


def bet_pnl(outcome: str, stake: float, p_entry: float, p_now: float) -> float:
    """Mark-to-market P&L of a mana bet under a simple share model.

    YES buys ``stake / p_entry`` shares each worth ``p_now`` now (1 if it resolves YES);
    NO buys ``stake / (1 - p_entry)`` shares each worth ``1 - p_now``. For a resolved market
    pass p_now = 1.0 (YES) or 0.0 (NO). A proxy: it ignores CPMM slippage and fees.
    """
    p_entry = min(max(p_entry, 0.01), 0.99)
    if outcome == "YES":
        return stake * (p_now / p_entry - 1.0)
    return stake * ((1.0 - p_now) / (1.0 - p_entry) - 1.0)


def brier(p: float, outcome: bool) -> float:
    return (p - (1.0 if outcome else 0.0)) ** 2


# --------------------------------------------------------------------------- live state


def fetch_market_state(market_id: str) -> MarketState:
    """Current price + resolution for one market from the public API (no auth)."""
    request = urllib.request.Request(f"{MANIFOLD_API}/market/{market_id}", headers=UA)
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    resolved = bool(data.get("isResolved"))
    resolution = str(data.get("resolution") or "")
    outcome: bool | None = None
    if resolved and resolution in ("YES", "NO"):
        outcome = resolution == "YES"
    prob = data.get("resolutionProbability")
    if prob is None:
        prob = data.get("probability")
    return {
        "probability": float(prob) if prob is not None else None,
        "resolved": resolved,
        "outcome": outcome,
    }


# --------------------------------------------------------------------------- scoring


def _p0_of(record: Any) -> float | None:
    """The market price at forecast time — journaled in the crowd field."""
    crowd = record.crowd or {}
    value = crowd.get("value")
    return float(value) if value is not None else None


def score_rows(
    records: list[Any], state_lookup: Callable[[str], MarketState],
) -> dict[str, Any]:
    """Analyze every record against live/looked-up market state.

    ``state_lookup(market_id)`` returns a MarketState; injected so tests can score a
    synthetic journal with no network. Returns per-pair rows plus blind/sighted aggregates.
    """
    states: dict[str, MarketState] = {}

    def state_for(market_id: str) -> MarketState:
        if market_id not in states:
            states[market_id] = state_lookup(market_id)
        return states[market_id]

    pairs: dict[str, dict[str, Any]] = {}
    for record in records:
        src = record.source or {}
        if src.get("platform") != "manifold":
            continue
        market_id = str(src.get("question_id"))
        pair_id = str(src.get("pair_id") or record.id)
        state = state_for(market_id)
        p_us = record.probability
        p_0 = _p0_of(record)
        p_now = state.get("probability")
        row = pairs.setdefault(pair_id, {
            "pair_id": pair_id, "market_id": market_id,
            "question": record.question[:70],
            "resolved": state.get("resolved"), "outcome": state.get("outcome"),
            "p_market_0": p_0, "p_now": p_now,
        })
        side = "blind" if record.blind else "sighted"
        row[f"p_{side}"] = p_us
        if p_us is not None and p_0 is not None and p_now is not None:
            row[f"movement_{side}"] = movement_toward(p_us, p_0, p_now)
        if state.get("resolved") and state.get("outcome") is not None and p_us is not None:
            row[f"brier_{side}"] = brier(p_us, bool(state["outcome"]))
        bet = src.get("bet")
        if bet and p_us is not None:
            entry = bet.get("p_market_at_bet")
            entry = float(entry) if entry is not None else (p_0 or 0.5)
            if state.get("resolved") and state.get("outcome") is not None:
                mtm_price = 1.0 if state["outcome"] else 0.0
            else:
                mtm_price = p_now if p_now is not None else entry
            row["bet"] = {
                "outcome": bet.get("outcome"), "stake": bet.get("stake"),
                "dry_run": bet.get("dry_run"),
                "pnl": bet_pnl(str(bet.get("outcome")), float(bet.get("stake") or 0.0),
                               entry, mtm_price),
            }

    rows = list(pairs.values())
    summary = _aggregate(rows)
    return {"rows": rows, "summary": summary}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    move_blind = [r["movement_blind"] for r in rows
                  if r.get("movement_blind") is not None]
    move_sighted = [r["movement_sighted"] for r in rows
                    if r.get("movement_sighted") is not None]
    brier_blind = [r["brier_blind"] for r in rows if "brier_blind" in r]
    brier_sighted = [r["brier_sighted"] for r in rows if "brier_sighted" in r]
    pnl = [r["bet"]["pnl"] for r in rows if "bet" in r]
    return {
        "n_pairs": len(rows),
        "n_resolved": sum(1 for r in rows if r.get("resolved")),
        "mean_movement_blind": _mean(move_blind),
        "mean_movement_sighted": _mean(move_sighted),
        "n_movement_scored": {"blind": len(move_blind), "sighted": len(move_sighted)},
        "brier_blind": _mean(brier_blind),
        "brier_sighted": _mean(brier_sighted),
        "n_brier_scored": len(brier_blind),
        "total_bet_pnl": sum(pnl) if pnl else 0.0,
        "n_bets": len(pnl),
    }


def _fmt(value: Any, spec: str = ".2f") -> str:
    if value is None:
        return "  -  "
    return format(value, spec)


def render_table(result: dict[str, Any]) -> str:
    rows = result["rows"]
    lines = [
        "Manifold blind-vs-sighted scoring",
        "",
        f"{'question':<40} {'blind':>6} {'sight':>6} {'mkt0':>6} {'now':>6} "
        f"{'mv_bl':>7} {'mv_si':>7} {'br_bl':>6} {'br_si':>6}",
        "-" * 100,
    ]
    for r in sorted(rows, key=lambda x: str(x.get("question"))):
        lines.append(
            f"{str(r.get('question'))[:40]:<40} "
            f"{_fmt(r.get('p_blind')):>6} {_fmt(r.get('p_sighted')):>6} "
            f"{_fmt(r.get('p_market_0')):>6} {_fmt(r.get('p_now')):>6} "
            f"{_fmt(r.get('movement_blind'), '+.3f'):>7} "
            f"{_fmt(r.get('movement_sighted'), '+.3f'):>7} "
            f"{_fmt(r.get('brier_blind'), '.3f'):>6} {_fmt(r.get('brier_sighted'), '.3f'):>6}"
        )
        if "bet" in r:
            b = r["bet"]
            tag = "dry-run" if b.get("dry_run") else "LIVE"
            lines.append(
                f"    bet [{tag}] {b.get('outcome')} {_fmt(b.get('stake'), '.0f')} mana "
                f"-> P&L {_fmt(b.get('pnl'), '+.1f')} mana"
            )
    s = result["summary"]
    lines += [
        "-" * 100,
        f"pairs: {s['n_pairs']}  resolved: {s['n_resolved']}",
        f"mean movement-toward-us   blind {_fmt(s['mean_movement_blind'], '+.3f')} "
        f"(n={s['n_movement_scored']['blind']})   "
        f"sighted {_fmt(s['mean_movement_sighted'], '+.3f')} "
        f"(n={s['n_movement_scored']['sighted']})",
        f"resolution Brier          blind {_fmt(s['brier_blind'], '.3f')}   "
        f"sighted {_fmt(s['brier_sighted'], '.3f')}   (n={s['n_brier_scored']})",
        f"bet P&L (mark-to-market)  {_fmt(s['total_bet_pnl'], '+.1f')} mana "
        f"over {s['n_bets']} bet(s)",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--json", action="store_true", help="emit the raw analysis as JSON")
    args = parser.parse_args(argv)

    journal = Journal(args.journal)
    records = [r for r in journal if (r.source or {}).get("platform") == "manifold"]
    if not records:
        print("no Manifold records in the journal yet")
        return 0
    result = score_rows(records, fetch_market_state)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
