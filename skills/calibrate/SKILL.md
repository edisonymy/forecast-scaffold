---
name: calibrate
description: Close the forecasting learning loop — resolve recorded forecasts whose date has passed, score calibration (Brier score, over/under-confidence direction), and turn misses into lessons. Use when asked "how are my forecasts doing", "resolve my predictions", "what's my calibration/Brier score", at the start of a review session, or on a schedule. Reads and writes the same journal the forecast skill records to.
---

# calibrate — resolve, score, learn

The forecast skill's records are only worth anything if they get graded. This skill resolves what
is due, scores calibration, and extracts lessons. **Resolve honestly** — the loop's entire value is
ground truth; grading yourself kindly destroys the signal.

## Tooling

`fsj.py` lives in this skill's `scripts/` directory (plugin install:
`${CLAUDE_PLUGIN_ROOT}/skills/calibrate/scripts/fsj.py`); pure stdlib, Python >= 3.11. Journal:
`$FORECAST_JOURNAL` or `./forecasts.jsonl`. Without Python, read the JSONL directly and report what
you can — but say that scoring was manual.

## Step 1 — Find what's due

```
python fsj.py due [--json]
```

## Step 2 — Resolve each due record

For each: research what actually happened, judged **strictly against the record's
`resolution_criterion`** — not against the question's vibe. Then:

```
python fsj.py resolve --id <id> --outcome true|false --note "<what happened, with source>"
```

- Outcome unknowable right now (data not yet published)? Leave it open and say so.
- Criterion turned out ambiguous, or the resolution source vanished? Annul it — annulled records
  are excluded from scoring, which is honest, unlike guessing:
  `python fsj.py resolve --id <id> --annul --note "<why>"`
- The tool refuses to re-resolve — or annul — a record that already carries a resolution (a
  resolution is calibration *data*); pass `--overwrite` only to fix a genuine mistake, and say
  you did. Annulling a scored miss is exactly the kind self-grading that destroys the signal.

## Step 3 — Score

```
python fsj.py score [--json]
```

Report the Brier score (0 is perfect; 0.25 = always saying 50%), N, and the **direction** — the
actionable part. Honor the small-N flag: below the threshold the report itself carries, direction
is noise; report it, don't act on it.

## Step 4 — Post-mortem the misses (and spot-check the hits)

For each surprising resolution, tag which pipeline step failed — this is what makes the loop
improve rather than just tally:

| Step | Failure looks like |
|---|---|
| question-hygiene | resolved on a technicality you didn't read for; was already effectively resolved at forecast time |
| research | the decisive fact was public and findable; single-source error |
| reasoning | wrong reference class; ignored the status quo; catastrophized a sub-scenario; stale base rate with a lapsed mechanism |
| aggregation | one wild draw dragged the pool; ignored a crowd number that was right |

Check hits too — a right answer for a wrong reason is a future miss. Look for **clusters**: several
misses sharing one wrong world-model assumption count as one lesson, not many.

## Step 5 — Recalibrate (beliefs, not the scorer)

If the direction is systematic and N is honest — e.g. over-confident: shade future high-confidence
calls toward the base rate until the direction clears. State the adjustment explicitly so future
forecasts can apply it. Never adjust the scoring itself, past records, or resolution criteria to
flatter the track record.

## Cadence

Worth running: at the start of a working session on forecasts, on a schedule (a daily or weekly
automated run of exactly Steps 1–3 works unattended), and whenever the user asks how their
predictions are going.
