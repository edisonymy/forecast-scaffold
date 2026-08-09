# HANDOVER — continuation state as of 2026-07-16

## 2026-08-09: two-wave review; v0.4.23 + v0.4.24 shipped; AskNews armed; tips killed

Full review of MiniBench 2026-07-13 (final: 584.2, rank 12, $57) and 2026-07-27
(near-final: ~299 over 57 scored, rank 17, $0; prize line ≈ 461 under the
score²-with-$50-floor payout rule, verified against both leaderboards).

**Shipped to production, live for the 2026-08-10 wave (THREE changes at once — attribute
nothing wave-over-wave to any single one; only paired tests separate them):**
- **v0.4.23 escape-mass elicitation**: `p_below_lower`/`p_above_upper` on open-bounded
  numerics, honored end-to-end in `percentiles_to_cdf`. Motivation: BMEX (45012) and
  bluetongue (44967) resolved out-of-bounds at the exact −195.6 floor (undeclared open
  tails pin at 0.001); BMEX's journal wanted 12–15% below the floor and the format
  discarded it. Regression-locked (−195.6 → +47.8). Wave-2 counterfactual: fix ≈ rank
  5–8 and $60–115 instead of $0.
- **v0.4.24 research checklist**: four record-only items (calendars, trend/regime,
  market metadata, source update rhythm) at the two research-run call sites in
  `run_bot.py` + `references/research.md`. Bench arms and Manifold deliberately NOT
  wired. Directional-language ban is test-enforced.
- **AskNews armed in CI** (secret set 2026-08-09; both prior waves ran WITHOUT it
  despite local arming). Fail-open-proof smoke step in bot-test.yml (commit 500a6cb).

**Killed by preregistered process — do not resurrect without new evidence:**
- All wave-1 numeric transforms (right-tail widen r=1.5 → +0.3/q on wave 2, dead;
  pooled CI now EXCLUDES zero AGAINST global widening at n=41/42).
- Reasoning tips v1 (red team: low-band lift −47..−128 on our own record) and v2
  (A/B, 69 paired binaries, $17.48: primary +0.0032, targeted loss-shapes +0.0465
  WORSE, guard clean/no flattening — `bench/analysis/ab-tips-2026-08-09-readout.txt`).
  The wave-1 "we under-predict change" unified diagnosis DID NOT REPLICATE.

**New tooling (all committed):** `bench/fetch_minibench_wave.py` (one-command wave
capture; group posts need post-id fetches, qid-as-post-id reads the WRONG post),
`bench/analysis/minibench_mc.py` (MC scoring verified vs platform source: baseline
100·log(p·N)/log(N); MC peer carries 100·n/(n−1), NOT the continuous 50),
multi-wave pooling + calibration-band monitor in both scorers
(`minibench-pooled-readout-2026-08-09.txt` is the frozen two-wave readout),
`bench/analysis/ab_tips_binary.py` (paired reasoning-side A/B harness).

**Standing monitors (measure, don't inject):** pooled 0–10% band 3/23, 10–25% 3/17
(neither significant); ≥75% band's three large misses (symmetric shrink a=0.7 was +214
pooled but concentrated in 3 questions — hypothesis only); OOB outcome rate 2/42 and
escape-mass usage from v0.4.23 on. Wave-2 community comparisons: all six big binary
losses were us MORE extreme than the crowd in both directions; the crowd held ~11%
below-bound on BMEX.

**Known residue:** metaculus community aggregates are API-hidden for bot tournaments
(browser only); pages-build failure on 2026-08-09 was GitHub-transient (requeued via
`gh api -X POST .../pages/builds`); site root 404 is longstanding (no docs/index).
The nested `claude -p` OAuth token expires and needs interactive re-login — check with
a pong ping before any bench run. Tips draft stays in docs/ as a tested negative.

## 2026-07-31: Manifold uses sonnet-5; Metaculus remains opus-5

Operator directive: lower the hourly Manifold forecaster to `claude-sonnet-5` to reduce
subscription usage, while retaining `claude-opus-5` for Metaculus. The Manifold runner and its
workflow pin Sonnet explicitly; `bot/run_bot.py`, `bot.yml`, and `bot-test.yml` intentionally
remain on Opus.

