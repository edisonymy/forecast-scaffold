# research.md — hunt the evidence that would change the number

Retrieval quality is the largest single scaffold gain measured; multi-source research is the
strongest correlate of tournament performance. Use whatever search and fetch tools the host agent
has — native web search beats bespoke retrieval pipelines. This file is about *how* to hunt, not
which tool.

## Hunt for the RIGHT information

Before searching, write down: **what evidence would most change my estimate?** Search for that
first — not for general background. Typical decisive evidence: the current official status of the
thing (the already-resolved check), the base rate data for the reference class, the one leading
indicator that moves first.

Query discipline: expand the question into 2–4 searches — at least one aimed at **history** (past
instances, base rates: "how often has X happened since…") and one at the **present** (latest
status, recent news). At high tier run these as two explicit passes: the historical pass builds the
outside view *before* the current pass can anchor you on today's headlines.

## Source rules

- **At least two independent sources** for anything load-bearing. One source is usually not enough,
  and five copies of one wire story are still one source.
- **Primary first.** For a claim about an organization's product, policy, numbers: its own
  publication outranks news, which outranks aggregators, which outrank forums, which outrank model
  memory. Fetch the actual page when the claim is decisive; don't trust snippets.
- **Date-stamp everything.** Note each source's publication date; flag anything undatable. A true
  fact from before the last relevant change is a false fact now.
- **Never invent a base rate.** Search for published rates; if none exist, count instances yourself
  ("of the 14 similar cases since 2015, 3 resolved yes") and label it hand-built.
- **Numbers you'll compute with** (base rates, trends, denominators): prefer sources you can quote
  exactly; where the host provides code execution, use it for the arithmetic instead of estimating
  in prose.

## Red-team your own draft

Once you have a tentative answer, deliberately search for **disconfirming** evidence — the query
you'd run if you believed the opposite. Then:

- **A contradiction inside your own evidence is the most valuable signal you have.** Resolve it —
  by scope (do the sources talk about different things?), recency (did the world change between
  them?), or authority (is one primary?) — and name the reconciliation. Never average two
  contradictory sources, and never conclude past an unresolved contradiction.
- **Coherence test:** does the conclusion square with the actors' incentives and the other facts?
  A conclusion that requires the world to be incoherent is usually the error.

## Leave provenance

Record `{n_searches, sources: [the load-bearing URLs]}` with the forecast, and capture any
crowd/market number **with its timestamp** the moment you see it (`--crowd-value`,
`--crowd-source`) — the crowd's value *at forecast time* is both an aggregation input and the
baseline your track record will be judged against.
