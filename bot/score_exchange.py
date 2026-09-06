"""Score the exchange PAPER journal: closing-line value, settled P&L, Brier vs the book.

Offline by default — everything it needs is in the two files the paper bot commits:

  bot/journal/exchange.jsonl         blind + sighted forecasts and the paper bet per pair
  bot/journal/exchange-prices.jsonl  book snapshots of every tracked contract, per run

Metrics, per venue and pooled:

  * CLV (closing-line value) per paper bet: q_close / q_entry - 1, where q is the price of
    the side we took (YES: the back price; NO: 1 - lay price) and q_close is the same side's
    MID at the last open snapshot before the market closed. Positive = the market moved our
    way by the close. In the betting literature CLV is the fastest, least noisy test of a
    real edge: it needs no settlement and is unaffected by variance in outcomes.
  * Settled paper P&L, net of the venue's commission on winnings, and ROI on stakes.
  * Brier at settlement for the blind forecast, the sighted forecast, and the book mid at
    entry — the three-way comparison that says whether the bot ADDS to the price.
  * Movement toward the sighted forecast after >= 7 days (Manifold's phase-1 signal).

The preregistered decision rule (docs/exchange-paper-policy.md) is computed here, never by
eye. ``--refresh`` pulls live quotes for still-open contracts and appends snapshots first,
so the scorer can also run standalone between bot ticks.

Usage:
    python bot/score_exchange.py
    python bot/score_exchange.py --refresh --json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

# ruff: noqa: E402  (imports follow the sys.path bootstrap above)
import exchanges
import run_exchange

DEFAULT_JOURNAL = run_exchange.DEFAULT_JOURNAL
DEFAULT_PRICES = run_exchange.DEFAULT_PRICES

# ---- preregistered go-live gate (docs/exchange-paper-policy.md; change there first) -------
# The gate is computed on ONE population only: taker fills at the touch, held to settlement,
# every paper bet (no venue, screen, or execution-arm sub-population), and on ABSOLUTE
# closing-line value in probability points — relative CLV (q_close/q_entry - 1) has a
# variance dominated by low-priced bets and would let a few longshots decide the verdict.
# Every other comparison the scorer prints is descriptive (red team 2026-09-06: with
# n ~ 200 nothing else is powered, and 16 sub-populations under a one-sided 5% gate is a
# 30-55% family-wise false-GO rate).
GATE_MIN_BETS = 200          # paper bets with a scored closing line
GATE_MIN_SETTLED = 100       # settled paper bets for the P&L and Brier legs
GATE_BRIER_MARGIN = 0.01     # sighted Brier must beat the book mid by at least this
GATE_CI = 0.90               # bootstrap interval on mean CLV POINTS must exclude zero
MOVEMENT_AGE_DAYS = 7.0
ENTRY_GRACE_SECONDS = 60.0   # snapshots this close to entry are the entry book, not a close
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 7


# --------------------------------------------------------------------------- math (pure)


def brier(p: float, outcome: bool) -> float:
    return (p - (1.0 if outcome else 0.0)) ** 2


def side_price(mid: float, side: str) -> float:
    return mid if side == "YES" else 1.0 - mid


def clv(entry_price: float, close_mid: float, side: str) -> float:
    """q_close / q_entry - 1 on the side taken; positive when the close favours us."""
    return side_price(close_mid, side) / entry_price - 1.0


def paper_pnl(bet: dict[str, Any], outcome: bool) -> float:
    """Settled P&L of one paper bet, net of commission (folded into ``net_odds_b``)."""
    won = outcome if bet["outcome"] == "YES" else not outcome
    stake = float(bet["stake_gbp"])
    return stake * float(bet["net_odds_b"]) if won else -stake


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def bootstrap_ci(values: list[float], level: float = GATE_CI, draws: int = BOOTSTRAP_DRAWS,
                 seed: int = BOOTSTRAP_SEED) -> tuple[float, float] | None:
    """Percentile bootstrap interval on the mean; None below n=2."""
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(draws))
    lo = means[int((1.0 - level) / 2.0 * (draws - 1))]
    hi = means[int((1.0 + level) / 2.0 * (draws - 1))]
    return lo, hi


def pearson(xy: list[tuple[float, float]]) -> float | None:
    """Pearson correlation of (x, y) pairs; None below n=3 or with a constant column."""
    n = len(xy)
    if n < 3:
        return None
    mx = sum(x for x, _ in xy) / n
    my = sum(y for _, y in xy) / n
    sxx = sum((x - mx) ** 2 for x, _ in xy)
    syy = sum((y - my) ** 2 for _, y in xy)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in xy) / (sxx * syy) ** 0.5


def _parse(value: Any) -> datetime | None:
    return run_exchange._parse_iso(value)


# --------------------------------------------------------------------------- assembly


def pairs_from_journal(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """pair_id -> {"blind": row, "sighted": row, "contract_id": ..., "venue": ...}"""
    pairs: dict[str, dict[str, Any]] = {}
    for r in rows:
        src = r.get("source") or {}
        pid = src.get("pair_id")
        if not pid or src.get("mode") not in ("blind", "sighted", "proxy"):
            continue
        p = pairs.setdefault(pid, {"contract_id": src.get("question_id"),
                                   "venue": src.get("platform"),
                                   "reforecast_of": src.get("reforecast_of")})
        p[src["mode"]] = r
    return {k: v for k, v in pairs.items() if "sighted" in v}


def snapshots_by_contract(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("contract_id") and r.get("at"):
            out.setdefault(str(r["contract_id"]), []).append(r)
    for snaps in out.values():
        snaps.sort(key=lambda s: str(s["at"]))
    return out


def closing_snapshot(snaps: list[dict[str, Any]],
                     entry_at: datetime | None) -> dict[str, Any] | None:
    """The last snapshot with a two-sided open book, taken AFTER entry. When the contract has
    settled, that is the closing line; while it is open, it is the line so far."""
    best = None
    for s in snaps:
        at = _parse(s.get("at"))
        if entry_at and at and (at - entry_at).total_seconds() < ENTRY_GRACE_SECONDS:
            continue  # the entry book itself (or a same-tick re-quote) is not a closing line
        if s.get("status") == "open" and s.get("mid") is not None:
            best = s
    return best


def settlement(snaps: list[dict[str, Any]]) -> bool | None:
    for s in reversed(snaps):
        if s.get("outcome") is not None:
            return bool(s["outcome"])
    return None


def score_pair(pair: dict[str, Any], snaps: list[dict[str, Any]],
               now: datetime | None = None,
               all_snaps: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    sighted, blind = pair["sighted"], pair.get("blind")
    src = sighted.get("source") or {}
    entry_at = _parse(sighted.get("forecast_at") or sighted.get("created"))
    mid0 = (src.get("book") or {}).get("mid")
    if mid0 is None and isinstance(sighted.get("crowd"), dict):
        mid0 = sighted["crowd"].get("value")
    proxy = pair.get("proxy")
    out: dict[str, Any] = {
        "pair_id": src.get("pair_id"), "contract_id": pair["contract_id"],
        "venue": pair["venue"], "question": sighted.get("question", "")[:90],
        "p_sighted": sighted.get("probability"),
        "p_blind": blind.get("probability") if blind else None,
        "p_proxy": proxy.get("probability") if proxy else None,
        "mid_entry": mid0, "entry_at": sighted.get("forecast_at"),
    }
    outcome = settlement(snaps)
    close = closing_snapshot(snaps, entry_at)
    out["resolved"] = outcome is not None
    if outcome is not None:
        out["outcome"] = outcome
        out["brier_sighted"] = brier(float(sighted["probability"]), outcome)
        if blind and blind.get("probability") is not None:
            out["brier_blind"] = brier(float(blind["probability"]), outcome)
        if proxy and proxy.get("probability") is not None:
            out["brier_proxy"] = brier(float(proxy["probability"]), outcome)
        if mid0 is not None:
            out["brier_mid"] = brier(float(mid0), outcome)
    if close is not None and mid0 is not None and out["p_sighted"] is not None:
        p_now = float(close["mid"])
        p_us = float(out["p_sighted"])
        close_at = _parse(close["at"]) or now
        age = (close_at - entry_at).total_seconds() / 86_400 if entry_at else None
        out["close_mid"] = p_now
        out["close_age_days"] = age
        if abs(p_us - float(mid0)) >= run_exchange.DIVERGENCE_THRESHOLD and age is not None \
                and (age >= MOVEMENT_AGE_DAYS or outcome is not None):
            sign = 1.0 if p_us >= float(mid0) else -1.0
            out["movement_sighted"] = sign * (p_now - float(mid0))
    bet = src.get("paper_bet")
    if isinstance(bet, dict) and bet.get("price"):
        # A routed bet lives on its own contract (the twin venue): score it against THAT
        # contract's snapshots, which the runner writes alongside the forecast contract's.
        bet_cid = str(bet.get("contract_id") or pair["contract_id"])
        routed = bet_cid != pair["contract_id"]
        if routed:
            # A routed bet is scored ONLY against its own venue's snapshots; with none it
            # gets no closing line rather than the other venue's.
            bet_snaps = (all_snaps or {}).get(bet_cid, [])
            bet_close = closing_snapshot(bet_snaps, entry_at)
            bet_outcome = settlement(bet_snaps)
        else:
            bet_snaps, bet_close, bet_outcome = snaps, close, outcome
        side = str(bet["outcome"])
        scored: dict[str, Any] = {"outcome": side, "stake_gbp": bet["stake_gbp"],
                                  "price": bet["price"], "capped_by": bet.get("capped_by"),
                                  "venue": bet.get("venue") or pair["venue"],
                                  "longshot": bool(bet.get("longshot")),
                                  "routed": routed}
        if bet_close is not None:
            scored["clv"] = clv(float(bet["price"]), float(bet_close["mid"]), side)
            scored["clv_points"] = (side_price(float(bet_close["mid"]), side)
                                    - float(bet["price"]))
        if bet_outcome is not None:
            scored["pnl_gbp"] = paper_pnl(bet, bet_outcome)
            scored["won"] = scored["pnl_gbp"] > 0
        # Descriptive arms (never in the gate): maker fill, exit-past-fair-value.
        entry_book = bet.get("book") or src.get("book") or {}
        maker = maker_fill(bet, bet_snaps, entry_at, entry_last=entry_book.get("last"))
        if maker is not None:
            scored["maker"] = maker
            if maker.get("filled") and bet_close is not None:
                scored["maker"]["clv_points"] = (side_price(float(bet_close["mid"]), side)
                                                 - float(maker["price"]))
            if maker.get("filled") and bet_outcome is not None:
                scored["maker"]["pnl_gbp"] = paper_pnl(
                    {**bet, "price": maker["price"], "net_odds_b": maker["net_odds_b"]},
                    bet_outcome)
        p_us = out["p_sighted"]
        if p_us is not None:
            exit_arm = exit_past_fair_value(bet, float(p_us), bet_snaps, entry_at)
            if exit_arm is not None:
                scored["exit"] = exit_arm
        out["bet"] = scored
    return out


def maker_fill(bet: dict[str, Any], snaps: list[dict[str, Any]],
               entry_at: datetime | None, entry_last: float | None = None,
               ) -> dict[str, Any] | None:
    """Lower-bound fill test for the maker quote: FILLED only when a later snapshot shows a
    NEW last-traded price (different from the previous observation) that printed STRICTLY
    through our resting price within the TTL. A touch that merely crossed our price is not
    a fill (most top-of-book moves in politics books are pulls, not trades), a print exactly
    at our price is not either (queue ahead), and an unchanged ``last`` is a stale print
    from before entry, not a trade."""
    maker = bet.get("maker")
    if not isinstance(maker, dict) or maker.get("prob") is None:
        return None
    side = str(bet["outcome"])
    q = float(maker["prob"])
    ttl = float(maker.get("ttl_days") or run_exchange.MAKER_TTL_DAYS)
    commission = float(bet.get("commission") or 0.0)
    price = float(maker.get("price") or (q if side == "YES" else 1.0 - q))
    result: dict[str, Any] = {"price": price, "prob": q, "improved": bool(maker.get("improved")),
                              "filled": False,
                              "net_odds_b": round((1.0 / price - 1.0) * (1.0 - commission), 4)}
    prev_last = entry_last
    for snap in snaps:
        at = _parse(snap.get("at"))
        if entry_at and at and at <= entry_at:
            continue
        if entry_at and at and (at - entry_at).total_seconds() > ttl * 86_400:
            break
        last = snap.get("last")
        if last is None:
            continue
        if prev_last is not None and abs(float(last) - float(prev_last)) < 1e-9:
            continue  # same print as before: no evidence of a trade
        prev_last = float(last)
        through = float(last) < q - 1e-9 if side == "YES" else float(last) > q + 1e-9
        if through:
            result["filled"] = True
            result["filled_at"] = snap.get("at")
            break
    return result


def exit_past_fair_value(bet: dict[str, Any], p_us: float, snaps: list[dict[str, Any]],
                         entry_at: datetime | None) -> dict[str, Any] | None:
    """Counterfactual: close the position at the first snapshot where the EXECUTABLE exit
    price sits at or past our own fair value — a YES is sold into the best lay when
    lay.prob >= p_us; a NO is bought back at the best back when back.prob <= p_us. Exiting
    earlier pays the spread again to free idle capital (red team 2026-09-06); exiting here
    banks a price we ourselves call fair. Commission on the venue's net market profit."""
    side = str(bet["outcome"])
    q_entry = float(bet["price"])
    stake = float(bet["stake_gbp"])
    c = float(bet.get("commission") or 0.0)
    for snap in snaps:
        at = _parse(snap.get("at"))
        if entry_at and at and at <= entry_at:
            continue
        if snap.get("status") != "open":
            continue
        back, lay = snap.get("back"), snap.get("lay")
        if side == "YES" and lay and lay["prob"] >= p_us:
            value = lay["prob"] / q_entry
        elif side == "NO" and back and back["prob"] <= p_us:
            value = (1.0 - back["prob"]) / q_entry
        else:
            continue
        gross = stake * (value - 1.0)
        pnl = gross * (1.0 - c) if gross > 0 else gross
        days = ((at - entry_at).total_seconds() / 86_400) if (at and entry_at) else None
        return {"exited_at": snap.get("at"), "pnl_gbp": round(pnl, 2),
                "capital_days": round(stake * days, 1) if days is not None else None}
    return None