## 2026-07-26: default model is now opus-5, repo-wide

Operator directive: every default forecasting model id moved `claude-sonnet-5` ->
`claude-opus-5` (bot.yml both provider steps, bot-test.yml, bench.yml,
`bot/run_bot.py` --agent-cmd default, `bot/run_manifold.py` DEFAULT_AGENT_CMD, plus the
two doc examples). Verified the pinned CLI accepts the id and reports usage under
`claude-opus-5`. Deliberately NOT changed: `bench/overnight_ablation.py`, whose arms are
named sonnet/opus experiment definitions, and test fixtures asserting literal strings.
WATCH: `--budget 3` per tournament run was sized for sonnet (~$1.57/question observed);
opus questions cost multiples of that, so a run can spend its cap inside one question and
collapse the intended 3-4 run ensemble to a single run. Raise the cap or accept fewer,
better-modelled questions per tick — and note the OpenRouter FALLBACK step now bills opus
rates to real credits when the subscription step fails.

## 2026-07-27 MiniBench wave

Entry needs nothing new: the slug is permanently `minibench` (project 33069 for this
round, "the project ID for the currently active minibench is always minibench"), and the
`TOURNAMENT_ID` repo variable is already `summer-futureeval-2026,minibench`. 60
questions, $1k pool, spot-peer scored, forecasting ends 2026-08-01T04:16:44Z, closes
2026-08-14. Most questions open in week 1. The 10-min Cloud Scheduler kicker is firing
reliably. ASKNEWS_API_KEY is EMPTY in CI — the competition-licensed research source is
off for the tournament that licenses it.

For the next working session. Everything load-bearing is in this repo; this file is the
map. Operator: Edison. Mission (his words): "the aspiration is definitely to reach SOTA
level performance with frontier models" — weak-model lift is a feature, never the pitch.

## 2026-07-17 tranche1 verdict (queue items 1-2 DONE)

Tranche1 completed to 126 rows ($96 total, incl. ~$10 of duplicate waste from
overlapping resume invocations — 9 dup cells, scored keep-first via `--dedupe first`).
Memory screen: 1 candidate (Ofcom/4chan), judged corpus-legitimate, no exclusions.
Frozen readout: `bench/analysis/tranche1-readout-2026-07-17.txt`. By the pre-registered
rules: NOTHING PROMOTES OVER HIGH. plain-vs-high mean +0.0219 (plain worse; CI90
[-0.0027,+0.0505] straddles but direction is clear at n=37, high wins 21/37) — the
scaffold's high tier BEATS plain ReAct, and the mechanism is refinement (RES 0.0972 vs
plain 0.0699). angles-vs-high: high better by 0.006 (CI straddles) at 3x the cost —
angle mode stays dark. On the common set high also scores BELOW the FutureSearch
teacher (-0.0136 Brier vs teacher) — with the corpus levelling discovery, our method
matches/beats theirs, supporting "discovery agency is the gap, not reasoning".
Substrate audit (critic amendment): 90% discoverability (18/20, 2 lexical misses),
100% URL retention — substrate not the bottleneck; verdicts interpretable.
Caveats recorded: scaffold-version mix v0.4.18-0.4.22 across rows; timeout 900->1500
mid-run; coverage ragged (plain 38 / high 37 / angles 36 scorable).
V2 A/B DONE (2026-07-17 evening): 40/40 cells, $25.68. Preregistered verdict:
PROMISING, DO NOT SHIP — RES +0.0180 (target metric, hit; v2 RES 0.1143 ~ teacher
level) but Brier guard not clean (paired mean -0.0016, CI90 [-0.0178,+0.0122]); REL
worsened +0.0156 (v2 is bolder: more refinement, slightly noisier calibration).
Snapshot: bench/analysis/ab-research-v2-readout-2026-07-17.txt. NEXT WAVE to decide:
run current-vs-v2 paired on 40 FRESH decontaminated btf2 questions (~$45) to pool
n~77 and re-apply the same rule; v2 stays on branch ab/research-v2 until then.

