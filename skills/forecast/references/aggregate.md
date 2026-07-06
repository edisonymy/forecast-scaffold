# aggregate.md — draws, pooling, and the crowd

Aggregation is the most reliable accuracy lever in the field: even running the same forecaster
twice measurably improves the Brier score, and a pool of a dozen diverse models has matched a
900-person human crowd. The math lives in `fsj.py aggregate` — this file is about producing draws
worth pooling and choosing the right pool.

## Producing draws

A draw = one full pass of the reasoning spine ending in one probability. Draw count per tier comes
from config. The value of an ensemble is the **diversity of its errors** — and the evidence is
specific about where diversity actually comes from: different *models* first, different
*analytical lenses* second, temperature/rewording resamples a distant third (cosmetic persona
prompts: measured effect nil). Copying your first number N times is not an ensemble.

**Every draw estimates the same unconditional probability.** Assign each draw a different **lens**
— where the reasoning *starts*, never what is being estimated. Lens wordings must be **neutral**:
name both failure directions and pre-judge nothing about the dossier — a lens that hints which way
the anchor is wrong manufactures the very movement it claims to detect. Lenses are suggestions,
not scripts — a draw may swap its lens for a better angle, as long as the estimate is its own.
The set (the harness rotation leads with the counter-biasing opposite pair, so lean run counts
stay directionally neutral): **reference-class check** (2+ candidate classes with rates, at least one broader and one
narrower than any the dossier offers; self-constructed rates marked unverified and down-weighted;
the right class may well be the dossier's), **consider-the-opposite in each direction** (strongest
specific reasons the estimate is too high / too low, then estimate), **decomposition** (≤ 4
components; multiplying long chains drifts low, ignoring correlation drifts high; recompose,
cross-check holistically), **premortem** (assume your first instinct proved badly wrong, write
how, estimate fresh). Whether re-derive-the-anchor lenses beat perspective lenses is an **open
question** — one live probe suggested attitude lenses inherit a prominently-placed dossier base
rate, but it was n=1 with arguably leading wordings; the resolved-Brier battery that decides it is
preregistered before any reshuffle ships. Do **not** condition draws on a scenario ("assume the
premortem happened") — that produces P(X | scenario), and pooling conditionals as if they were
estimates of P(X) is a category error.

Terminology, since three things get called "draws": *in-context draws* (one context window
producing several estimates — the correlated, degraded kind), *subagent/run draws* (one estimate
per separate context — the real kind), and the tool's generic `--draws` flag, which pools whatever
numbers you hand it regardless of provenance.

**The fan-out protocol (any surface with subagents — Claude Code, Cowork, harnesses).** This is
the primary mechanism, not a fallback; in-context draws are the degraded mode. Audited runs show
in-context draws collapse to one estimate ± noise (2–5 point spreads) while separate contexts on
the same evidence swing 2–3× wider. Research is done ONCE — the best published pipelines share
one retrieval across all reasoning calls; duplicated research buys correlated facts, not
independent judgment. The steps:

1. **Write the dossier** from your Step 2 research: 5–15 terse evidence bullets each with source
   and date, the status-quo outcome, base rates found (with source **and the class each is
   computed over** — when a conditioning variable is already known, carry the conditional or
   component rates too, never a single broad unconditional rate: one prominently-placed rate is
   an anchor, and an anchor shared by every subagent collapses the ensemble the same way a
   shared estimate would), the resolution-instrument line, and what you searched for but
   couldn't find — evidence for both directions. **No probability, no lean, no telegraphing
   adjectives** ("likely", "slim"). Sharing *facts* is nearly free but seeing another
   forecaster's *estimate* is the correlation that kills an ensemble — and a lone base rate is
   an estimate wearing a source citation. If you already formed a number while researching,
   keep it out.
2. **Fan out k parallel subagents** (k = config's `runs` for the tier — run
   `python fsj.py config` if you haven't this session), each given: question +
   verbatim criterion + resolve-by + the dossier + ONE suggested lens (a diversity device — the
   subagent may swap it for a better angle; the estimate must be its own) + this instruction: do
   not research further; reason from the dossier and your general knowledge; if a fact that would
   materially move the estimate is missing, stay closer to the base rate and report the gap; reply
   with a probability at 1% granularity, a 3-line rationale, and the **named-scenarios
   disclosure** — the pathways it considered to the opposite resolution from its lean, each with
   the probability mass it actually assigns ([] if nothing distinct points the other way).
   Subagents never see each other's output or yours. Use different models per subagent when the
   surface allows it.
3. **Pool** with `fsj.py aggregate --method geo_mean_odds` — untrimmed since v0.4.0: a
   rank-symmetric trim is logit-asymmetric near the boundary, so on one-sided pools it measurably
   moved the pool *toward* the extreme while deleting the dissenting draw (at n=4 it kept only
   the middle two). Use `median` if a draw is outright contaminated; don't trim healthy pools.
   Never extremize: with a shared dossier the information overlap is ~1, and the
   theory says the optimal extremizing factor at overlap 1 is none.
4. **Check the disclosure arithmetic, then read the spread.** A draw that names pathways to the
   opposite resolution must leave at least that much mass there — p=0.03 alongside named
   YES-pathways totaling 0.14 is incoherent, and it is exactly the audited tail failure ("named
   it, didn't price it"). Flag it and re-read that draw's rationale; never silently override the
   number. Then the spread: judge it in odds terms at the tails (0.02 vs 0.10 is a 5× odds
   disagreement even though the probability spread is only 8 points). A wide spread means the
   lenses found a genuine crux — name it, and consider one targeted research pass on it before
   recording. A 2–3 point spread from genuinely separate contexts is fine (agreement is
   informative when it wasn't enforced); a 2–3 point spread from in-context draws is one draw
   wearing k hats.

**No subagents available** (a plain chat): run the lens set as in-context draws, pool with
`trimmed_mean`, and tell the user: "draws were in-context (correlated) — treat the error bars as
wider than usual."

## Choosing the pool

| Situation | Method | Why |
|---|---|---|
| Your own draws (one forecaster, varied framings) | `trimmed_mean` (default) | Correlated draws share their information; trimming is the right robustification. **Never extremize your own draws** — extremizing assumes independent private information, which self-ensembles don't have; it just double-counts. |
| Genuinely independent forecasters (different models/agents, separate contexts) | `--method geo_mean_odds` | Geometric mean of odds, untrimmed — the pooling rule of the best-track-record human teams minus their extreme-drop, which was calibrated on ~7 genuinely diverse humans and measurably extremizes small correlated pools (pass `drop_extremes` only for a pool that actually looks like theirs). |
| Skewed or contaminated draw set | `--method median` | Robust fallback. |

## The crowd

If a community prediction or market price exists, capture it with its timestamp and pass
`--crowd`. Blending with the crowd has beaten both the system and the crowd alone — and the
validated optimum puts most of the weight on the CROWD (Halawi et al.: "4x weight for the crowd
... optimal on the validation set"; config default 0.8). The crowd is an anchor, not an opponent;
your edge case is the exception, not the rule. Three rules:

- **Disagreement is a stop sign, not a triumph.** If your aggregate is far from a liquid crowd
  number, either they know something (find it) or you do (name it, and be able to defend it). Only
  then proceed. Large model-vs-crowd disagreement is itself informative — in hybrid studies the
  model-side view won most direction-conflicts — but only when the disagreement was researched,
  not asserted.
- **Check staleness before trusting the anchor.** A crowd number is evidence as of its timestamp:
  if a decisive event postdates it (a ruling, a resolution-relevant announcement), the anchor may
  be dead — our worst measured "miss" was a correct forecast indicted by a three-week-old frozen
  market value. Metaculus's community prediction is recency-managed by the platform; frozen
  benchmark values and thin markets are not.
- The pre-blend aggregate, the crowd value, and the blended result all belong in the record
  (`--draws`, `--crowd-value`, final `--probability`) so the track record can later show whether
  your edge over the crowd was real.

## Clamp and overrides

`aggregate` clamps the final probability into the configured band and says so; the record command
warns when a probability sits outside it. The clamp encodes measured tail overconfidence — but
tails that are too *thin* are also a documented failure. Overriding the band is legitimate exactly
when the reasoning summary cites decisive evidence for the extreme; do it consciously, never
silently.

## Consistency checks (high tier)

Before recording, sanity-check the numbers against themselves: complementary framings should sum
to ~1 (if you'd forecast "will X NOT happen" at anything other than 1−p, find out why); a
probability over a longer horizon must be ≥ the same event over a shorter one; multiple-choice
probabilities must sum to 1 (the tool enforces this). Cheap, model-independent, and they catch
real errors every model generation.
