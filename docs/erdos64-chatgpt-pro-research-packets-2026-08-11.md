# Erdős #64: ChatGPT Pro research packets

Prepared 11 August 2026. These are private research prompts, not publication claims.

## Fresh-session assumption and recommended allocation

Assume every Pro session starts with **no chat history and no repository access**. Packets A and B are therefore framed as standalone mathematical problems: the campaign facts they use are written into the prompt as supplied hypotheses. Packets C, D and E are **not safe to run from prose alone**, because their exact labelled states and provenance rules are load-bearing. They require the source-note attachment bundles listed in each packet.

Run **Packet A** first. It is orthogonal to the root lane and has the best chance of producing genuinely new information rather than rediscovering current work. Run **Packet B** in a second fresh session. Hold C/D until the main campaign exports the named notes and confirms the target has not been superseded. Run E only with the complete dependency bundle.

Do not launch a generic 6–8-mark attack yet: the endpoint catalogue and its routing are still changing.

## Shared campaign context to include verbatim

> Erdős problem #64 (the Erdős–Gyárfás conjecture) asks whether every finite simple graph of minimum degree at least three contains a cycle whose length is a power of two. We call a graph **safe** if it contains no cycle of length 4, 8, 16, 32, ... . A counterexample would be a finite safe graph of minimum degree at least three. The campaign usually assumes a counterexample `G` that is lexicographically minimum by `(number of vertices, number of edges)`.
>
> The proof programme splits a smallest counterexample by connectivity: a bridge; a cutvertex; a two-edge cut; or no one/two-edge cut (the 3-edge-connected trunk). The bridge case is closed. The cutvertex/one-dot branch reduces to arbitrary-size one-defect pieces and remains open. The two-edge-cut branch has strong results for clean rooted shores but is not closed. The 3-edge-connected branch is highly compressed but still has bounded marked endpoints.
>
> Important campaign results that should not be rediscovered as if new: (i) in a smallest counterexample, degree-three vertices outnumber higher-degree vertices by at least two-to-one plus six; (ii) every ordinary edge has a near-dyadic certificate cycle of length `2^t+1`; (iii) the all-C5 certificate face is closed, so at least one certificate has length at least nine; (iv) clean nonbipartite two-edge-cut shores have at least four root-to-root path lengths; (v) mixed-parity exact-three rooted spectra are excluded in the relevant C4/C8-free class; (vi) order 17 critical-edge objects are certificate-excluded; order 18 is only provisionally UNSAT pending independent DRAT replay; (vii) arbitrary private five-mark states have an audited bounded descent, but bounded 6–8-mark exits and some finite endpoint classes remain.
>
> Known failed or insufficient routes: the generic history-free marked-descent invariant is false; route-length arithmetic alone cannot close the clean two-edge-cut branch (the abstract lists `{3,4,5,6}` and `{4,5,6,7}` pass the isolated number-only tests); independently guaranteed paths cannot be assumed simultaneously disjoint; unstructured enumeration of long certificate overlaps grows too quickly. Any proposed solution that repeats one of these moves without a new hypothesis is not progress.

## Common research contract

Paste this at the start of every packet:

> You are an independent mathematical-research and adversarial-proof agent working privately on Erdős problem #64 (the Erdős–Gyárfás conjecture): every finite simple graph of minimum degree at least three contains a cycle whose length is a power of two. Call a graph **safe** if it contains no cycle of length 4, 8, 16, 32, ... . The campaign reasons from a lexicographically smallest hypothetical safe graph of minimum degree at least three.
>
> Treat every campaign theorem explicitly supplied in the packet as a hypothesis for this research task, not as an externally published fact. Do not claim novelty or a solution to Erdős #64. If the packet includes attachments, read every attached source and audit correction before working. If a required attachment is missing, return `NEEDS_ARTIFACTS` instead of reconstructing the state from memory.
>
> Your primary duty is truth, not producing a positive theorem. For every proposed lemma, first try to falsify it by hand and by small exact search. Never import a hypothesis merely because it held earlier in the construction. Explicitly distinguish: PROVED, COMPUTATIONALLY CHECKED, CONJECTURAL, REFUTED, and BLOCKED BY MISSING ARTIFACTS. A natural-language proof must state all quantifiers and trace every dependency. If you find a scope defect, stop promotion and report the smallest exact failure.
>
> A useful negative result is an explicit counterexample to the proposed intermediate lemma, a precise missing hypothesis, or a proof that the route cannot close the target. Do not hide failure behind more casework.

