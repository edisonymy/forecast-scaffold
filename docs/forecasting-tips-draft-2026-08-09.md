# Forecasting tips — v2 draft (awaiting operator approval; not yet in the live skill)

v1 (earlier today) was red-teamed against our own scored record and audited against the
human and LLM forecasting literature. It failed the red team: its directional tips
encoded a one-wave diagnosis ("we under-predict change") that did not replicate in wave
2 (0–10% band 0/11 → 3/12; median-bias 71% → 45%; all p ≥ 0.16), its low-band advice
was net −47 to −128 points against our own book (the sub-10% band's nine quiet wins
outweigh its three famous losses), and one cited example (Starship conditional) was
annulled, never scored. The LLM literature adds a structural warning: imperative
caution text ("be careful about X") flattens forecasts globally toward 50 (arXiv
2506.01578); mitigations that work are procedural and number-anchored.

**v2 design rules:** every tip is a direction-neutral *procedure* — it says what to
check or name, never which way to move. No probability floors or caps, in numbers or
in prose. Anything that is "record a fact" lives in the checklist, where a wrong item
costs tokens, not points.

---

## Tips (reasoning step)

**1. Name the blocker or the driver — don't infer from silence.**
A found schedule, docket entry, recess calendar, or registry count is the strongest
fact this question class admits; weigh it heavily. The *absence* of a date you searched
for is weak evidence in both directions — it neither licenses an extreme call nor
forbids one. Ask what the actor has already done, not what you failed to find.
*(Lit: observers forecasting others' timelines run pessimistic — Buehler & Griffin;
announced target dates slip — Flyvbjerg. Both reduce to: anchor on concrete acts.)*

**2. Reconcile the notes and the number.**
Before submitting, reread your own brief. If the concrete evidence you recorded pulls
against your number, resolve the conflict explicitly: move the number, or write down
why the evidence does not bind. A decisive unknown you named yourself must be priced —
state which way it cuts, don't leave it decorative.
*(Two scored losses where the journal contained the winning consideration and the
number ignored it. LLM lit: verbalized numbers lag the model's own stated reasoning —
arXiv 2607.08046.)*

**3. Streaks and trends are regimes: name the machine.**
Before extrapolating a run rate — or betting against one — name the mechanism
generating it and what would turn it off, then check both sides: what has the
off-switch already done; what does simple continuation imply. If continuation exits
the question's range before the deadline, say so in the brief and price the escape
explicitly (`p_below_lower` / `p_above_upper`, v0.4.23) instead of letting the CDF
builder's bound anchor decide. This cuts both ways and is not by itself a reason to
move.
*(Independently replicated: capable models track growth with the distribution's center
while under-pricing regime breaks in both tails — FRI, arXiv 2605.22672.)*

**4. Premortem the confident call.**
For any forecast you would call confident, write the strongest single path to the
opposite resolution — the concrete world where you are wrong — and price that path
explicitly. If you genuinely cannot articulate one, the confidence stands; if you can,
check the number still clears it.
*(Klein 2007 premortem; Mitchell/Russo/Pennington consider-the-opposite. Direction-
neutral by construction: it tests confident YES and confident NO identically.)*

**5. Boldness is bought with facts.**
Stand far from a crowd or market only on a specific, checkable fact they plausibly
lack, and size the deviation to the concreteness of the fact. A claim about other
minds ("the market is overreacting", "thin books herd") is not a fact. Note that LLM
forecaster errors correlate strongly across models (r≈0.77 — arXiv 2605.00844): against
a bot crowd, your conviction is less independent than it feels.
*(Our three biggest wins were fact-backed extreme calls; our one market-deviation loss
was psychology-backed.)*

**6. Score the question, not your story.**
Re-read the resolution criterion immediately before submitting. Quarantine
P(condition) from P(outcome | condition). Continuous questions pay log density at the
outcome, not interval tidiness. A deadline pays observation-in-the-named-source, not
event-in-the-world — price the reporting step in whichever direction it cuts.
*(Scoring-rule mechanics — Gneiting & Raftery 2007 and the platform's own rules;
stated as mechanics, not as a scored-mistake trace.)*

---

## Research checklist ("record X" items; the number stays with judgment)

- **Deciding-body calendar**: term dates, recesses, scheduled sessions, bulletin
  cadence. Record a found schedule as a fact; record a not-found schedule as
  "searched, absent" — nothing more.
- **Trend questions**: current level, current rate, the rate's own trajectory, one
  named regime-break candidate in each direction, and whether simple continuation
  exits the range by the deadline.
- **Any relevant market**: price, venue, liquidity/volume, timestamp of the last
  meaningful move.
- **Named resolution source**: when it next updates relative to the deadline.

## Standing monitors (ours — measured each wave, never injected into forecasts)

- Calibration band table per wave (pooled 0–10%: 3/23; 10–25%: 3/17; neither
  significant — `bench/analysis/minibench-pooled-readout-2026-08-09.txt`).
- High-band (≥75%) large misses: 3 across two waves; symmetric shrink a=0.7 scored
  +214 pooled but concentrated in those 3 questions — a hypothesis to watch, not ship.
- Out-of-bound outcome rate on open-bounded numerics (2/42 pooled) and declared escape
  mass usage from v0.4.23 onward.

## Pre-prod test (required before these tips enter the skill)

Paired re-elicitation A/B on the 69 resolved binaries from both waves (~$15, opus-5,
tools off, own live research dossier as fixed context, memory screen, post-cutoff
events): control = production reasoning contract; treatment = contract + tips.
- **Primary**: paired Brier delta, bootstrap CI90.
- **Guard (flattening tripwire)**: no net loss on the ~20 correct sub-10% calls and
  the three big wave-1 fact-backed wins. Kill if the guard loses more than the primary
  gains.
- **Targeted**: direction of movement on the six known binary losses; premortem
  compliance (did the treatment actually write the opposite-path paragraph).
Ship only if primary CI90 clears in favor, or primary is neutral with a clean guard
and targeted improvement. Numerics as stage 2. Tips never ship mid-wave.

## Provenance (honest version)

Tips 2, 3, 5 trace to scored mistakes (n between 2 and 5 each); tip 6 is scoring
mechanics; tips 1 and 4 are literature-grounded procedures consistent with our record
rather than derived from it. The v1 claim "every tip traces to a confirmed, scored
mistake" was wrong and is retracted. The two-wave record is small; the direction of
wave-1's unified diagnosis reversed in wave 2 — which is precisely why nothing here
tells the forecaster which way to move.
