# question-types.md — beyond binary

Binary is the native, best-performing case; multiple-choice and numeric are where automated
forecasters measurably underperform and where roughly a third of tournament submissions fail on
format alone. The rules below are what close that gap. All format arithmetic lives in `fsj.py` —
run its validators rather than eyeballing.

## Multiple choice

- Options must be **MECE** (mutually exclusive, collectively exhaustive — check for the implicit
  "none of the above"). Probabilities sum to 1; the tool enforces ±0.01 and you renormalize, never
  hand-tweak one option to make the sum work.
- **Forecast each option as its own binary** ("this option vs. everything else"), then renormalize
  the set. Direct all-at-once MC elicitation is measurably weaker.
- **Shuffle option order across draws** — position bias is real. If your probabilities move when
  the order moves, they weren't beliefs.
- Leave moderate probability on unlikely-but-possible options; MC questions resolve to surprises
  more often than intuition says. An option at 0 is a claim of impossibility.
- Record with
  `fsj.py record --type multiple_choice --options "A,B,C" --probabilities "0.5,0.3,0.2" …`;
  pre-check any full record with `fsj.py validate --record-json '<record>'`.

## Numeric (and discrete)

- Elicit your belief as **five percentiles — 10 / 25 / 50 / 75 / 90** — strictly increasing,
  strictly inside the question's range. Start from the smallest value and work up.
- **Widen the tails beyond what feels right.** Distributions that are too narrow are the dominant
  numeric failure; your 10th–90th should feel uncomfortably wide. Ask: "what value would genuinely
  shock me?" — then make sure it carries some mass.
- Anchor the median on the zeroth/first-order forecast (persistence, trend); set the spread from
  the reference class's historical dispersion, not from confidence vibes.
- For platform submission the percentiles become a 201-point CDF with strict format rules
  (monotone, minimum step, per-bin mass cap, open/closed-bound tails). Never construct it by hand:

  ```
  python fsj.py cdf --percentiles "10:5,25:8,50:12,75:20,90:35" --min 0 --max 100 [--open-upper] [--zero-point Z]
  ```

  If it errors, fix the *percentiles* (usually: not strictly increasing, or values outside the
  range) and re-run — that error-and-repair loop is the difference between a scored and a rejected
  forecast. Log-scaled questions carry a `zero_point`; pass it through.
- Record with `fsj.py record --type numeric --percentiles "10:5,25:8,50:12,75:20,90:35" …`.

## Conditionals — P(A | B)

- Record the condition explicitly in the question text and criterion; the record only resolves if
  B occurs (otherwise annul).
- Forecast the pair when useful: P(A | B) and P(A | not-B) — the gap between them is the claimed
  effect, and stating both exposes incoherence (if they're equal, B doesn't matter; say so).
- Consistency checks (the tool can't see semantics — do these yourself):
  P(A) should lie between P(A|B) and P(A|¬B); P(A and B) ≤ min(P(A), P(B)); an event over a longer
  horizon ≥ the same event over a shorter one.
- Causal caveat: a conditional read off the worlds where B happens is not the effect of *doing* B.
  If the user wants the intervention, say you're forecasting it as best you can and name the
  confounders set aside (worlds where B happens differ from today's world in more ways than B).
