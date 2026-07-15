"""Reproduce the 2026-07-15 MiniBench bot-vs-community diagnostic.

The 15 updated values were supplied by the operator from the MiniBench UI.  They are
closed forecasts, not resolved outcomes, and the community values may not be timestamp-
matched to the bot submissions.  Accordingly this script computes disagreement and
dispersion signatures only; it never calls them errors or scores.

The two prior numeric rows and five prior binary rows are the values already frozen in
``docs/tournament-analysis.html``.  They are used only for explicitly labeled historical
direction/width checks.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Binary:
    name: str
    bot: float
    community: float
    category: str
    known_fixed_bug: bool = False

    @property
    def delta(self) -> float:
        return self.bot - self.community


@dataclass(frozen=True)
class Numeric:
    name: str
    bot_lo: float
    bot_median: float
    bot_hi: float
    community_lo: float
    community_median: float
    community_hi: float
    category: str
    batch: str = "current"

    @property
    def bot_width(self) -> float:
        return self.bot_hi - self.bot_lo

    @property
    def community_width(self) -> float:
        return self.community_hi - self.community_lo

    @property
    def width_ratio(self) -> float:
        return self.bot_width / self.community_width


CURRENT_BINARIES = (
    Binary("Pogačar Stage 19", 36.2, 33.5, "one-off outcome"),
    Binary("NBA Clippers/Aspiration investigation", 10.8, 25.4, "institution/status"),
    Binary("Starship Flight 13", 79.6, 75.7, "one-off outcome"),
    Binary("US–Iran direct talks", 9.0, 14.5, "institution/status"),
    Binary("NVDA closes above $225", 31.0, 32.1, "barrier"),
    Binary("Spain LOIA total amendment", 10.8, 15.0, "institution/status"),
    Binary("Uganda reports Ebola case 21", 12.3, 18.0, "repeated trigger"),
    Binary("SOL closes above $85", 14.3, 28.0, "barrier/crypto"),
    Binary("SK Hynix Q2 earnings release", 13.2, 62.0, "institution/status"),
)

PRIOR_BINARIES = (
    Binary("AI funding: Lovable/DeepSeek/Perplexity", 8.0, 31.1, "deadline", True),
    Binary("AI funding: OpenAI/Anthropic/Mistral", 38.3, 25.0, "deadline"),
    Binary("Lebanese Armed Forces deploy", 19.2, 31.5, "institution/status"),
    Binary("France >1 Ebola case", 9.2, 15.0, "repeated trigger"),
    Binary("SCOTUS retirement", 12.0, 15.0, "institution/status"),
)

CURRENT_NUMERICS = (
    Numeric("US wildfire acres (millions)", 4.05, 4.25, 4.50, 3.85, 4.20, 4.60,
            "other"),
    Numeric("ECB EUR/USD", 1.14, 1.14, 1.15, 1.13, 1.14, 1.15, "other"),
    Numeric("Death Valley maximum (°F)", 120.0, 122.0, 124.0, 120.0, 123.0, 126.0,
            "other"),
    Numeric("El Palmito fill (%)", 21.3, 24.0, 26.3, 19.1, 22.1, 26.1, "other"),
    Numeric("TAC DeFi TVL ($)", 572_000.0, 682_000.0, 818_000.0,
            392_000.0, 672_000.0, 1_100_000.0, "crypto/on-chain"),
    Numeric("Solana RWA value ($bn)", 3.45, 3.55, 3.71, 3.40, 3.64, 3.97,
            "crypto/on-chain"),
)

PRIOR_NUMERICS = (
    Numeric("Copper August high", 6.05, 6.25, 6.45, 6.04, 6.31, 6.58,
            "other", "prior"),
    Numeric("Liquid sulphur Q3", 740.0, 860.0, 970.0, 566.0, 761.0, 910.0,
            "other", "prior"),
)


def pearson(xs: list[float], ys: list[float]) -> float:
    """Population correlation for equal-length nonconstant vectors."""
    xbar, ybar = st.mean(xs), st.mean(ys)
    numerator = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys, strict=True))
    denominator = math.sqrt(
        sum((x - xbar) ** 2 for x in xs) * sum((y - ybar) ** 2 for y in ys)
    )
    return numerator / denominator


def ranks(values: list[float]) -> list[float]:
    """Average ranks, including ties."""
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    out = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        rank = (start + 1 + end) / 2
        for index, _value in ordered[start:end]:
            out[index] = rank
        start = end
    return out


def one_sided_sign_probability(successes: int, n: int) -> float:
    """P[Binomial(n, .5) >= successes]."""
    return sum(math.comb(n, k) for k in range(successes, n + 1)) / (2**n)


def summarize_binaries(rows: tuple[Binary, ...] = CURRENT_BINARIES) -> dict[str, Any]:
    deltas = [row.delta for row in rows]
    absolute = [abs(delta) for delta in deltas]
    ordered = sorted(rows, key=lambda row: abs(row.delta), reverse=True)
    non_sk = [row for row in rows if not row.name.startswith("SK Hynix")]
    below = sum(delta < 0 for delta in deltas)
    return {
        "n": len(rows),
        "below": below,
        "mean_signed_pp": st.mean(deltas),
        "median_signed_pp": st.median(deltas),
        "mean_absolute_pp": st.mean(absolute),
        "median_absolute_pp": st.median(absolute),
        "top3_absolute_share": sum(abs(row.delta) for row in ordered[:3]) / sum(absolute),
        "largest": [
            {"name": row.name, "delta_pp": row.delta} for row in ordered[:3]
        ],
        "same_modal_outcome": sum((row.bot >= 50) == (row.community >= 50) for row in rows),
        "pearson_excluding_sk": pearson(
            [row.bot for row in non_sk], [row.community for row in non_sk]
        ),
        "spearman_excluding_sk": pearson(
            ranks([row.bot for row in non_sk]),
            ranks([row.community for row in non_sk]),
        ),
        "one_sided_sign_probability": one_sided_sign_probability(below, len(rows)),
    }


def summarize_numerics(rows: tuple[Numeric, ...] = CURRENT_NUMERICS) -> dict[str, Any]:
    ratios = [row.width_ratio for row in rows]
    normalized_locations = [
        abs(row.bot_median - row.community_median) / row.community_width for row in rows
    ]
    return {
        "n": len(rows),
        "all_bot_narrower": all(row.bot_width < row.community_width for row in rows),
        "bot_narrower_count": sum(row.bot_width < row.community_width for row in rows),
        "mean_width_ratio": st.mean(ratios),
        "median_width_ratio": st.median(ratios),
        "bot_median_inside_community": sum(
            row.community_lo <= row.bot_median <= row.community_hi for row in rows
        ),
        "community_median_inside_bot": sum(
            row.bot_lo <= row.community_median <= row.bot_hi for row in rows
        ),
        "bot_interval_nested": sum(
            row.community_lo <= row.bot_lo and row.bot_hi <= row.community_hi
            for row in rows
        ),
        "median_normalized_location_shift": st.median(normalized_locations),
        "max_normalized_location_shift": max(normalized_locations),
        "rows": [
            {
                **asdict(row),
                "bot_width": row.bot_width,
                "community_width": row.community_width,
                "width_ratio": row.width_ratio,
            }
            for row in rows
        ],
    }


def full_summary() -> dict[str, Any]:
    current_binary = summarize_binaries()
    current_numeric = summarize_numerics()
    all_numeric = summarize_numerics(CURRENT_NUMERICS + PRIOR_NUMERICS)
    crypto = summarize_numerics(
        tuple(row for row in CURRENT_NUMERICS if row.category == "crypto/on-chain")
    )
    noncrypto = summarize_numerics(
        tuple(row for row in CURRENT_NUMERICS if row.category != "crypto/on-chain")
    )
    combined_below = sum(row.delta < 0 for row in CURRENT_BINARIES + PRIOR_BINARIES)
    return {
        "current_binary": current_binary,
        "current_numeric": current_numeric,
        "current_plus_prior_numeric": all_numeric,
        "crypto_numeric": crypto,
        "noncrypto_numeric": noncrypto,
        "current_plus_prior_binary_below": combined_below,
        "current_plus_prior_binary_n": len(CURRENT_BINARIES + PRIOR_BINARIES),
        "caveats": [
            "No question has resolved; disagreement is not forecast error.",
            "Community values may not be timestamp-matched to bot submission times.",
            "Displayed numeric intervals and values are UI-rounded and may aggregate "
            "forecasters differently from a single model distribution.",
            "The prior binary direction count includes one known fixed event-window bug.",
        ],
    }


def render(summary: dict[str, Any]) -> str:
    binary = summary["current_binary"]
    numeric = summary["current_numeric"]
    combined = summary["current_plus_prior_numeric"]
    crypto = summary["crypto_numeric"]
    noncrypto = summary["noncrypto_numeric"]
    lines = [
        "MiniBench diagnostic — closed forecasts, unresolved outcomes",
        "",
        "binary disagreement",
        f"below community: {binary['below']}/{binary['n']} "
        f"(one-sided sign p={binary['one_sided_sign_probability']:.3f})",
        f"mean signed: {binary['mean_signed_pp']:+.2f} pp; "
        f"mean absolute: {binary['mean_absolute_pp']:.2f} pp",
        f"top three share of absolute disagreement: {binary['top3_absolute_share']:.1%}",
        f"same modal outcome: {binary['same_modal_outcome']}/{binary['n']}",
        f"excluding SK Hynix: Pearson={binary['pearson_excluding_sk']:.3f}, "
        f"Spearman={binary['spearman_excluding_sk']:.3f}",
        "",
        "numeric displayed-interval diagnostic",
        f"current bot intervals narrower: {numeric['bot_narrower_count']}/{numeric['n']}; "
        f"mean ratio={numeric['mean_width_ratio']:.3f}, "
        f"median={numeric['median_width_ratio']:.3f}",
        f"bot medians inside community interval: "
        f"{numeric['bot_median_inside_community']}/{numeric['n']}",
        f"current + prior bot intervals narrower: "
        f"{combined['bot_narrower_count']}/{combined['n']}; "
        f"mean ratio={combined['mean_width_ratio']:.3f}",
        f"crypto/on-chain mean ratio={crypto['mean_width_ratio']:.3f}; "
        f"other current numerics={noncrypto['mean_width_ratio']:.3f}",
        "",
        "boundaries",
    ]
    lines.extend(f"- {caveat}" for caveat in summary["caveats"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = full_summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False) if args.json else render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
