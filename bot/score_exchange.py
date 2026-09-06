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
GATE_MIN_BETS = 200          # paper bets with a scored closing line
GATE_MIN_SETTLED = 100       # settled paper bets for the P&L and Brier legs
GATE_BRIER_MARGIN = 0.01     # sighted Brier must beat the book mid by at least this
GATE_CI = 0.90               # bootstrap interval on mean CLV must exclude zero (lower > 0)
MOVEMENT_AGE_DAYS = 7.0
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


def _parse(value: Any) -> datetime | None:
    return run_exchange._parse_iso(value)


# --------------------------------------------------------------------------- assembly


def pairs_from_journal(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """pair_id -> {"blind": row, "sighted": row, "contract_id": ..., "venue": ...}"""
    pairs: dict[str, dict[str, Any]] = {}
    for r in rows:
        src = r.get("source") or {}
        pid = src.get("pair_id")
        if not pid or src.get("mode") not in ("blind", "sighted"):
            continue
        p = pairs.setdefault(pid, {"contract_id": src.get("question_id"),
                                   "venue": src.get("platform")})
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
        if entry_at and at and at <= entry_at:
            continue
        if s.get("status") == "open" and s.get("mid") is not None:
            best = s
    return best


def settlement(snaps: list[dict[str, Any]]) -> bool | None:
    for s in reversed(snaps):
        if s.get("outcome") is not None:
            return bool(s["outcome"])
    return None


def score_pair(pair: dict[str, Any], snaps: list[dict[str, Any]],
               now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    sighted, blind = pair["sighted"], pair.get("blind")
    src = sighted.get("source") or {}
    entry_at = _parse(sighted.get("forecast_at") or sighted.get("created"))
    mid0 = (src.get("book") or {}).get("mid")
    if mid0 is None and isinstance(sighted.get("crowd"), dict):
        mid0 = sighted["crowd"].get("value")
    out: dict[str, Any] = {
        "pair_id": src.get("pair_id"), "contract_id": pair["contract_id"],
        "venue": pair["venue"], "question": sighted.get("question", "")[:90],
        "p_sighted": sighted.get("probability"),
        "p_blind": blind.get("probability") if blind else None,
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
        scored: dict[str, Any] = {"outcome": bet["outcome"], "stake_gbp": bet["stake_gbp"],
                                  "price": bet["price"], "capped_by": bet.get("capped_by")}
        if close is not None:
            scored["clv"] = clv(float(bet["price"]), float(close["mid"]), str(bet["outcome"]))
            scored["clv_points"] = (side_price(float(close["mid"]), str(bet["outcome"]))
                                    - float(bet["price"]))
        if outcome is not None:
            scored["pnl_gbp"] = paper_pnl(bet, outcome)
            scored["won"] = scored["pnl_gbp"] > 0
        out["bet"] = scored
    return out


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bets = [r["bet"] for r in rows if "bet" in r]
    clvs = [b["clv"] for b in bets if "clv" in b]
    pnls = [b["pnl_gbp"] for b in bets if "pnl_gbp" in b]
    staked = [b["stake_gbp"] for b in bets if "pnl_gbp" in b]
    bs = [r["brier_sighted"] for r in rows if "brier_sighted" in r]
    bb = [r["brier_blind"] for r in rows if "brier_blind" in r]
    bm = [r["brier_mid"] for r in rows if "brier_mid" in r and "brier_sighted" in r]
    bs_paired = [r["brier_sighted"] for r in rows if "brier_mid" in r and "brier_sighted" in r]
    moves = [r["movement_sighted"] for r in rows if "movement_sighted" in r]
    ci = bootstrap_ci(clvs)
    return {
        "n_pairs": len(rows),
        "n_resolved": sum(1 for r in rows if r.get("resolved")),
        "n_bets": len(bets),
        "n_bets_clv": len(clvs),
        "mean_clv": mean(clvs),
        "clv_ci90": list(ci) if ci else None,
        "clv_hit_rate": (sum(1 for c in clvs if c > 0) / len(clvs)) if clvs else None,
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
        "clv_positive_ci": bool(agg["clv_ci90"]) and agg["clv_ci90"][0] > 0,
        "enough_settled": agg["n_settled_bets"] >= GATE_MIN_SETTLED,
        "roi_positive": (agg["roi"] or 0.0) > 0,
        "brier_beats_mid": (agg["brier_sighted_vs_mid_delta"] is not None
                            and agg["brier_sighted_vs_mid_delta"] <= -GATE_BRIER_MARGIN),
    }
    if all(checks.values()):
        status = "GO-LIVE-CANDIDATE"
    elif checks["enough_bets"] and (agg["mean_clv"] or 0.0) <= 0:
        status = "KILL"
    else:
        status = "HOLD"
    return {"status": status, "checks": checks,
            "rule": (f"n_clv>={GATE_MIN_BETS} & CLV CI{int(GATE_CI * 100)} lower>0 & "
                     f"settled>={GATE_MIN_SETTLED} & ROI>0 & Brier(sighted)-Brier(mid)"
                     f"<=-{GATE_BRIER_MARGIN}")}


def score(journal_rows: list[dict[str, Any]], price_rows: list[dict[str, Any]],
          now: datetime | None = None) -> dict[str, Any]:
    pairs = pairs_from_journal(journal_rows)
    snaps = snapshots_by_contract(price_rows)
    rows = [score_pair(p, snaps.get(str(p["contract_id"]), []), now) for p in pairs.values()]
    by_venue = {}
    for venue in sorted({r["venue"] for r in rows if r.get("venue")}):
        by_venue[venue] = aggregate([r for r in rows if r.get("venue") == venue])
    pooled = aggregate(rows)
    return {"rows": rows, "by_venue": by_venue, "pooled": pooled, "verdict": verdict(pooled)}


# --------------------------------------------------------------------------- output


def _f(value: Any, spec: str = ".3f") -> str:
    return "  n/a" if value is None else format(value, spec)


def render(result: dict[str, Any]) -> str:
    lines = ["EXCHANGE PAPER SCOREBOARD", ""]
    header = (f"{'venue':<10}{'pairs':>6}{'res':>5}{'bets':>6}{'clv_n':>6}{'meanCLV':>9}"
              f"{'CI90':>18}{'hit':>6}{'settled':>8}{'P&L GBP':>9}{'ROI':>7}{'Bs':>7}{'Bb':>7}"
              f"{'Bmid':>7}")
    lines.append(header)
    for venue, agg in list(result["by_venue"].items()) + [("POOLED", result["pooled"])]:
        ci = agg["clv_ci90"]
        ci_s = f"[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci else "n/a"
        lines.append(
            f"{venue:<10}{agg['n_pairs']:>6}{agg['n_resolved']:>5}{agg['n_bets']:>6}"
            f"{agg['n_bets_clv']:>6}{_f(agg['mean_clv'], '+.3f'):>9}{ci_s:>18}"
            f"{_f(agg['clv_hit_rate'], '.2f'):>6}{agg['n_settled_bets']:>8}"
            f"{_f(agg['pnl_gbp'], '+.0f'):>9}{_f(agg['roi'], '+.2f'):>7}"
            f"{_f(agg['brier_sighted']):>7}{_f(agg['brier_blind']):>7}{_f(agg['brier_mid']):>7}"
        )
    v = result["verdict"]
    lines += ["", f"VERDICT: {v['status']}   rule: {v['rule']}",
              "checks: " + ", ".join(f"{k}={'ok' if ok else 'no'}"
                                     for k, ok in v["checks"].items())]
    pooled = result["pooled"]
    if pooled["n_movement"]:
        lines.append(f"movement toward sighted (>= {MOVEMENT_AGE_DAYS:.0f}d or settled): "
                     f"{_f(pooled['mean_movement_sighted'], '+.3f')} over {pooled['n_movement']}")
    return "\n".join(lines)


def refresh_snapshots(journal_path: Path, prices_path: Path) -> int:
    rows = run_exchange.journal_rows(journal_path)
    tracked = run_exchange.open_contract_ids(rows, settled=run_exchange.settled_ids(prices_path))
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
    parser.add_argument("--refresh", action="store_true",
                        help="pull live quotes for open contracts and append snapshots first")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.refresh:
        n = refresh_snapshots(Path(args.journal), Path(args.prices))
        print(f"refreshed {n} snapshot(s)", file=sys.stderr)
    result = score(run_exchange.journal_rows(args.journal), run_exchange.journal_rows(args.prices))
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
