"""Platform-exact scoring for MiniBench MULTIPLE-CHOICE questions.

MC is ~15% of a wave and had no scorer: ``minibench_counterfactuals.py`` handles binaries
and numerics, ``minibench_numeric_tails.py`` handles the continuous log-density. This file
closes the gap. As with the numeric file, the formula here is NOT derived by intuition —
the 2026-07-26 correction (100*log2 where the platform uses 50*ln, a 2.885x overstatement
of every numeric figure) is the standing reminder that platform formulas get READ FROM THE
SOURCE and then validated against cells the platform itself scored.

VERIFIED FORMULA (Metaculus/metaculus @ main, fetched 2026-08-09)
  scoring/score_math.py :: evaluate_forecasts_baseline_spot_forecast  (and the identical
  branch in evaluate_forecasts_baseline_accuracy), the ``question_type in ["binary",
  "multiple_choice"]`` arm, verbatim:

      options_at_time = sum(~np.isnan(pmf))
      p = pmf[resolution_bucket] if not np.isnan(pmf[resolution_bucket]) else pmf[-1]
      forecast_score = 100 * np.log(p * options_at_time) / np.log(options_at_time)

  so  baseline = 100 * ln(p_outcome * N) / ln(N) = 100 * log2(p_outcome * N) / log2(N)
  (the ratio of logs is base-invariant, so the log2 form of the hypothesis was right).
  Properties that fall out: a uniform 1/N forecast scores exactly 0 for every N, and
  N=2 collapses to the binary case 100 * (1 + log2 p) since binary's pmf is [1-p, p].

  N is ``options_at_time``, the count of options ACTIVE at forecast time, not the count
  the question ended with: ``Forecast.get_pmf`` (questions/models.py) writes float("nan")
  into slots for options that were not yet available, and the sum counts the non-nan
  slots. Every MiniBench MC row we have carries a probability for every option, so here
  N == len(options); the helper still takes an explicit N so a future options_history
  question can pass the smaller count.

  resolution_bucket for MC is just the option's INDEX in the full option history
  (utils/the_math/formulas.py :: string_location_to_scaled_location ->
  scaled_location_to_unscaled_location -> unscaled_location_to_bucket_index; all three
  are identity/index passthroughs for MULTIPLE_CHOICE). No bucket-edge subtlety as in
  the continuous case.

CLIPPING: the scoring code does NO clipping and NO renormalization — it consumes the
stored vector as submitted. The clip lives one layer up, at submission, and it is a
REJECTION not a clamp: questions/serializers/common.py ::
ForecastSerializer.multiple_choice_validation raises "Probabilities for current options
must be between 0.001 and 0.999" for any active option outside [0.001, 0.999], and
raises again unless ``np.isclose(sum(values), 1)``. So a p=0 forecast cannot exist on the
platform. ``clip_renormalize`` below mirrors what a submission harness must do to get
accepted (clip to [0.001, 0.999], then renormalize to sum 1) and is applied before
scoring so that a hypothetical p=0 scores the worst score the platform would ever pay,
100*ln(0.001*N)/ln(N), instead of -inf. Our own submissions never trip it: every MC row
in the journal already sums to 1.0 with min p = 0.002.

PEER (for reference; this file reports baseline, MiniBench's leaderboard pays peer)
  score_math.py :: evaluate_forecasts_peer_spot_forecast:

      forecast_score = 100 * (gm.num_forecasters / (gm.num_forecasters - 1)) * np.log(p / gmp)
      if question_type in QUESTION_CONTINUOUS_TYPES: forecast_score /= 2

  The working hypothesis "peer = 50 * (ln p - field mean ln p)" was WRONG ON TWO COUNTS
  for MC: (a) the /2 applies to CONTINUOUS types only, so MC peer carries 100, not 50;
  (b) there is a small-field correction factor n/(n-1), and the reference gmp is the
  GEOMETRIC mean of the field's pmfs at that slot (``gmean`` over forecasters in
  get_geometric_means), which is exp(mean ln p) — so the "mean of logs" intuition was
  right about the reference point and wrong about the coefficients.

BASELINE vs SPOT BASELINE: ``evaluate_forecasts_baseline_accuracy`` multiplies the same
per-forecast score by ``forecast_coverage = forecast_duration / total_duration`` and sums
over a user's forecasts, where total_duration spans question open -> scheduled close. We
open one forecast per question, late, and never update, so coverage < 1 and the
time-averaged baseline_score is exactly spot_baseline_score * coverage. Verified on all
four cells below to 1e-15 (e.g. qid 44983: 61.48595 * 0.9481597 = 58.29850, the platform's
own baseline_score). This file reproduces the SPOT value; multiply by the platform's
reported coverage if you want the accuracy variant.

FOUR-CELL VALIDATION against platform-scored submissions of our own (wave2 census,
my_score_data.spot_baseline_score from the Metaculus API):

    qid    N  p_win  ours       platform   delta
    44983  3  0.655  61.48595   61.48595    0.0e+00
    45003  6  0.870  92.22764   92.22764    0.0e+00
    45007  5  0.900  93.45358   93.45358    0.0e+00
    45021  3  0.820  81.93622   81.93622   -1.4e-14

  Max |delta| 1.4e-14, i.e. float noise; the tolerance asked for was 0.05. These four
  cells are hardcoded in tests/test_minibench_mc.py so the formula cannot silently drift.

Usage:
    python bench/analysis/minibench_mc.py --resolutions FILE.json [FILE2.json ...]
                                          [--journal bot/journal/forecasts.jsonl]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"

# questions/serializers/common.py :: ForecastSerializer.multiple_choice_validation
PLATFORM_MIN_P = 0.001
PLATFORM_MAX_P = 0.999

# questions/constants.py :: UnsuccessfulResolutionType — never scored (score_math.py's
# string_location_to_bucket_index returns None and evaluate_question bails out).
UNSCORED_RESOLUTIONS = {"annulled", "ambiguous"}


def clip_renormalize(probs: list[float]) -> list[float]:
    """The submission-side constraint, applied as a clamp: [0.001, 0.999] then sum to 1.

    The platform REJECTS out-of-range vectors rather than fixing them (see module
    docstring), so this only ever fires on hypothetical/counterfactual vectors. It keeps
    p=0 scoring at the platform's worst payable score instead of -inf.
    """
    clipped = [min(max(p, PLATFORM_MIN_P), PLATFORM_MAX_P) for p in probs]
    total = sum(clipped)
    if total <= 0:
        raise ValueError("probability vector sums to zero")
    return [p / total for p in clipped]


def mc_baseline_score(probs: list[float], winner: int, n_options: int | None = None) -> float:
    """Metaculus's MC baseline score in leaderboard points (score_math.py, see docstring):

        100 * ln(p_winner * N) / ln(N)

    ``n_options`` defaults to len(probs); pass it explicitly to reproduce a question whose
    options_history had fewer options active at forecast time (the platform's
    ``options_at_time = sum(~np.isnan(pmf))``).
    """
    n = len(probs) if n_options is None else n_options
    if n < 2:
        raise ValueError(f"multiple choice needs >= 2 options, got {n}")
    if not 0 <= winner < len(probs):
        raise ValueError(f"winner index {winner} out of range for {len(probs)} options")
    p = clip_renormalize(probs)[winner]
    return 100.0 * math.log(p * n) / math.log(n)


def mc_peer_score(p: float, field_geometric_mean_p: float, n_forecasters: int) -> float:
    """Metaculus's MC/binary peer score (score_math.py::evaluate_forecasts_peer_spot_forecast):

        100 * (n / (n - 1)) * ln(p / gmp)

    NOT halved — the ``/= 2`` in that function applies to QUESTION_CONTINUOUS_TYPES only.
    ``gmp`` is the geometric mean of the field's probabilities on the resolved option,
    i.e. exp(mean ln p) over forecasters.
    """
    if n_forecasters < 2:
        raise ValueError("peer score is undefined with fewer than 2 forecasters")
    return 100.0 * (n_forecasters / (n_forecasters - 1)) * math.log(p / field_geometric_mean_p)


def load_mc_rows(journal: Path) -> list[dict]:
    """Latest journaled MC forecast per question id, by ``forecast_at``."""
    rows = [json.loads(line) for line in journal.open(encoding="utf-8") if line.strip()]
    rows.sort(key=lambda r: str(r.get("forecast_at")), reverse=True)
    latest: dict[int, dict] = {}
    for row in rows:
        if row.get("question_type") != "multiple_choice":
            continue
        qid = (row.get("source") or {}).get("question_id")
        if qid is None or qid in latest:
            continue
        if not row.get("options") or not row.get("probabilities"):
            continue
        latest[qid] = row
    return list(latest.values())


def load_resolutions(paths: list[Path]) -> dict[int, str]:
    """Merge {qid_str: outcome} files, keeping only STRING outcomes (the MC ones).

    Numeric/binary outcomes in the same files belong to the other scorers; annulled and
    ambiguous resolutions are dropped because the platform does not score them either.
    """
    out: dict[int, str] = {}
    for path in paths:
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            if isinstance(value, str) and value.strip().lower() not in UNSCORED_RESOLUTIONS:
                out[int(key)] = value
    return out


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def match_option(resolution: str, options: list[str]) -> tuple[int | None, str]:
    """Resolve a platform outcome label to an option index. Returns (index, how).

    Exact first, then whitespace/case-insensitive exact, then a UNIQUE prefix match in
    either direction (the platform truncates long option labels in some payloads, and our
    own stored label can be the longer one). Ambiguity is reported, never guessed.
    """
    if resolution in options:
        return options.index(resolution), "exact"
    target = _norm(resolution)
    normed = [_norm(o) for o in options]
    hits = [i for i, o in enumerate(normed) if o == target]
    if len(hits) == 1:
        return hits[0], "casefold"
    if len(hits) > 1:
        return None, f"ambiguous casefold match ({len(hits)} options)"
    hits = [i for i, o in enumerate(normed) if o.startswith(target) or target.startswith(o)]
    if len(hits) == 1:
        return hits[0], "prefix"
    if len(hits) > 1:
        return None, f"ambiguous prefix match ({len(hits)} options)"
    return None, "no match"


def score_wave(rows: list[dict], resolutions: dict[int, str]) -> tuple[list[dict], list[dict]]:
    """Score every journaled MC row that has a resolution. Returns (scored, unmatched)."""
    scored, unmatched = [], []
    for row in sorted(rows, key=lambda r: r["source"]["question_id"]):
        qid = row["source"]["question_id"]
        if qid not in resolutions:
            continue
        options, probs = row["options"], row["probabilities"]
        if len(options) != len(probs):
            unmatched.append({"qid": qid, "resolution": resolutions[qid],
                              "why": f"{len(options)} options vs {len(probs)} probabilities",
                              "options": options})
            continue
        winner, how = match_option(resolutions[qid], options)
        if winner is None:
            unmatched.append({"qid": qid, "resolution": resolutions[qid],
                              "why": how, "options": options})
            continue
        ranked = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
        scored.append({
            "qid": qid,
            "question": row.get("question", ""),
            "n_options": len(options),
            "p_winner": probs[winner],
            "uniform": 1.0 / len(options),
            "score": mc_baseline_score(probs, winner),
            "winner": winner,
            "match": how,
            "argmax_hit": ranked[0] == winner,
            "top2_hit": winner in ranked[:2],
        })
    return scored, unmatched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolutions", type=Path, nargs="+", required=True,
                        help="one or more JSON files of {qid: outcome}")
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    args = parser.parse_args(argv)

    resolutions = load_resolutions(args.resolutions)
    rows = load_mc_rows(args.journal)
    scored, unmatched = score_wave(rows, resolutions)
    print(f"journaled MC questions: {len(rows)}   "
          f"string-valued (MC) resolutions supplied: {len(resolutions)}   "
          f"scored: {len(scored)}")

    if not scored:
        print("\nnothing to score")
        for miss in unmatched:
            print(f"  UNMATCHED {miss['qid']}: {miss['why']}")
        return 0

    print("\n=== per-question (Metaculus spot BASELINE, leaderboard points) ===")
    print(f"{'qid':>6} {'N':>3} {'p(win)':>7} {'1/N':>6} {'baseline':>9} {'argmax':>7}  question")
    for s in scored:
        mark = "yes" if s["argmax_hit"] else ("top2" if s["top2_hit"] else "no")
        print(f"{s['qid']:>6} {s['n_options']:>3} {s['p_winner']:>7.3f} {s['uniform']:>6.3f} "
              f"{s['score']:>+9.2f} {mark:>7}  {s['question'][:46]}")

    n = len(scored)
    scores = [s["score"] for s in scored]
    print(f"\n=== summary (n={n}) ===")
    print(f"  total baseline      {sum(scores):>+9.1f}")
    print(f"  mean baseline       {st.mean(scores):>+9.2f}   "
          f"median {st.median(scores):>+8.2f}   worst {min(scores):>+8.2f}")
    print(f"  mean p on winner    {st.mean(s['p_winner'] for s in scored):>9.3f}   "
          f"vs mean 1/N {st.mean(s['uniform'] for s in scored):>6.3f}")
    argmax = sum(1 for s in scored if s["argmax_hit"])
    top2 = sum(1 for s in scored if s["top2_hit"])
    print(f"  winner was our top pick   {argmax}/{n} ({argmax / n:.0%})")
    print(f"  winner in our top 2       {top2}/{n} ({top2 / n:.0%})")
    beat = sum(1 for s in scored if s["score"] > 0)
    print(f"  beat the uniform 1/N      {beat}/{n} ({beat / n:.0%})   "
          "(baseline > 0 iff p(win) > 1/N)")
    non_exact = [s for s in scored if s["match"] != "exact"]
    if non_exact:
        print("\n  label matched non-exactly (check these):")
        for s in non_exact:
            print(f"    {s['qid']}: {s['match']} -> option {s['winner']}")
    if unmatched:
        print(f"\n=== UNMATCHED resolutions ({len(unmatched)}) — not scored, not guessed ===")
        for miss in unmatched:
            print(f"  {miss['qid']}: {miss['why']}")
            print(f"      resolution: {miss['resolution'][:80]}")
            print(f"      options:    {[o[:34] for o in miss['options']]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
