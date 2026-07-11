# research.md — the research run

Research is the largest measured lever in this scaffold (evidence access: +0.054 Brier), and
*self-directed* research beats being handed the same evidence as a digest (+0.022 measured on this
model family) — so run your own queries; every step below is a tool-use procedure, not a writing
exercise. Use whatever search/fetch tools the host has — native search beats bespoke retrieval
pipelines. Elite envelope for a medium+ question: 10–20 tool calls, 50–300 snippets scanned, 5–20
pages read in full; below ~6 calls you are under-searched, past ~25 you are refining, not
learning. Everything here works identically on live web and a frozen corpus — on a corpus,
"latest" means latest-in-corpus and the cut date is "today" for every recency judgment. This file
governs the research step only; the forecast step stays unscripted (prose gates there are a
measured null).

## The plan — three lines before the first query

1. **The naive answer**: base rate + status quo, one line — "this class resolves YES ~X% of the
   time; if everyone stops acting, the outcome is ___." From general knowledge; a hypothesis, not
   an estimate. It converts the run from gathering background into hunting what makes this stale.
   It is a research device: the base rate and status-quo outcome enter the dossier as facts, the
   lean never does (`aggregate.md` owns why).
2. **Decisive unknowns**: what evidence would most change the number? Search for that, never for
   background. Query #1 is always the already-resolved check (question-hygiene owns why).
3. **The source-class portfolio** for this question type (table). Breadth of *classes* is a
   measured winner correlate (r=.42); ≥2 distinct classes is the floor, the portfolio is the
   target. Five links to one wire story are one class.

| Question type | Source classes — hit each deliberately, ≥1 query per class |
|---|---|
| Elections / politics | poll aggregates · rating agencies · special-indicator series (special elections, approval, fundamentals) · historical base rates · mechanical facts (thresholds, seats, ballot rules) |
| Institutional action by deadline | official docket/status record · remaining-steps status · the institution's slippage record on its own past deadlines · trade/beat press · calendar mechanics (sessions, recesses, notice periods) |
| Economic / numeric | official statistical series (+ revision history) · market/consensus pricing · seasonal and historical base rates · the release calendar · analog episodes |
| Negotiation / conflict | both sides' primary statements (see discounts) · on-the-ground reporting · resolution rates of similar standoffs · logistics mechanics (who is where, what is signed) |
| Science / tech / natural | the measurement series or primary publications · regulator/registry status · historical event frequency · independent replication/benchmarks · the issuing agency's own forecast (one input — see discounts) |

No archetype fits? Construct one: who officially records this · who aggregates opinion on it ·
what historical series contains its ancestors · what mechanical facts constrain it.

## Query craft

- **Entity + mechanism + date, never topic soup.** Bad: `Ukraine peace talks news`. Good:
  `"ceasefire" Istanbul delegation signed July 2026`.
- Quote the phrases the *resolving document* would contain, the way a domain insider names the
  instrument ("cloture vote schedule", "Phase 3 readout" — not the question text verbatim). Run
  official jargon and press vocabulary as separate passes — institutions and journalists don't
  share a dictionary.
- One unknown per query; a query that misses gets re-worded, not paged deeper. Use date/site
  operators where supported; on a corpus, keyword conjunctions do the same work.
- Extract special indicators as numbers, not vibes ("special-election overperformance +4.2 avg",
  not "doing well lately").

## Read vs snippet — scan wide, read narrow

Snippets are for coverage; full reads are earned. Promote a page to a full read when it is
load-bearing: a number you will compute with, resolution-relevant status, a claim decisive enough
to move the forecast, anything contradicting the emerging picture or undatable from the snippet.
Never let a decisive claim rest on a snippet — truncation eats dates, negations, and qualifiers.
Quote numbers from full reads exactly; where the host provides code execution, use it for the
arithmetic instead of estimating in prose. Two independent sources for anything load-bearing.
Every fact gets **two dates** — publication, and the date of the underlying event or data —
positioned against the event-window line; undatable evidence is downgraded, not silently dropped.
A true fact from before the last relevant change is a false fact now: after any pivotal find,
re-verify what you collected earlier in the run.

