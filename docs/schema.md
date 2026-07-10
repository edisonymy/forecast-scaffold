# ForecastRecord schema (v1)

One forecast = one JSON object = one line of the journal (JSONL). The journal is append-only;
`resolve` rewrites the file in place, and a git history of the journal file (plus platform
timestamps, where forecasts are also submitted to a platform) is the tamper-evidence story.

Canonical implementation: `src/forecast_scaffold/core.py` (`ForecastRecord`), vendored into each
skill at `scripts/fsj.py`. `fsj.py record --dry-run` prints a valid record without writing it.

## Fields

Only `question` is required. Serialization drops `null` fields; absent = null.

### Identity & lifecycle

| Field | Type | Meaning |
|---|---|---|
| `id` | str | `<created-date>-<8 hex>`; generated, stable across round-trips |
| `schema_version` | int | `1` |
| `created` | str | UTC ISO-8601, when the record was drafted |
| `forecast_at` | str? | UTC ISO-8601, when the probability was committed — the pre-registration timestamp |
| `status` | str | `draft` \| `open` \| `resolved` \| `annulled` (annulled = ambiguous/voided; excluded from all scoring) |
| `dry_run` | bool? | v0.4.8: `true` = produced by a `--dry-run`/`--post` run that never submitted anywhere — exclude from the live track record; `null` on older records (assume live) |

### Estimand (the question)

| Field | Type | Meaning |
|---|---|---|
| `question` | str | one proposition, one sentence |
| `question_type` | str | `binary` (first-class) \| `multiple_choice` \| `numeric` \| `discrete` |
| `resolution_criterion` | str | exactly what counts, judged by which source — carried verbatim into reasoning |
| `resolve_by` | str? | ISO date the answer should be knowable |
| `source` | obj? | `{platform, question_id, url}` for platform questions |
| `reference_class` | str | the outside-view anchor |
| `base_rate` | float? | its numeric base rate, when one exists |
| `why_it_matters` | str | VOI note: which decision/tracked belief this moves |
| `parent_id` | str? | id of the record this decomposes (fast-proxy linkage) |
| `fast_proxy` | bool | short-horizon sub-question of a slow parent |

### Forecast (shape gated by `question_type`)

| Field | Type | Meaning |
|---|---|---|
| `probability` | float? | binary: final pooled p, in [0,1] |
| `options` / `probabilities` | list? | multiple_choice: parallel lists; probabilities sum to 1 (±0.01) |
| `percentiles` | obj? | numeric/discrete: `{"10","25","50","75","90"}`, strictly increasing |
| `submitted_cdf` | list? | numeric/date: the exact CDF (~201 points) submitted to the platform — a preregistration record of the object actually scored, since `percentiles` alone can't be rebuilt into it |
| `scaling` | obj? | numeric/date: `{range_min, range_max, zero_point, lower_open, upper_open, cdf_size}` the CDF was built against — needed to interpret `submitted_cdf` |
| `raw_draws` | list? | the individual ensemble draws (audit trail) |
| `aggregation` | str? | e.g. `"trimmed_mean(n=5)"`, incl. any crowd blend and clamp |
| `effort` | str? | `low`/`medium`/`high`, with `(auto)` when auto-triaged |
| `model` | str | free string naming the model(s) used |
| `provider` | str? | billing/routing path that produced it, e.g. `subscription` / `openrouter` |
| `crowd` | obj? | `{value, source, at}` — the crowd/market number **at forecast time** |
| `reasoning` | str | 3–6 line summary: base rate → key update → main counterargument |
| `what_would_change_my_mind` | list[str] | observations that would move the number |
| `research` | obj? | `{n_searches, sources: [urls]}` |

### Resolution

| Field | Type | Meaning |
|---|---|---|
| `resolution` | obj? | `{outcome: bool\|float\|str, resolved_on: iso-date, note}` |

A record is **scorable** (enters the Brier score) when: `status == "resolved"`,
`question_type == "binary"`, `probability` set, and `resolution.outcome` is a boolean.

## Versioning policy

- **Additive optional fields do not bump `schema_version`.** Readers ignore unknown fields
  (`from_dict` filters to known names), so old code reads new journals.
- **Breaking changes bump `schema_version`**, and the reader must accept version N and N−1.

## Interop: decision-journal (`DecisionRecord`) format

Some decision-scaffolding systems store forecasts as a general `DecisionRecord` with
`method="forecast"` and the estimand metadata packed into `assumptions` provenance strings. The
mapping is lossless in both directions:

| ForecastRecord | DecisionRecord |
|---|---|
| `question` | `title` and `prediction.expectation` |
| `probability` | `prediction.probability` |
| `options` / `probabilities` | `prediction.options` / `prediction.probabilities` (present for multiple_choice) |
| `percentiles` | `prediction.percentiles` (present for numeric/discrete/date) |
| `resolve_by` | `prediction.resolve_by` |
| `reasoning` | `rationale` |
| `what_would_change_my_mind` | `what_would_change_my_mind` |
| `question_type` | assumption `"estimand_kind: <kind>"` |
| `reference_class` | assumption `"reference_class: <text>"` |
| `why_it_matters` | assumption `"VOI: <text>"` |
| `resolution_criterion` | assumption `"resolves_when: <text>"` |
| `parent_id` | assumption `"parent_decision: <id>"` |
| `fast_proxy` | assumption `"fast_proxy: true"` (present only when true) |
| `resolution.outcome` (bool) | `resolution.realized` |
| `resolution.note` | `resolution.what_happened` |
| `resolution.resolved_on` | `resolution.resolved_on` |
| — (constants on export) | `method="forecast"`, `needs_system2=false` |

`fsj.py export --format decision-record` implements this mapping (annulled records export as
resolved with `realized: null` plus an `"annulled: true"` assumption).