---

## Packet A — Standalone graph-realizability problem for the clean two-edge-cut branch

**Priority:** Highest orthogonal theory target  
**Expected difficulty:** Extremely hard  
**Why useful:** This attacks a separate top-level branch rather than duplicating the marked-core siege.

Paste after the common contract:

> ### Background
>
> In the clean two-edge-cut branch, deleting two edges exposes two rooted graph pieces (“shores”). For a shore `H` with distinct nonadjacent roots `x,y`, let `Λ_H(x,y)` be the set of lengths of simple `x–y` paths in `H`.
>
> Work with the following self-contained class. For `i=1,2`, `H_i` is a finite simple connected graph with distinct nonadjacent roots `x_i,y_i`; `H_i+x_i y_i` is 2-connected; every nonroot vertex has degree at least three in `H_i`; `H_i` is nonbipartite; and `H_i` contains no cycle whose length is a power of two. Put `A=Λ_{H_1}(x_1,y_1)` and `B=Λ_{H_2}(x_2,y_2)`. Assume `|A|,|B|≥4`; each set contains both parities, a power of two and a Mersenne number `2^r−1`; `A+B` contains a power of two; `(A+B)+1` contains a power of two; and `(A+B)+2` contains no power of two. If needed, strengthen this by requiring the exponents represented by the distinguished Mersenne elements of `A` and `B` to be different.
>
> Pure arithmetic is known to be insufficient: `A={3,4,5,6}` and `B={4,5,6,7}` pass the isolated number-only conditions. They are not known to be realizable as spectra of graphs in the class above.
>
> ### Task
>
> Decide whether the self-contained graph class above is empty. Find a **graph-geometric realizability constraint** on `Λ_H(x,y)` strong enough to rule it out, or construct a member. Acceptable alternatives are:
>
> 1. a new all-orders theorem about intersections, attachments, parity, ears, longest paths, or two internally disjoint rooted paths that every actual clean shore satisfies and that the abstract countermodel does not encode;
> 2. an exact finite rooted-pole construction satisfying every authoritative clean-shore hypothesis and the surviving arithmetic pattern, proving that the proposed Q16 closure is false or needs another hypothesis;
> 3. a rigorous reduction of Q16 to a smaller named geometric lemma with no hidden connectivity or degree assumptions.
>
> Do **not** prove another number-set lemma unless it uses a property derived from graph geometry. This class is deliberately a slightly abstracted superclass of the campaign’s actual clean shores. Proving it empty would directly help the campaign. Constructing a member would show that still more campaign-specific geometry is required.
>
> ### Required deliverables
>
> - Frozen theorem statement with every hypothesis.
> - Dependency and scope table.
> - Small-instance falsifier search or a reason it is infeasible.
> - Full proof, exact counterexample, or sharply stated residual lemma.
> - A final paragraph explaining exactly which part of the top-level two-edge-cut branch would close if the result were true.

---

## Packet B — Standalone one-defect witness-aggregation problem

**Priority:** High; attacks the one-dot-split branch  
**Expected difficulty:** Extremely hard  
**Why useful:** This is a longstanding all-orders bottleneck that has received less concentrated attention than the 3-edge-connected trunk.

Paste after the common contract:

> ### Background
>
> Study a finite simple connected graph `H` with a distinguished root `u`. Assume `H` is safe; `deg_H(u)=2`; every other vertex has degree at least three; and the natural root completion is 2-connected. Two matching pieces of this type would reconstruct a candidate counterexample. Finite cubic-except-root instances have been excluded only through limited orders, so the arbitrary-size class is open.
>
> Add the following strong saturation axiom, abstracted from the campaign theorem: for every two distinct vertices `a,b` outside `{u}`, at least one of the unordered pairs `{u,a}`, `{u,b}`, `{a,b}` is joined by a simple path of length `2^k−2` for some integer `k≥2`. If useful, separately study the strengthened version in which every vertex also has a root-path of a Mersenne length `2^t−1`. Witnesses for different pairs are not promised to be distinct or internally disjoint.
>
> ### Task
>
> First decide whether this abstract class can be infinite or even nonempty. Prove an **aggregation or uncrossing theorem** that turns the saturated family of power-minus-two/Mersenne witnesses into either:
>
> - a forbidden power-of-two cycle;
> - a strictly smaller safe minimum-degree-three graph or critical pair with valid scope; or
> - a finite list of explicit exceptional geometries.
>
> If the desired implication is false, construct the smallest abstract or graphical witness system showing how all required paths can coexist without creating a dyadic cycle. The countermodel must respect path simplicity, shared vertices/edges, degrees, and the exact safe-graph hypotheses—not merely route lengths.
>
> Avoid assuming that independently guaranteed paths can be chosen simultaneously or internally disjoint. This is the central trap.
>
> ### Required deliverables
>
> - Exact restatement of the class and any strengthened variant used.
> - A compatibility graph/hypergraph or other explicit representation of witness overlap.
> - Adversarial small search against the proposed aggregation rule.
> - Proved theorem, exact counterexample, or a minimal residual configuration.
> - Scope impact: say whether the result proves the abstract class empty, merely bounds its order, or constructs a countermodel. Do not claim closure of the campaign’s one-dot branch without later checking the exact source theorem.

