# HANDOVER — continuation state as of 2026-07-12 (session end)

For the next working session. Everything load-bearing is in this repo; this file is the
map. Operator: Edison. Mission (his words): "the aspiration is definitely to reach SOTA
level performance with frontier models" — weak-model lift is a feature, never the pitch.

## Read these first, in order
1. `docs/roadmap-v05.md` — THE plan (Fable panel + adversarial critics; every step has a
   pre-registered decision rule). Execute it top to bottom.
2. `CHANGELOG.md` v0.4.15–v0.4.20 — what was measured and shipped, with numbers.
3. `docs/manifold-policy.md` — the operator-approved betting policy + amendments.
4. `bench/analysis/README.md` — the analysis scripts behind every claim.
5. `docs/proposals-research-v2.md` — research.md v2 merged draft, AWAITING OPERATOR
   APPROVAL (do not ship into skills/forecast/references/research.md without his yes).

## State of play (what exists, what's running, what's next)

**Measured foundation (all on 152 decontaminated BTF-2 questions, opus-4-6, paired):**
parametric-only 0.2483 → +research digest 0.1946 (evidence = the lever, +0.054) →
FutureSearch teacher 0.1750 (gap 0.020 = refinement, RES 0.111 vs our 0.042). All
generator-prompt levers measured NULL (spines, resampling, spine-pools, extremization of
single runs) — externally replicated. Research AGENCY is the mechanism (their ablation:
0.022 on opus). Deadline-optimism tail ≈ 0.026 gross. Platt recalibration worth ~0.024 on
pastcast (slope 0.573 = overconfident THERE; sign not portable — layer built, ships inert,
`fsj calibrate-fit`).

**The decisive experiment ("tranche1") may be complete or partial**: 3 arms
(plain ReAct / high / angles) × 40q, corpus+vault research, results accumulate in
`bench/results/btf2-loop1-adm.tranche1.results.jsonl` (resumable — rerun the exact
command in git log `86dc390`'s message context or: run_bench on btf2-loop1-adm.jsonl,
--tiers plain,high,angles --leakfree timevault --corpus bench/corpus/btf2_corpus.sqlite
--limit 40 --max-runs 1 --budget 80 --timeout 900 --tag tranche1, agent-cmd opus-4-6).
`--max-runs 1` is load-bearing: without it, the configured high tier expands to four
runs and the command targets 240 rows instead of the preregistered 120 arm rows. Six
nonzero-run high rows were already produced before this was caught; preserve them as paid
raw data, but memory-screen and score only `run == 0`. The completed raw file will therefore
have 126 lines while still containing exactly 120 preregistered/scorable arm rows. FIRST ACTION:
`python bench/analysis/memory_screen.py` (adapted to the tranche file), then
`python bench/analysis/readout_tranche1.py`. Interpret ONLY by the pre-registered rules
in the script docstring / roadmap. If rows < ~90/120, resume the run first.

**Manifold bot (live, phase 1, betting enabled, 0 bets so far — all completed pairs
converged < 0.05):** journal `bot/journal/manifold.jsonl`, phase file
`manifold-phase.json`. Run: `python bot/run_manifold.py --limit 10 --tier medium --live`
(key in `~/.manifold/key.txt`). Daily CI workflow exists (needs MANIFOLD_API_KEY secret
to bet from CI). Score with `python bot/score_manifold.py`. Known finding to fix: the
Odyssey pair — sighted read the market "thin" then capitulated to it anyway (blind 0.70 →
sighted 0.23 vs mkt 0.199; Fable teacher says 0.60). One-line sighted-brief fix: a
thin/stale read means YOUR number carries the weight. Also verify sources journaling
(records showed sources:[] despite the floor — check build_record wiring).

**Fable teacher yardstick:** `bot/journal/fable-teacher.jsonl` — 7 max-effort blind
forecasts on live questions. Score at resolution vs production blind/sighted and market.
Key disagreement: IMO perfect-score — teacher 0.57 vs market 0.886, resolves ~Jul 20-31.

**Tournament bot:** sonnet-5 cron on FutureEval+MiniBench, v0.4.20 live: reference-class
floor, refresh gate 48h, angle mode dark (`run_angles` empty), recalibration inert,
AskNews armed locally (COMPETITION-ONLY key — never Manifold, compliance test enforces;
CI needs ASKNEWS_API_KEY secret to use it there).

## Immediate queue (from roadmap, in order)
1. memory_screen + readout_tranche1 (rules pre-registered — no peeking first).
2. Substrate recall audit BEFORE interpreting any research-mechanics null (roadmap
   critic amendment: coverage vs discoverability, diagnostic not gate).
3. Deadline-discipline test: build deadline tagging (does NOT exist yet), router census
   over all 152, NET paired scoring, HOLD OUT the ~10 motivating catastrophes.
4. research.md v2: get operator approval on docs/proposals-research-v2.md, then A/B it
   (paired vs current, RES the target metric).
5. Bundle arm at n=152: related-resolved-question lookup + numeric 5→6 percentiles +
   tail-widening + re-research-only auditor (ablate only if bundle clears +0.006).
6. Pool-level extremization fitted on angle-member pools (free once tranche angles rows
   exist). 7. Weekly prospective freeze (bench/freeze_prospective.py) + resolve pass.

## Operating rules (operator directives, standing)
- Fable main loop → delegate small implementations to opus/sonnet subagents with precise
  specs; review their diffs; run repo-wide `python -m ruff check .` + full pytest before
  any commit (CI lints stricter than local subagent claims).
- Git: cron pushes to main every ~10 min. Commit with a CLEAN tree, `git add -A` (or all
  touched paths), fetch+rebase BEFORE commit, push, then VERIFY: `git status` clean of
  tracked files AND remote hash == local. Never `rebase --autostash` over feature work
  (it fragmented main once — see memory/git-commit-hygiene).
- Cost: sample-first, hard --budget caps, report spend. Subscription for Claude models
  (wait out window resets; they bite ~every few hours — every long run must be
  resumable); openrouter-direct only for non-subscription models (~$70 left on key).
- Portability is hard: skill markdown + thin harness, one model, one provider. No
  cross-model ensembling in core claims. No Gemini/OpenAI.
- Keys on this machine (never print contents): ~/.manifold/key.txt, ~/.asknews/key.txt,
  OPENROUTER_API_KEY + METACULUS_TOKEN in env.
- Pastcast validity ritual for ANY new model/questions: contamination probe
  (bench/contamination_probe.py) + memory-claim screen; exclusions applied pairwise.
- Version ritual: SCAFFOLD_VERSION (core.py) + pyproject + plugin.json + `python
  scripts/vendor_sync.py`; config/forecast.toml mirrors DEFAULTS (test-enforced).

## Windows gotchas
Bash heredocs mangle backslashes (write patch scripts via the Write tool); PC restarts
kill detached background jobs (relaunch with resume semantics; verify liveness via row
counts, not task status); `git add` on bench/sets needs the prospective-* carve-out only;
PYTHONUTF8=1 on every python invocation that prints.
