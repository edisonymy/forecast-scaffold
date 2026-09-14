# Roadmap to v0.5 — closing the gap to SOTA with frontier models

Synthesized 2026-07-11 from a three-strategist Fable panel (research-mechanics,
architecture/post-processing, and evaluation lenses) run against the full measured
evidence pack, then corrected by a two-critic adversarial pass (the critics verified
claims against the codebase — including catching a cited tail_audit tagging feature that
does not exist).

**Consensus diagnosis.** The remaining ~0.020 gap to the FutureSearch teacher is
REFINEMENT (RES 0.111 vs our 0.042; REL comparable), it is not purchasable at the
generator prompt (every prompt-side lever measured null, externally replicated), and the
only measured mechanism of the right size is RESEARCH AGENCY (their own ablation: Claude
self-directing search is worth 0.022 vs judging a fixed digest — our exact architecture).
A second overlapping slice is the deadline-optimism tail (~0.026 gross, ~10 of 24
catastrophes).

## The plan (ordered; each step carries its decision rule)

0. **Preregister the 3-arm A/B decision rules before reading it** (running now: plain
   ReAct vs our-method vs angles, 40q, evidence-matched). Promote plain/angles if paired
   delta >= 0.008; if |d| < 0.005 run step 2 BEFORE interpreting; extend winner to 152q.
1. **Research telemetry retrofit**: bench rows gain {n_searches, n_full_reads,
   source_classes, queries} so every later A/B correlates mechanics with RES, not just
   Brier.
2. **Retrieval-recall audit of the corpus substrate** (novel, validity-gating): can our
   discovery index surface the teacher-cited load-bearing pages at all? >=70%
   discoverable = nulls are real; <50% = fix discovery before believing any research A/B.
3. **Deadline discipline as a conditional research MOVE, not a spine**: on
   "will INSTITUTION do X by DATE" questions, fetch procedural status, count remaining
   steps vs time, price from the institution's slippage record. Promote if tagged-set
   delta >= +0.015 AND non-deadline controls degrade < 0.003 (control degradation is the
   spine-null signature — watch explicitly). Kill if controls degrade >= 0.005.
4. **research.md v2 — the analyst loop** (two Fable drafts complete; adversarial merge
   pending): source-class portfolio by question type, entity+mechanism+date query rules,
   snippet-triage-then-full-read policy, evidence dating, three targeted statement
   discounts, anomaly pass, VOI stopping rule. A/B paired vs current, RES is the target
   metric (promote at >= +0.008 with RES up; kill < +0.003).
5. **Related-resolved-question lookup as a research move** + **pool-level extremization
   fitted on angle-member pools** (free test on the tranche's F/D/A outputs — the
   theoretically correct home for extremizing is a decorrelated pool, never single runs).
6. **Platt activation gate**: temporal cross-validation from the journal (fit early, score
   late) before the built layer ever activates in production.
7. **Live transfer scoreboard**: weekly prospective freeze of open questions forecast by
   BOTH production config and current-best config; plus tournament score-accrual policy
   (nearest-resolution-first selection, breadth over polish per the squared-prize rule).
8. **Assemble v0.5 and confirm on a FRESH decontaminated set** (100-120 new BTF-2
   questions, probe-cleared) — no component ships on its screening result alone.

## Critic corrections (adversarial pass — binding amendments to the steps above)
- **Order**: run the substrate audit (step 2) BEFORE interpreting the 3-arm A/B — every
  branch decision leans on it. Redesign it to separate CORPUS COVERAGE (is the page in
  the 8M corpus at all?) from INDEX DISCOVERABILITY (surfaceable in <=5 queries?); with
  only ~20 auditable questions its thresholds are wide-CI — treat as DIAGNOSTIC, not a
  gate.
- **Statistics**: size every gate from the measured paired-delta SD before fixing any n;
  n=40 resolves only effects >= ~0.015 — do not let the tranche adjudicate 0.005-sized
  differences. Paired bootstrap on Brier deltas is the primary statistic (the gap lives
  in tail-question magnitudes; sign tests discard them), win-rate secondary.
- **Deadline test design**: router census over ALL 152 (build the deadline tagging — it
  does not exist yet), score NET paired Brier including a +/-0.002 contamination guard on
  non-fired questions, and HOLD OUT the ~10 motivating catastrophes from the promote
  decision (validating a tail fix on the questions that defined it is regression-to-mean
  inflation). Content: the drafted window-arithmetic/steps/slippage spine upgraded to a
  fetch-the-docket research move.
- **Auditor**: the re-research-only auditor survives; any auditor that adjusts the number
  (capping toward 0.5) is KILLED — it is the measured hedging null aimed at the wrong
  deficit (our gap is RES, not REL).
- **Portability of FS's 0.022**: an upper-bound analogy, not an additive transfer —
  expected values in steps 3-4 are ceilings.

## Standing cautions (from the evidence, binding)
- Anything "be more careful" applied globally is a measured null that sells refinement.
- The audit pass routes to RE-RESEARCH, never to hedging the number.
- Bundle-screening several small adopts in ONE n=152 arm (related-question lookup,
  numeric percentile changes, surviving audit pass); ablate for attribution only if the
  bundle clears ~+0.006.
- One fresh-slice confirmation (~100 new decontaminated questions, pre-registered
  thresholds, Platt inert, revert-to-minimal rule) before anything ships as default.
- Portability is a hard constraint: everything above is skill markdown + thin-harness
  orchestration, one model, one provider.
