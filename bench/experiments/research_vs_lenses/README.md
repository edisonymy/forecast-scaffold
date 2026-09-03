# Research runs vs reasoning lenses — preregistered live test (2026-09-03)

Question: does spending the ensemble budget on a second INDEPENDENT research run beat
spending it on reasoning-only runs that re-read the first run's dossier under a lens?

- Control: production (`config/forecast.toml`): 1 research run + 2 lens runs (medium).
- Candidate: `forecast.toml` here: `run_angles = ["P", "P"]` — two plain replicates
  (Angle P), each a full research run, pooled by geo-mean odds. Binary questions only.
- Rule, cost accounting and scorer: `bench/analysis/research_vs_lenses.py` (header).

Procedure per MiniBench wave (questions open Monday, most in the first week):
1. Let production forecast the wave as usual.
2. Within the same hour, run the candidate in dry-run against the same open binaries,
   journaling to `bench/sets/rvl-<wave>.jsonl` (command in `forecast.toml`). Cap with
   `--budget`; ~25 binaries x ~$2.6 = ~$65 per wave at opus-5.
3. After the wave resolves, `python bench/sync_resolutions.py` then
   `python bench/analysis/research_vs_lenses.py --candidate bench/sets/rvl-<wave>.jsonl`.
4. Two waves (n >= 40 paired) decide: PROMOTE / KILL / EXTEND per the header rule.

Status: NOT YET RUN — the first wave needs operator approval for the spend (above the
$25 standing threshold). Nothing in production changes until the rule fires.