def stop_loss_counterfactual(original: dict[str, Any], reforecast: dict[str, Any],
                             snaps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """For a re-forecast triggered by an adverse move: what closing the original bet at the
    executable side of the snapshot nearest the re-forecast would have returned, versus
    holding (settled P&L when known). Descriptive."""
    bet = (original.get("source") or {}).get("paper_bet")
    if not isinstance(bet, dict) or not bet.get("price"):
        return None
    at = _parse(reforecast.get("forecast_at") or reforecast.get("created"))
    if at is None:
        return None
    nearest = None
    for snap in snaps:
        s_at = _parse(snap.get("at"))
        if s_at and s_at <= at and snap.get("status") == "open":
            nearest = snap
    if nearest is None:
        return None
    side = str(bet["outcome"])
    q_entry, stake = float(bet["price"]), float(bet["stake_gbp"])
    c = float(bet.get("commission") or 0.0)
    back, lay = nearest.get("back"), nearest.get("lay")
    if side == "YES" and lay:
        value = lay["prob"] / q_entry
    elif side == "NO" and back:
        value = (1.0 - back["prob"]) / q_entry
    else:
        return None
    gross = stake * (value - 1.0)
    out: dict[str, Any] = {"pair_id": (original.get("source") or {}).get("pair_id"),
                           "reforecast_pair_id": (reforecast.get("source") or {}).get("pair_id"),
                           "p_reforecast": reforecast.get("probability"),
                           "stop_loss_pnl_gbp": round(gross * (1.0 - c) if gross > 0 else gross, 2)}
    outcome = settlement(snaps)
    if outcome is not None:
        out["hold_pnl_gbp"] = round(paper_pnl(bet, outcome), 2)
    return out


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bets = [r["bet"] for r in rows if "bet" in r]
    clvs = [b["clv"] for b in bets if "clv" in b]
    clv_pts = [b["clv_points"] for b in bets if "clv_points" in b]
    # Shadow-proxy diagnostic: does |p_proxy - mid| predict the bet's CLV? A correlation
    # near zero says a screen on the proxy would have selected noise; descriptive only.
    proxy_xy = [(abs(float(r["p_proxy"]) - float(r["mid_entry"])), r["bet"]["clv_points"])
                for r in rows if r.get("p_proxy") is not None and r.get("mid_entry") is not None
                and "bet" in r and "clv_points" in r["bet"]]
    pnls = [b["pnl_gbp"] for b in bets if "pnl_gbp" in b]
    staked = [b["stake_gbp"] for b in bets if "pnl_gbp" in b]
    bs = [r["brier_sighted"] for r in rows if "brier_sighted" in r]
    bb = [r["brier_blind"] for r in rows if "brier_blind" in r]
    bm = [r["brier_mid"] for r in rows if "brier_mid" in r and "brier_sighted" in r]
    bs_paired = [r["brier_sighted"] for r in rows if "brier_mid" in r and "brier_sighted" in r]
    moves = [r["movement_sighted"] for r in rows if "movement_sighted" in r]
    ci = bootstrap_ci(clv_pts)
    bp = [r["brier_proxy"] for r in rows if "brier_proxy" in r]
    # Execution arms, paired within bet (descriptive).
    makers = [b["maker"] for b in bets if "maker" in b]
    maker_filled = [b for b in bets if b.get("maker", {}).get("filled")]
    maker_pair_pts = [(b["maker"]["clv_points"], b["clv_points"]) for b in maker_filled
                      if "clv_points" in b["maker"] and "clv_points" in b]
    maker_pair_pnl = [(b["maker"]["pnl_gbp"], b["pnl_gbp"]) for b in maker_filled
                      if "pnl_gbp" in b["maker"] and "pnl_gbp" in b]
    exits = [b for b in bets if "exit" in b]
    exit_pair_pnl = [(b["exit"]["pnl_gbp"], b["pnl_gbp"]) for b in exits if "pnl_gbp" in b]
    longshots = [b for b in bets if b.get("longshot")]
    routed = [b for b in bets if b.get("routed")]
    return {
        "n_maker": len(makers), "n_maker_filled": len(maker_filled),
        "maker_fill_rate": (len(maker_filled) / len(makers)) if makers else None,
        "maker_minus_taker_clv_points": mean([m - t for m, t in maker_pair_pts]),
        "maker_minus_taker_pnl_gbp": sum(m - t for m, t in maker_pair_pnl),
        "n_exit": len(exits), "n_exit_settled": len(exit_pair_pnl),
        "exit_minus_hold_pnl_gbp": sum(e - h for e, h in exit_pair_pnl),
        "exit_pnl_gbp": sum(b["exit"]["pnl_gbp"] for b in exits),
        "n_longshot": len(longshots),
        "longshot_pnl_gbp": sum(b["pnl_gbp"] for b in longshots if "pnl_gbp" in b),
        "n_routed": len(routed),
        "n_pairs": len(rows),
        "n_resolved": sum(1 for r in rows if r.get("resolved")),
        "n_bets": len(bets),
        "n_bets_clv": len(clv_pts),
        "mean_clv": mean(clvs),
        "mean_clv_points": mean(clv_pts),
        "clv_points_ci90": list(ci) if ci else None,
        "clv_hit_rate": (sum(1 for c in clv_pts if c > 0) / len(clv_pts)) if clv_pts else None,
        "brier_proxy": mean(bp),
        "proxy_clv_corr": pearson(proxy_xy), "n_proxy": len(proxy_xy),
        "n_settled_bets": len(pnls),
        "pnl_gbp": sum(pnls),
        "roi": (sum(pnls) / sum(staked)) if staked and sum(staked) > 0 else None,
        "win_rate": (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else None,
        "brier_sighted": mean(bs), "brier_blind": mean(bb),
        "brier_mid": mean(bm), "brier_sighted_vs_mid_delta": (
            (mean(bs_paired) or 0.0) - (mean(bm) or 0.0)) if bm else None,
        "n_brier": len(bs),
        "mean_movement_sighted": mean(moves), "n_movement": len(moves),
    }


def verdict(agg: dict[str, Any]) -> dict[str, Any]:
    """The preregistered rule, evaluated. Three outcomes only."""
    checks = {
        "enough_bets": agg["n_bets_clv"] >= GATE_MIN_BETS,
        "clv_positive_ci": bool(agg["clv_points_ci90"]) and agg["clv_points_ci90"][0] > 0,
        "enough_settled": agg["n_settled_bets"] >= GATE_MIN_SETTLED,
        "roi_positive": (agg["roi"] or 0.0) > 0,
        "brier_beats_mid": (agg["brier_sighted_vs_mid_delta"] is not None
                            and agg["brier_sighted_vs_mid_delta"] <= -GATE_BRIER_MARGIN),
    }
    if all(checks.values()):
        status = "GO-LIVE-CANDIDATE"
    elif checks["enough_bets"] and (agg["mean_clv_points"] or 0.0) <= 0:
        status = "KILL"
    else:
        status = "HOLD"
    return {"status": status, "checks": checks,
            "rule": (f"n_clv>={GATE_MIN_BETS} & CLV-points CI{int(GATE_CI * 100)} lower>0 & "
                     f"settled>={GATE_MIN_SETTLED} & ROI>0 & Brier(sighted)-Brier(mid)"
                     f"<=-{GATE_BRIER_MARGIN}")}


def lock_summary(lock_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-venue locks observed, and how many PERSISTED across consecutive ticks — a
    lock that survives two pulls minutes apart is almost always a settlement-rule
    mismatch, which is what this ledger is for."""
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for r in lock_rows:
        key = "|".join(r.get("pair") or [])
        if key:
            by_pair.setdefault(key, []).append(r)
    persistent = 0
    for rows in by_pair.values():
        rows.sort(key=lambda r: str(r.get("at")))
        ticks = sorted({str(r.get("at"))[:13] for r in rows})  # hour buckets
        if len(ticks) >= 2:
            persistent += 1
    returns = [float(r.get("lock_return_on_capital") or 0.0) for r in lock_rows]
    return {"n_observations": len(lock_rows), "n_pairs": len(by_pair),
            "n_persistent_pairs": persistent, "mean_lock_return": mean(returns)}


def score(journal_rows: list[dict[str, Any]], price_rows: list[dict[str, Any]],
          now: datetime | None = None,
          lock_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    pairs = pairs_from_journal(journal_rows)
    snaps = snapshots_by_contract(price_rows)
    primary = {k: v for k, v in pairs.items() if not v.get("reforecast_of")}
    rows = [score_pair(p, snaps.get(str(p["contract_id"]), []), now, all_snaps=snaps)
            for p in primary.values()]
    # Re-forecast pairs: the stop-loss counterfactual against their original bet.
    stop_losses: list[dict[str, Any]] = []
    for p in pairs.values():
        orig_id = p.get("reforecast_of")
        if not orig_id or orig_id not in pairs:
            continue
        orig = pairs[orig_id]["sighted"]
        bet = (orig.get("source") or {}).get("paper_bet") or {}
        cid = str(bet.get("contract_id") or pairs[orig_id]["contract_id"])
        sl = stop_loss_counterfactual(orig, p["sighted"], snaps.get(cid, []))
        if sl:
            stop_losses.append(sl)
    by_venue = {}
    for venue in sorted({r["venue"] for r in rows if r.get("venue")}):
        by_venue[venue] = aggregate([r for r in rows if r.get("venue") == venue])
    pooled = aggregate(rows)
    pooled["n_reforecast"] = len(stop_losses)
    settled_sl = [x for x in stop_losses if "hold_pnl_gbp" in x]
    pooled["stop_loss_minus_hold_pnl_gbp"] = sum(
        x["stop_loss_pnl_gbp"] - x["hold_pnl_gbp"] for x in settled_sl)
    return {"rows": rows, "by_venue": by_venue, "pooled": pooled, "verdict": verdict(pooled),
            "stop_losses": stop_losses, "locks": lock_summary(lock_rows or [])}


# --------------------------------------------------------------------------- output


def _f(value: Any, spec: str = ".3f") -> str:
    return "  n/a" if value is None else format(value, spec)


def render(result: dict[str, Any]) -> str:
    lines = ["EXCHANGE PAPER SCOREBOARD", ""]
    header = (f"{'venue':<10}{'pairs':>6}{'res':>5}{'bets':>6}{'clv_n':>6}{'CLVpts':>9}"
              f"{'CI90':>18}{'hit':>6}{'settled':>8}{'P&L GBP':>9}{'ROI':>7}{'Bs':>7}{'Bb':>7}"
              f"{'Bmid':>7}")
    lines.append(header)
    for venue, agg in list(result["by_venue"].items()) + [("POOLED", result["pooled"])]:
        ci = agg["clv_points_ci90"]
        ci_s = f"[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci else "n/a"
        lines.append(
            f"{venue:<10}{agg['n_pairs']:>6}{agg['n_resolved']:>5}{agg['n_bets']:>6}"
            f"{agg['n_bets_clv']:>6}{_f(agg['mean_clv_points'], '+.3f'):>9}{ci_s:>18}"
            f"{_f(agg['clv_hit_rate'], '.2f'):>6}{agg['n_settled_bets']:>8}"
            f"{_f(agg['pnl_gbp'], '+.0f'):>9}{_f(agg['roi'], '+.2f'):>7}"
            f"{_f(agg['brier_sighted']):>7}{_f(agg['brier_blind']):>7}{_f(agg['brier_mid']):>7}"
        )
    v = result["verdict"]
    lines += ["", f"VERDICT: {v['status']}   rule: {v['rule']}",
              "checks: " + ", ".join(f"{k}={'ok' if ok else 'no'}"
                                     for k, ok in v["checks"].items())]
    pooled = result["pooled"]
    lines.append("")
    lines.append("DESCRIPTIVE ARMS (never in the gate)")
    if pooled["n_maker"]:
        lines.append(f"maker (lower-bound fills): {pooled['n_maker_filled']}/{pooled['n_maker']} "
                     f"filled ({_f(pooled['maker_fill_rate'], '.0%')}); vs taker on the same bets:"
                     f" CLV pts {_f(pooled['maker_minus_taker_clv_points'], '+.3f')}, "
                     f"P&L GBP {pooled['maker_minus_taker_pnl_gbp']:+.0f}")
    if pooled["n_exit"]:
        lines.append(f"exit past fair value: {pooled['n_exit']} exits, P&L GBP "
                     f"{pooled['exit_pnl_gbp']:+.0f}; vs hold on {pooled['n_exit_settled']} "
                     f"settled: GBP {pooled['exit_minus_hold_pnl_gbp']:+.0f}")
    if pooled["n_longshot"]:
        lines.append(f"longshot lays: {pooled['n_longshot']}, settled P&L GBP "
                     f"{pooled['longshot_pnl_gbp']:+.0f}")
    if pooled.get("n_routed"):
        lines.append(f"routed to the twin venue: {pooled['n_routed']} bet(s)")
    if pooled.get("n_reforecast"):
        lines.append(f"re-forecasts on adverse moves: {pooled['n_reforecast']}; stop-loss vs "
                     f"hold GBP {pooled['stop_loss_minus_hold_pnl_gbp']:+.0f}")
    locks = result.get("locks") or {}
    if locks.get("n_observations"):
        lines.append(f"cross-venue lock ledger: {locks['n_observations']} observation(s) on "
                     f"{locks['n_pairs']} pair(s), {locks['n_persistent_pairs']} persistent "
                     f"(check rules), mean lock {_f(locks['mean_lock_return'], '+.2%')}")
    if pooled["n_proxy"]:
        lines.append(f"shadow proxy (descriptive): corr(|p_proxy-mid|, CLV pts) = "
                     f"{_f(pooled['proxy_clv_corr'], '+.2f')} over {pooled['n_proxy']} bets; "
                     f"Brier(proxy) {_f(pooled['brier_proxy'])}")
    if pooled["n_movement"]:
        lines.append(f"movement toward sighted (>= {MOVEMENT_AGE_DAYS:.0f}d or settled): "
                     f"{_f(pooled['mean_movement_sighted'], '+.3f')} over {pooled['n_movement']}")
    return "\n".join(lines)


def refresh_snapshots(journal_path: Path, prices_path: Path) -> int:
    rows = run_exchange.journal_rows(journal_path)
    tracked = run_exchange.tracked_contract_ids(rows, settled=run_exchange.settled_ids(prices_path))
    if not tracked:
        return 0
    quotes = exchanges.quote_contracts(tracked)
    snaps = run_exchange.snapshot_rows(quotes)
    run_exchange.append_snapshots(prices_path, snaps)
    return len(snaps)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--prices", default=str(DEFAULT_PRICES))
    parser.add_argument("--arbs", default=str(run_exchange.DEFAULT_ARBS))
    parser.add_argument("--refresh", action="store_true",
                        help="pull live quotes for open contracts and append snapshots first")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.refresh:
        n = refresh_snapshots(Path(args.journal), Path(args.prices))
        print(f"refreshed {n} snapshot(s)", file=sys.stderr)
    result = score(run_exchange.journal_rows(args.journal), run_exchange.journal_rows(args.prices),
                   lock_rows=run_exchange.journal_rows(args.arbs))
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
