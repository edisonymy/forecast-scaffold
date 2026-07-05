# reasoning.md — the scratchpad spine

How to get from evidence to a probability. This encodes what measurably improves forecasts —
reference classes first, status-quo anchoring, structured disagreement with yourself, 1%
granularity — and deliberately omits what measurably doesn't (clever prompt rituals, explicit
odds-ratio arithmetic). Run the full spine per draw at medium+ tier; the short form (steps 1, 2, 3,
6) at low tier. When the Step 4 fan-out is active, "per draw" means once per subagent/run — a
fan-out member runs the spine exactly once for its assigned lens and must NOT produce its own
in-context draw ensemble on top.

## The spine (per draw)

**1. Restate.** The question, the resolution criterion **verbatim**, today's date, and time left
until resolution. Short horizons are status-quo-friendly; long horizons leave room for trends and
surprises. If restating exposes ambiguity, go back to question-hygiene before spending effort.

**2. Anchors first — before any causal story.**
- **Status quo:** what happens if nothing much changes? The world changes slowly most of the time;
  weight this outcome up, and demand a concrete, dated mechanism before betting on change.
- **Zeroth-order:** the average of the few most similar past instances.
- **First-order:** the trend through recent instances, extrapolated — after checking the trend
  hasn't saturated.
Your final answer must state why (or that) it deviates from these anchors.

**3. Outside view.** Name **at least two candidate reference classes** with a numeric base rate
each. If they disagree, don't pick the flattering one — blend them, weighted by relevance, and say
which one you'd bet on. Never invent a base rate: it comes from searched data or explicitly counted
instances. Where the platform's own history is known (on major forecasting platforms, questions
resolve YES only about a third of the time), that is itself a reference class — resist the pull to
say yes.

**4. Inside view — how is this time different?** Base rates describe a generating mechanism. Check
it still runs: a pattern that held because of some force is worthless the day the force lapses
(sanctions renewed 10 times ≠ high probability of an 11th if the trigger expired). Conversely, a
genuine structural change can justify leaving the base rate far behind — with evidence.
**Current-period override check:** when the current period's own data is silent or points the
other way from your reference class (zero events so far this year vs a multi-year rate; the
precondition itself unmet), say in one sentence why absence-of-evidence is not evidence of absence
*here* — or shrink the base rate's weight to match. Audited misses show the reference class
outvoting a decisive current-period fact the forecast itself had already quoted.

**5. Argue both directions, with independence bookkeeping.** Strongest case for YES; strongest case
for NO; weigh them. Two rules:
- **Cluster evidence by ultimate source.** Five articles syndicating one wire report are ONE piece
  of evidence. Two arguments sharing a premise are roughly one argument. Count clusters, not
  mentions — double-counting correlated evidence is how confident wrong answers are built.
- **Rate each cluster** Strong / Moderate / Weak before weighing, so a pile of weak evidence
  doesn't outshout one strong piece.
- **Name your load-bearing premise.** State the single factual premise your number most depends on
  (a scheduled date, a published document, a party's stated position) and verify it with a search
  when tools allow — audited runs found two models forecasting the same question from contradictory
  "facts", neither flagging the premise as uncertain. If it can't be verified, widen toward the
  scenario where it's wrong.

**6. Land on a number at 1% granularity.** Rounding to the 5% grid measurably destroys accuracy —
most for the best forecasters. The last digit needs a stated reason ("7%, not 5%, because the
denominator of the base rate is small"). Two named defaults are banned without justification:
**50%** ("perfectly balanced evidence" is a strong claim) and **round numbers** (anchoring, not
inference). Extreme values are allowed when evidence is genuinely decisive — strong evidence is
common, and tails that are too *thin* are a real documented failure — but an extreme number must
cite the evidence that earns it and survive the clamp-band warning consciously.
**Mechanical vs political near-certainty — a reason gate, not a floor.** Going below ~5% or above
~95% must be *earned* by naming the blocking mechanism: arithmetic on a published index, a fixed
deadline already passed, a filing that does not exist, every enumerable pathway to the other
outcome individually closed. When the outcome runs through a live political, diplomatic, or
institutional choice (a vote, a signing, a negotiation with active sponsors), that mechanism
rarely exists — audited misses put 3% on a multi-track diplomatic process with named backers,
which is a choice, not a mechanism; and tournament data cannot validate anyone's calibration much
below ~5% on such questions. But this is a gate, not a clamp: when the mechanical case is
genuinely there, take the extreme and log the mechanism — measured tournament bots lost more to
*timid* tails (7% where better forecasters said 2%) than to reckless ones, and reflexive
"it's-probably-nothing" hedging is its own documented failure mode.

**7. Self-tests before committing** (all tiers where time permits; mandatory at high tier):
- **Conservation of expected evidence:** what near-term observation would move this number *down*,
  and by how much? If every plausible observation confirms you, the number is untestable. If you
  already expect to raise it later, raise it now.
- **Equivalent bet:** would you rather bet on your claim, or on a draw from an urn with your stated
  percentage of winning balls? Adjust until indifferent.
- **Premortem (high tier):** "This resolved against me. The most plausible story of how is…" — if
  the story is easy to tell, move the number toward it.
- **Second private estimate (high tier):** after the premortem/critique, write a fresh number
  *without looking at your earlier one*, then hand both to aggregation as separate draws.

**8. Output.** `Probability: XX%` plus the 3–6 line reasoning summary (base rate → key update →
main counterargument) and the "what would change my mind" observations.

## Named failure modes to counter (each documented in the field)

| Failure | Counter |
|---|---|
| Acquiescence / YES-bias — models say yes far more often than questions resolve yes | Outside view first (step 3); ask "what does NO look like?" explicitly |
| Round-number clustering and lazy 50% | Step 6; the tool warns on both |
| Tail overconfidence — 99% on the strength of a vibe | Clamp band (config); extremes must cite decisive evidence |
| Tails too thin — ritual humility capping honest 97%s at 90% | Also step 6: strong evidence is common; document and keep it |
| Catastrophizing — a vivid sub-scenario's probability assigned to the whole question | The sub-scenario is one branch: recompose explicitly (P(question) = Σ branches), and cross-check against the holistic anchor |
| Base-rate over-adherence — history repeated until the mechanism lapsed | Step 4 |
| Treating the question as open when it's effectively resolved (or vice versa) | Question-hygiene, before reasoning ever starts |
| Multiple-stage fallacy — long chains of multiplied point conditionals drifting to 0 | Cap decomposition at 3–4 factors; recompose with explicit algebra; if the decomposed and holistic numbers disagree wildly, investigate — don't average |

## Decomposition (when it helps — full mechanics in `decompose.md`)

Decompose when the question has separable drivers or when a slow question needs **fast proxies** —
short-horizon sub-questions whose quick resolution is evidence on the slow one (record them with
`--parent-id` and `--fast-proxy`; they are the highest-bandwidth calibration signal you can
create). Keep chains short, prefer ranges to point estimates within stages, and always cross-check
the recomposed number against your holistic estimate. When forecasting **several related
questions**, name the shared assumption and state what all your answers look like if it's wrong —
one bad world-model poisons whole clusters of forecasts.

## What NOT to do

Do not narrate Bayes' theorem, assign numeric likelihood ratios, or "reason like a Bayesian" as a
performance — measured effect on accuracy: negative. The discipline is already in the structure:
base rate first (step 3), independent evidence clusters (step 5), explicit update with a reason
(step 6), expected-evidence test (step 7). The ensemble math happens *outside* this scratchpad, in
aggregation — one context window pretending to be a committee is not a committee.
