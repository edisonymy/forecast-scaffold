# Fortnightly review

Every two weeks: fix what is plainly broken, and bring the bot up to date with the field —
new models, and practices that are working for others. The output is one draft pull
request whose description says what was found, what changed and what was left alone.

## 1. Look

- **Standing.** Dispatch `list-forecasts.yml` with `standing_slugs` set to the last two
  finished MiniBench waves (`minibench-YYYY-MM-DD`; plain `minibench` is the wave in
  progress) and read the `Standing` step. CI holds the token.
- **Health.** `python bench/depth.py` (work per research run; exit 1 means it moved),
  `bot/journal/failures.jsonl`, open alert issues.
- **Misses.** Read the traces of the fortnight's five worst-scored questions. Look for
  something that went wrong in the process, not for a forecast that was unlucky.
- **The field.** What changed outside since the last review:
  - which `metac-*` rows lead the standing report. Metaculus enters plain bots on the same
    questions, one per model and harness, so those rows show what is winning right now;
  - Metaculus announcements, the season's bot-maker survey, and changes to its template
    bot (the `forecasting-tools` repository);
  - new models from our provider;
  - papers and open-source bots worth reading.

## 2. Patch

Anything broken or drifting gets fixed at its cause: failed submissions, a crash, a dead
credential, telemetry that lies, a default that changed under us.

## 3. Adopt

**A new model.** When our provider ships a newer model, move to it.

1. Dry-run a few questions through `bot-test.yml` and compare the work per research run
   with the current model. The effort level is always written out, never left to the
   model's default: set it so a run does at least as much work as before.
2. Switch. When unsure, give the newcomer one of the three research runs first
   (`run_models`), so it forecasts the same live questions before it carries all of them.
3. At the next review, read its row against the others. Switch back if it is plainly
   behind.

Accuracy is judged on live questions, not on a backtest: a newer model's training data can
contain the answer to a resolved question.

**A new practice.** Adopt what is demonstrably working: a tool, a research source, a
setting, a change the winning bots or Metaculus's own bots have made. The clearest prompt
to act is a `metac-*` bot on our own model beating us on the same questions.

## 4. Keep it simple

- Reach for a tool, a setting or a deletion before prompt text. Never add a sentence
  because of one bad question.
- When something goes in, look for something to take out. Note these three numbers in the
  pull request so growth is visible: characters in the system prompt, agent calls per
  question, lines in `bot/run_bot.py`.
- One wave is about 58 questions, and its average moves by 3 to 4 points per question on
  luck alone. Rank from a single wave is not a reason to change anything; something
  plainly broken is.
