# question-hygiene.md — operationalize before you estimate

An ambiguous question can't be scored, and an unscored forecast teaches nothing. Roughly nine in
ten candidate forecasting questions fail operationalization review in production systems — expect
to rewrite.

## The operationalized form

Every forecast needs, pinned down before reasoning starts:

- **Question** — one sentence, one proposition. "Will X and Y" is two questions.
- **Resolution criterion** — exactly what counts, judged by which source or arbiter. "AI passes a
  hard test" fails; "an LLM scores ≥ 90% on benchmark B per its public leaderboard" resolves.
- **Resolve-by date** — when the answer will be knowable. Prefer dates where the resolving data is
  *published*, not just when the event happens.
- **Question type** — binary / multiple-choice (options MECE, probabilities will sum to 1) /
  numeric (elicited as percentiles).

When the user's ask is vague, propose the operationalized version ("did you mean A — announced —
or B — actually shipped?") and confirm before forecasting. The gap between what they asked and
what resolves is where forecasts silently become worthless.

## The adversarial read

Read the criterion the way a motivated opponent would:

- **How could this resolve NO on a technicality?** (Or YES?) Deadlines measured in different time
  zones, "officially confirmed" vs. reported, definitional edge cases.
- **Quantifier-drop:** a rule about "third parties / at scale / their customers" is not a rule
  about this specific case. Check the scope of every clause before generalizing it.
- **Prove-a-negative:** "no incident of X will occur" questions are near-impossible to resolve
  cleanly — restate positively or narrow the observation window and source.
- On platform questions (Metaculus, prediction markets): the platform's **resolution text is the
  contract** — carry it verbatim, and mine the comments; they contain the technicality arguments
  and base rates others already found.

## Known anti-patterns (priced-in base rates)

- **"Institution announced X — will X happen within N months?"** Announcement-to-delivery lag means
  these overwhelmingly resolve NO on tournament timescales. The announcement is weak evidence of
  the deed.
- **Already effectively resolved.** The single most catastrophic failure mode in automated
  forecasting: confidently forecasting a question whose answer is already public (or whose
  as-of date has silently passed). Before estimating anything, spend one search checking whether
  the answer already exists. Symmetrically: a question can be effectively *dead* (the only pathway
  to YES has closed) while technically open.
- **Ambiguous resolver.** If two honest people reading the criterion could grade the same world
  differently, fix the criterion, not the forecast.

## Conditionals

"If B, then how likely is A?" — record it as P(A | B), state clearly that it only resolves if B
occurs, and flag the standard caveat: a conditional read off correlated worlds is not the effect of
*making* B happen. If the user wants "what happens if we DO B," say you are forecasting the
intervention as best you can and name the confounders you're setting aside. Full conditional and
numeric mechanics: `references/question-types.md`.
