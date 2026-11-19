# Fortnightly review loop

A review that runs every two weeks, reads how the bot did, and proposes at most one
change. Its default outcome is **no change**. It exists to catch what a human stops
watching for (a silent default change, a dead credential, a field that moved), not to
tune the bot on the last 30 questions.

The reviewer may not edit this file. Only the operator changes the rules.

## 1. Read (nothing else is evidence)

1. Standing. Dispatch `list-forecasts.yml` with `standing_slugs` set to the last two
   finished MiniBench waves (`minibench-YYYY-MM-DD`; plain `minibench` is the wave in
   progress) and read the `Standing` step. CI holds the token.
2. Our own scores: `python bench/sync_resolutions.py --no-sync --readout`.
3. Health: `bot/journal/failures.jsonl`, open alert issues, and `python bench/depth.py`
   (work per research run, last 14 days against the 28 before; exit 1 means it moved).
4. Every preregistered scorer in `bench/analysis/` whose sample-size gate is now met.

## 2. The control group

Metaculus enters plain template bots on the same questions, one per model
(`metac-<model>-high+asknews`). The row for the model we run is the bar:

- At or below it over the last 100 scored questions: the harness is not paying for
  itself. The proposal is a deletion or a setting, never an addition.
- A different `metac-*` row above ours by a margin that holds in two consecutive
  reviews: propose that model.

## 3. A new model

Nothing in the prompts or skills is rewritten for a model. The only per-model setting is
the effort level, and it is always written out, never left to the model's default.

1. **Notice.** A `metac-<model>` row for a newer model from our provider appears in the
   standing report. Metaculus adds one within days of a release, so the row is both the
   signal and the first evidence.
2. **Match the work.** Dry-run the candidate through `bot-test.yml` and compare its
   per-call telemetry with the incumbent's baseline in `bench/depth.py`. Pick the lowest
   effort level at which a research run does at least as much work. This is the part a
   backtest can answer.
3. **Canary.** Give the candidate one of the three research runs. It forecasts the same
   live questions as the incumbent and carries a third of the pool. Accuracy is not
   backtested: a newer model's training data can contain the outcome of a resolved
   question, so a backtest flatters exactly the model under test.
4. **Promote or remove.** After 100 scored questions, promote the candidate if its runs
   score at least as well as the incumbent's on the same questions and its `metac-*` row
   is not below the incumbent's. Otherwise remove it. Either way the canary slot empties.

Steps 2 to 4 need three small changes that are not built yet: the model and its effort
level in one place (the model id is repeated in five), `run_models` honoured by the
parallel-research path, and a paired per-run scorer.

## 4. What a review may propose

| Class | Examples | Bar |
|---|---|---|
| Fix | failed submits, a crash, a default that changed under us, broken telemetry | reproduced from the journal or logs |
| Setting | model id, effort level, run count, a research source on or off | sections 2 and 3, or a paired comparison whose 90% interval excludes zero |
| Structure | a stage, a prompt section, a rule in a skill | a preregistered paired test, already scored, and the budget in section 5 |

Not allowed, whatever the evidence looks like:

- a prompt sentence written in response to one bad question. A pattern needs at least
  10 scored questions and both of the last two waves;
- more than one pull request per review;
- merging, changing repository variables or secrets, or re-litigating a preregistered rule.

## 5. The budget only goes down

Three numbers, recorded in every report:

| | 2026-11-19 |
|---|---|
| system prompt, characters (`build_system`, medium, binary) | 16,122 |
| agent calls per question at medium | 5 |
| lines in `bot/run_bot.py` | 3,665 |

A pull request that raises one of them lowers another by more, in the same pull request.

Every review also names one deletion candidate: the stage, section or rule with the
weakest evidence for its own existence. When its scorer says drop, that deletion is the
review's pull request and takes priority over anything else.

## 6. Output

One comment on the `Fortnightly review` issue, 40 lines at most: the standing table, the
three budget numbers, what changed since the last review, the deletion candidate, and
either a link to one draft pull request or the words "no change".
