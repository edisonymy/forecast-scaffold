# bench/analysis — session analysis scripts (2026-07-10/11 improvement loops)

Repo-relative ports of the analysis used for every measured claim in CHANGELOG
v0.4.15–v0.4.19 and docs/roadmap-v05.md. All read bench/sets + bench/results (gitignored
data — regenerate via the fetchers/probe if absent) and apply the standard exclusions
(opus probe flags + the ECB memory-leak qid).

- `readout_tranche1.py` — THE pre-registered readout for the 3-arm research A/B
  (plain/high/angles). It scores only the preregistered `run == 0` cells and reports any
  preserved nonzero-run rows it ignores. Run `memory_screen.py RESULTS --run 0` first;
  pass confirmed hits as repeatable `--exclude-qid QID` arguments. Rules are in the
  docstring and docs/roadmap-v05.md. Do not peek before the screen.
- `memory_screen.py` — regex prefilter + judged reading for memory-claim leakage
  (weights recall surfacing mid-forecast). Point it at any new results file.
- `minibench_counterfactuals.py` — preregistered (2026-07-16, pre-outcome) transforms
  for the 2026-07 MiniBench wave: binary logit shrink + numeric widening, scored
  globally and per outcome-blind subgroup (`minibench-2026-07-tags.json`). Run with
  `--resolutions FILE.json` once the wave resolves (Jul 23-25); decision rules in the
  docstring. Companion memo: `docs/minibench-analysis-2026-07-16.md`.
- `pastcast_validity.py` — pre-score provenance and research-mechanics audit for a
  TimeVault result file. It deliberately never reads probabilities: it exposes mixed
  versions, unused tools, legacy attempt-only telemetry, and the optional substrate
  any-hit proxy before anyone interprets an A/B as evidence about forecasting quality.
- `minibench_2026_07_15.py` — reproducible diagnostic for the operator-supplied 15
  closed MiniBench forecasts (9 binary, 6 numeric). It measures bot/community
  disagreement and displayed interval-width signatures, with explicit unresolved and
  timestamp-mismatch boundaries; it does not score disagreement as error.
- `timevault-smoke-2026-07-15.json` — content-free inputs and outcomes from the final-code,
  no-model TimeVault smoke. It preserves the 5/5 corpus hits, strict Wayback and
  exact-title Wikipedia archive timestamps, and the failed GDELT response without upgrading the
  smoke into a recall or external-validity result.
- `substrate_recall.py` — diagnostic first-20 audit of the corpus discovery substrate.
  The public BTF-2 release omits teacher search/page-read traces, so this is explicitly a
  question-source-set proxy, not "teacher-cited page" recall. It separates production-
  global vs question-scoped discoverability, cutoff eligibility, and optional archive
  readability; frozen queries live in `substrate-recall-proxy.jsonl`.
- `deadline_census.py` — validates and reads out the all-152 institutional-action-by-
  deadline router census, including the exact 10 motivating holdouts. The future paid A/B
  contract and experiment-only research move live under `bench/experiments/deadline/`.
- `loop3_multiplicity.py` — resample-vs-spine pooling verdict (the multiplicity null).
- `tail_analysis.py` — catastrophe/extreme-claim profile per arm (found the deadline
  cluster).
- `sota_power.py` — vs-teacher comparison, Murphy decomposition, MDE/power math.
- `cross_model_pools.py` — retired cross-model loop's pooling analysis (kept for the
  error-correlation machinery).
- `extremize_test.py` — split-sample extremization test (negative on single runs; the
  open variant is pool-level extremization on angle members).