---

## Packet C — Eliminate or realize the exceptional four-mark state E4 (attachments required)

**Priority:** Reserve packet; first confirm the root lane is not already attacking it  
**Expected difficulty:** Very hard but sharply scoped

Paste after the common contract:

> ### Background
>
> The provenance-aware four-mark programme now has audited connectivity normalization and bounded descent for the nonexceptional case. The remaining exceptional `q=4` state, called `E4` in the current notes, has an audited fork: either it descends to fewer marks, or it carries pair-dependent Mersenne/power-minus-two paths on all six pairs of four terminals. The generic history-free marked-descent invariant is false and must not be reused.
>
> Locate the authoritative four-mark normalization, nonexceptional q=4 cap composition, E4 definition, E4 fork, and all audit repairs. Write the exact labelled graph/state defining E4 before doing any mathematics. If those artifacts are unavailable, return `NEEDS_ARTIFACTS`.
>
> **Required attachment bundle:** the complete four-mark connectivity-normalization note; the nonexceptional q=4 cap-composition note; the exact E4 definition/diagram or canonical labelled record; the E4 six-pair fork proof; every hostile-audit report and repaired hash; and the definitions of private mark, provenance, safe deletion, collision and common blocker. A relay summary is not enough.
>
> ### Task
>
> Decide whether the six-pair path menu in E4 forces a contradiction when combined with the state’s exact provenance and private-cycle requirements. Seek one of:
>
> 1. a simultaneous-choice/uncrossing theorem producing a dyadic cycle;
> 2. a sound strict-order descent that preserves the provenance required by the parent reduction;
> 3. a complete finite normal form for all possible path-intersection patterns;
> 4. an explicit E4 realization showing that this endpoint cannot be killed from the current hypotheses.
>
> You must not choose the six guaranteed paths independently without proving compatibility. You must not contract a marked edge unless the authoritative state explicitly permits it. Test every proposed local surgery against the exact private-cycle witnesses.
>
> ### Required deliverables
>
> - One-page exact definition of E4.
> - Exhaustive branch table for the six-pair witness pattern.
> - Automated small checker where finite enumeration is possible.
> - Proof, counterexample, or smallest unresolved intersection pattern.
> - Composition statement showing how the conclusion plugs back into the four-mark descent.

---

## Packet D — Arbitrary C9 five-mark banner: provenance or realizability (attachments required)

**Priority:** Reserve packet  
**Expected difficulty:** Very hard and finite-looking, but scope-sensitive

Paste after the common contract:

> ### Background
>
> The audited arbitrary private five-mark descent has five outputs: at most four marks; bounded 6/7/8-mark blocker/collision exits; a private wheel of order at most 11; an all-proper-marked state of order at most 20; or one exact `C9 banner` state. In the campaign’s finite notation the banner arose as a reflection class of a Theta(1,4,8)-type trace with mark set reported as `{02,06,24,46,48}`. Verify that notation against the authoritative files before using it.
>
> A separate support-specific C5 theorem excludes the banner in the C5-supported subroute. It is a scope error to apply that theorem to an arbitrary five-mark state without first proving C5 support.
>
> Locate `ARBITRARY_FIVE_MARK_PRIVATE_DESCENT_2026-08-11.md`, the five-mark normalization, the banner enumeration/canonical records, the support-specific C5 theorem, and their hostile-audit reports.
>
> **Required attachment bundle:** all files named in the preceding sentence; the exact labelled banner graph and canonical record; definitions of the arbitrary private five-mark input class and every output class; the C5-support predicate; and the final scope/composition audit. Without these, return `NEEDS_ARTIFACTS`.
>
> ### Task
>
> Determine whether an arbitrary provenance-bearing private banner state can actually exist. Either:
>
> - prove that the parent campaign provenance always supplies the additional support condition needed by the C5 theorem;
> - derive a contradiction directly from the banner’s five private dyadic witnesses;
> - give a sound descent to the already-controlled ≤4-mark class; or
> - construct an exact finite banner state satisfying every arbitrary-five hypothesis, proving the endpoint is genuinely realizable.
>
> Keep “abstract private five-mark state” separate from “state generated by the particular forest/common-blocker/cap route.” A theorem about one is not automatically a theorem about the other.
>
> ### Required deliverables
>
> - Canonical labelled banner and automorphism/reflection handling.
> - Exact list of arbitrary-five versus C5-specific hypotheses.
> - SAT/SMT or exhaustive checker for realizability if feasible.
> - Proof/counterexample and a composition audit back to the parent descent.

---

## Packet E — Fundamental scope and composition audit of the current 3-edge-connected frontier (full bundle required)

**Priority:** Highest reliability packet  
**Expected difficulty:** Extremely hard audit, not theorem generation  
**Why useful:** The main risk is now a locally correct lemma being composed outside its proved scope.

Paste after the common contract:

> ### Objective
>
> Perform an independent root-to-leaf audit of the claimed 3-edge-connected reduction, with theorem discovery disabled until the audit is complete. Start from a lexicographically minimum 3-edge-connected hypothetical counterexample and trace every route to the current endpoints: certificate-backed order 17 exclusion; provisional order 18 UNSAT; private marked states through four and five marks; bounded 6/7/8-mark exits; wheels; all-proper states; E4; and the arbitrary/C5-supported banner distinction.
>
> The campaign previously lost a broad marked-descent claim because history/provenance and connectivity hypotheses were not preserved. Search specifically for the same failure mode.
>
> ### Audit questions
>
> 1. At every arrow, is order strictly reduced when later minimality is invoked?
> 2. Are simplicity, minimum degree, connectedness, 2-/3-connectivity and safe deletion preserved?
> 3. Does each retained mark still possess the exact private witness claimed for it?
> 4. Is mark provenance preserved strongly enough for the next theorem?
> 5. Are C5-specific, half-order, clean-shore, root-disjoint or proper-edge hypotheses imported only where proved?
> 6. Are all outputs exhaustive, or has an unlabelled remainder been omitted?
> 7. Does a finite UNSAT result have a frozen formula, proof log and independent checker, rather than solver status alone?
>
> ### Required deliverables
>
> - Machine-readable dependency DAG and human-readable theorem ledger.
> - For every edge in the DAG: input class, output class, preserved/lost hypotheses, and exact source.
> - At least one adversarial attempt to instantiate each interface with a small countermodel.
> - Verdict per promoted global claim: PASS, REPAIR, WITHDRAW, or UNVERIFIABLE.
> - A minimal trusted kernel: the smallest subset of results that can safely be used by future agents.
>
> Do not repair a failed route until the failure and its downstream blast radius have been frozen and reported.
>
> **Required attachment bundle:** the current claim index and state/log; the repaired arbitrary-order critical-pair and forest-packing notes; the private-core normalization/descent notes for 2, 3, 4 and 5 marks; E4 and banner artifacts; common-blocker/collision definitions; wheel/all-proper endpoint definitions; order-17 CNF/proof manifest/checker report; order-18 CNF/provisional solver manifest; and every scope correction that supersedes an earlier claim. This audit is not standalone without that bundle.

## Do not spend a Pro session on these yet

- **Order-18 proof verification:** use gzip integrity checking and an independent DRAT checker. Language-model review is not a substitute for certificate replay.
- **Generic 6–8-mark descent:** not yet stable enough to be a clean independent packet. Wait for a frozen endpoint catalogue and provenance ledger.
- **Unstructured long-certificate pair enumeration:** the campaign already found that the number of safe pair types explodes with length. Require a structural invariant before more enumeration.
- **Generic “solve Erdős #64” prompts:** they duplicate screened routes, lose scope, and are unlikely to produce composable work.