## 2026-07-16 MiniBench full-census addendum

- `docs/minibench-analysis-2026-07-16.md` is the reviewed readout: 58 bot-vs-crowd
  pairs, top gaps audited live with adversarial skeptic verification. Net: our three
  largest divergences are verified research-edge WINS (schedule/docket/registry); the
  confirmed misses are extrapolation overconfidence, institutional-process
  overdiscount, numeric narrowness (19/21 narrower, ratio 0.62), and one
  conditional-criterion leak.
- DONE 2026-07-26: resolutions entered (`bench/analysis/minibench-2026-07-resolutions.json`,
  pulled from the platform record) and both scorers run. Verdict appended to the memo:
  by the preregistered rule NOTHING PROMOTES (binary shrink CI straddles; the numeric
  pinball metric turned out scale-dominated and is retired). The real defect is
  ASYMMETRY, not width: 25-75 coverage 52% and 10-90 coverage 76% are calibrated, but
  15/21 outcomes landed above our median (p=0.039) and all five negative-scoring
  numerics were upper-tail misses, four barely above p90. Right-tail-only widening
  r=1.5 is worth +149 leaderboard points over 21 questions (+7.1/q) with CI90
  [-21.5,+35.1] straddling — preregistered as the primary transform for the 2026-07-27
  wave. New scorer: `bench/analysis/minibench_numeric_tails.py`.
- SCORING RULE VERIFIED 2026-07-26 against Metaculus/metaculus source, after the first
  version of the scorer got it wrong three ways. Platform truth:
  `score = 50*ln(mass_in_bucket / baseline)`, `baseline = (1-0.05*open_bounds)/N`, bucket
  index RIGHT-closed (`max(int(u*N + 1 - 1e-10), 1)`), and MiniBench pays the PEER form
  `50*(ln p - field mean ln p)` at a single spot instant (no standing forecast at that
  instant scores 0, never negative). Our first pass used `100*log2`, a left-closed bucket
  and a uniform reference of 1.0 — every figure was 2.885x too large and biased
  pessimistic exactly at declared percentiles. Semantics now locked by tests in
  `tests/test_minibench_analysis.py`; do not re-derive these by intuition.
- Out-of-bound mass is NOT tail insurance: under peer everyone's is pinned near the same
  floor, so an out-of-bound outcome is near score-neutral. Tail budget only pays inside
  the range.
- Open lever, no elicitation needed: `percentiles_to_cdf`'s open-bound halving
  (`core.py:1046-1048`) makes every submission sharper than what we elicited (declared p90
  lands at mean CDF 0.930). Removing it is worth +43 points (+2.0/q) on this wave. It is
  inherited from the upstream reference implementation — diverging is a decision, not a
  bug fix.
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

## Windows gotchas (updated 2026-07-17)
- Local pytest needs a clean auth env: this dev machine's sessions export
  ANTHROPIC_BASE_URL, which the manifold runner's fail-closed subscription preflight
  correctly rejects → 25+ "failures" that are environment artifacts. Run
  `env -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN python -m pytest -q`.
- NEVER let two run_bench invocations share a results file: resume detection reads the
  file at start, so overlapping runs duplicate (tier,qid) cells (9 dups on 2026-07-17,
  ~$10 wasted). Before relaunching, verify the old process tree is DEAD via PowerShell
  Get-Process (Git Bash ps cannot see native processes) and confirm each PID gone —
  one taskkill in a sweep can fail silently (exit 128).
- run_bench: hard --budget forces --concurrency 1 (sequential native-cap accounting).
  For parallel resumes drop --budget and guard with a wrapper-loop cumulative-cost
  halt (see the tranche/ab loop scripts pattern in the analysis snapshots).
- The angles tier's anomaly-hunt sub-run needs --timeout 1500 under timevault (900
  produced repeatable TimeoutExpired → fail-closed full-budget reservations).
Bash heredocs mangle backslashes (write patch scripts via the Write tool); PC restarts
kill detached background jobs (relaunch with resume semantics; verify liveness via row
counts, not task status); `git add` on bench/sets needs the prospective-* carve-out only;
PYTHONUTF8=1 on every python invocation that prints.