## Pass 1 — outside view (before the current pass, so headlines can't anchor it)

- **Similar resolved questions** — the cheapest high-signal move measured (34% of tournament
  winners do it; 0% of non-winners). Search platforms' resolved sections and archives for how
  near-identical questions actually resolved: a pre-counted base rate plus a catalog of how "sure
  things" failed.
- **An explicit base rate for a named reference class** (r=.38) — published if it exists; if not,
  count instances yourself, state numerator and denominator, and label the rate hand-built. Never
  invent one. It feeds the `reference_class` field verbatim.

## Pass 2 — the staleness hunt

Now attack the naive line with the queries you would run **if you believed the opposite**: recent
rating shifts, filings, schedule changes, surprising data, quiet reversals. Recency-weighted — the
last 2–8 weeks are where staleness lives. Price each anomaly ("moves the number from X toward Y
because…") — most surprises are noise, and saying so is part of the job. **Finding nothing is a
finding**: a clean hunt is positive evidence for the naive answer, not a failed search.

A contradiction inside your own evidence is the most valuable signal of the run. Resolve it — by
scope, recency, or authority — and name the reconciliation. Never average past one; if it stays
unresolved, the number stays nearer the base rate and the dossier says why.

## Statement discounts — three measured burns, not global skepticism

- **Plans are not events — build the remaining-steps ledger.** "On track", "scheduled", "expected
  to" are statements of intent. We priced deadline deliverables at 0.72–0.92 where resolved truth
  was 0.05–0.38; the whole error was unpriced slippage. Procedure: list the concrete steps between
  now and resolution, whose desk each sits on, and the institution's slippage record on its own
  past deadlines — the announcement adjusts that rate, never replaces it. If you cannot enumerate
  the remaining steps, that is itself pro-slippage evidence.
- **An institution forecasting its own domain is a claim, not a base rate.** Check it against the
  realized frequency of the phenomenon (a space-weather agency's alert level lost to its own
  historical hit rate). If the forecast series has a published hit rate, fetch it and weight
  accordingly; where forecast and base rate disagree, the base rate is the anchor.
- **Strategic speech is a move, not a report.** Ultimatums, "final offer", "talks collapsed",
  denials, leaked deadlines are produced to be believed — both sides declaring collapse days
  before signing is a documented pattern. Ask what the speaker gains if you believe it; price the
  incentive and the track record, then the claim.

These discounts are targeted. Routine factual reporting weighs normally — blanket skepticism is a
measured null that only blurs the estimate.

## Stopping rule

Stop when all three hold: every portfolio class hit at least once or explicitly recorded empty,
and the tier's source floor met; the plan's decisive unknowns found or confirmed unfindable; the
last two queries produced nothing that would move the number by ≥3 points. While any fails, keep
going — up to a hard cap of ~20 tool calls, past which the marginal query is confirmation-shopping:
forecast with what you have and record the gap ("missing: X; staying nearer the base rate").
Neither "feels complete" nor "the naive answer feels confirmed" is a stopping condition —
completeness is class coverage, and Pass 2 exists to stress that feeling.

## Negative space and handoff

The dossier carries a **negative-space line**: "searched for ___ and could not find it" — the
empty queries and what each absence means. Absence of an expected record (a docket entry due by
now, a filing, a fresh poll) is evidence, usually against on-time action; absence of coverage of a
dramatic hypothetical is evidence it didn't happen. Retry once with alternate phrasing before
claiming absence — "couldn't find" and "doesn't exist" are different claims, and on a corpus the
gap may be corpus scope: say which you believe.

Record provenance `{n_searches, source_classes_hit, sources: [the load-bearing URLs or doc-ids]}`
and any crowd number **with its timestamp** the moment you see it (`--crowd-value`,
`--crowd-source`). The dossier contract (`references/aggregate.md`) owns the output shape — dated
evidence bullets, base rates with the class each is computed over, the resolution-instrument and
event-window lines, no probability, no lean, no telegraphing adjectives. The number belongs to the
reasoning step, which this file deliberately leaves alone.
