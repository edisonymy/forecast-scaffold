# HANDOVER — continuation state as of 2026-07-16

For the next working session. Everything load-bearing is in this repo; this file is the
map. Operator: Edison. Mission (his words): "the aspiration is definitely to reach SOTA
level performance with frontier models" — weak-model lift is a feature, never the pitch.

## 2026-07-16 MiniBench full-census addendum

- `docs/minibench-analysis-2026-07-16.md` is the reviewed readout: 58 bot-vs-crowd
  pairs, top gaps audited live with adversarial skeptic verification. Net: our three
  largest divergences are verified research-edge WINS (schedule/docket/registry); the
  confirmed misses are extrapolation overconfidence, institutional-process
  overdiscount, numeric narrowness (19/21 narrower, ratio 0.62), and one
  conditional-criterion leak.
- DUE JUL 23-25: enter resolutions and run
  `python bench/analysis/minibench_counterfactuals.py --resolutions FILE.json` —
  transforms + subgroup tags preregistered 2026-07-16 pre-outcome. CI-gated rules in
  the docstring; do not fit anything on this wave's outcomes.
- `docs/proposals-research-v2.md` gained a 2026-07-16 addendum (question-shape research
  rules + reasoning-side missing-evidence gate and conditional guard). Still awaiting
  operator approval as one unit; no production prompt changed.
- Journal integrity: 6 submitted-but-unjournaled MiniBench rows were backfilled from
  the platform record (`scripts/backfill_journal.py`); run it after any suspected
  journal-commit loss.

## 2026-07-15 MiniBench / pastcast addendum

- The operator supplied 15 new closed MiniBench comparisons (9 binary, 6 numeric).
  They are unresolved and may not be timestamp-matched, so they are diagnostic rather
  than score evidence. The reproducible readout is
  `bench/analysis/minibench_2026_07_15.py`; the reviewed memo is
  `docs/minibench-pastcast-analysis-2026-07-15.html`.
- Binary disagreement is concentrated: three rows (SK Hynix, NBA investigation, SOL)
  carry 76.9% of absolute disagreement; excluding SK Hynix, bot/community Pearson is
  0.972 and Spearman 0.958. Do NOT apply a global YES lift.
- Numeric dispersion is the stronger new hypothesis: all 6 current displayed bot
  intervals are narrower (mean width ratio 0.547), and all 8 current+prior comparisons
  are narrower. The binary-only harness cannot test this; build continuous support and
  score CRPS/coverage/sharpness before changing production widths.
- An adversarial TimeVault audit found and fixed a live-origin redirect leak, removed a
  live MediaWiki title-map dependency (including the subtler page-move leak), made corpus
  dates fail closed, recognized prospective `frozen_at` rows, added bounded transient
  retries, and separated attempts from successful/unavailable reads.
  Legacy tranche rows have no semantic telemetry and mix scaffold versions; do not use
  them as evidence of parity with live agentic search.
- A single bounded Opus 4.6 capability smoke timed out without a result. The old harness
  incorrectly retried the timeout; v0.4.22 makes transport failures single-shot and a
  positive bench budget serialized/native, reserving its remainder when usage is
  unknown. No OpenRouter or AskNews spend occurred; killed subscription-equivalent usage
  is unknown.
- Do not resume the incomplete legacy tranche without approval: estimated completion is
  roughly $40.6, above the standing $25 threshold and of limited value while provenance
  is heterogeneous. First pass the $0 no-model retrieval gate pre-registered in the HTML;
  only then consider its maximum-$12 paired web-vs-TimeVault pilot.

## Read these first, in order
1. `docs/roadmap-v05.md` — THE plan (Fable panel + adversarial critics; every step has a
   pre-registered decision rule). Execute it top to bottom.
2. `CHANGELOG.md` v0.4.15–v0.4.22 — what was measured and shipped, with numbers.
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

**The decisive experiment ("tranche1") is incomplete and quarantined from score
interpretation:** 71 preregistered `run == 0` rows across only 24 unique questions, plus
six preserved nonzero-run high rows. The run-0 memory screen found 0 candidates, but the
file mixes scaffold versions 0.4.18/0.4.20/0.4.21 and none of its telemetry distinguishes
attempted tools from returned evidence. Run the mechanics/provenance diagnostic with
`python bench/analysis/pastcast_validity.py RESULTS --run 0 --substrate-details DETAILS`;
do not read the incomplete score. Estimated completion is roughly $40.6 and requires
operator approval, but is not recommended: first prove TimeVault's external validity
using the $0 retrieval gate in the 2026-07-15 HTML memo. Preserve all 77 existing rows.

**Manifold bot (live, phase 1, betting enabled — 2 live 25-mana bets placed (dry_run=False:
qid uIQlEUOhuS NO, qid IyZz6yqqqQ YES; 50 mana open exposure), remaining pairs converged;
numbers drift, the journal is the source of truth and `python bot/score_manifold.py` is
the authoritative live count):** journal `bot/journal/manifold.jsonl`, phase file
`manifold-phase.json`. Run: `python bot/run_manifold.py --limit 10 --tier medium --live`
(key in `~/.manifold/key.txt`). The cloud workflow is dispatched hourly at minute 17 UTC
by GCP Cloud Scheduler `manifold-bot-kicker`; its activation gate keeps all setup and
Claude work dormant until `2026-07-15T00:00:00Z`. It needs the `MANIFOLD_API_KEY` secret
to bet from CI and remains subscription-only with a $5/run cap. Score with
`python bot/score_manifold.py`. Known finding to fix: the
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
1. Build and run the $0, 18-fact TimeVault external-validity gate pre-registered in
   `docs/minibench-pastcast-analysis-2026-07-15.html`. A security miss is an automatic
   kill; do not substitute the permissive question-source-set any-hit proxy.
2. Only if that passes, seek approval for the maximum-$12, nine-question paired
   live-web-vs-TimeVault capability pilot. Keep Terra/Opus, prompts, and questions paired;
   concurrency stays 1 and no score is interpreted before contamination/memory screens.
3. Add continuous/numeric benchmark support and proper CRPS/coverage/sharpness readout
   before testing the repeated MiniBench interval-width signature.
4. Deadline-discipline test using the existing all-152 census, NET paired scoring, and
   exact 10 motivating holdouts excluded from promotion.
5. research.md v2: get operator approval on docs/proposals-research-v2.md, then A/B it
   (paired vs current, RES the target metric).
6. Bundle arm at n=152: related-resolved-question lookup + numeric 5→6 percentiles +
   tail-widening + re-research-only auditor (ablate only if bundle clears +0.006).
7. Pool-level extremization fitted on angle-member pools only after a valid paired set
   exists. 8. Weekly prospective freeze (bench/freeze_prospective.py) + resolve pass.

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
