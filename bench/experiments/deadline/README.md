# Deadline research-move experiment (preregistered, not yet run)

This directory is experiment-only. Nothing here is a live production prompt, and the
conditional move has not been run against a paid model.

## Frozen router census

`bench/analysis/deadline-census.jsonl` labels all 152 admissible Opus BTF-2 questions in
source-file order. The label was made from question text, criteria, and background only;
forecast results and resolved outcomes were not inputs. The source set has 153 rows. The
known ECB memory-claim question is excluded, while the 14 frozen Opus contamination-probe
flags were already absent, leaving 152.

The router fires when resolution depends on a named organization, government, court,
legislature, regulator, intergovernmental body, or other formal institution completing,
issuing, adopting, scheduling, or implementing a discrete action by the cutoff, and its
progress can be investigated as a status-and-steps process. It does not fire when the
target is a measurement, exogenous-event count, ranking, vote result, natural trigger,
market outcome, or mere persistence of a status, even when an official source reports
that outcome. A count does fire when it directly expresses completion of a named
institutional rollout or program milestone. Repeated trigger-driven alerts or enforcement
events without one pending completion process are controls.

Every row carries a concise basis and an explicit `holdout_motivator` boolean. The exact
10 motivating catastrophes are router-tagged but held out from all promotion decisions.

Validate and read out the frozen partition with:

```powershell
$env:PYTHONUTF8='1'
python bench/analysis/deadline_census.py --show-qids
```

## A/B contract

The future paired A/B compares the current research workflow with the same workflow plus
`research-move.md`, injected only for router-fired questions. Report paired Brier
improvement as `baseline Brier - move Brier`; report control degradation as
`move Brier - baseline Brier`. Report net paired Brier, the tagged-development slice, the
held-out motivating slice, and non-fired controls separately. Size the paid sample from
the observed paired-delta standard deviation before proposing spend.

Pre-registered gate, verbatim:

> net paired Brier; tagged development delta >= +0.015 and non-deadline controls degrade <0.003 promote; controls >=0.005 kill; +/-0.002 contamination guard on non-fired questions; motivating 10 never enter promote decision.

The `+/-0.002` guard is a contamination/invariance diagnostic on non-fired questions, not
permission to modify the move after seeing the held-out examples. The motivating 10 may
be reported only as an out-of-decision diagnostic. No paid A/B begins without a separate,
costed run proposal.
