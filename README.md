# forecast-scaffold

A general-purpose **forecasting scaffold** for AI agents: a pair of skills plus a tiny
zero-dependency Python core that turn "will X happen?" into a calibrated, recorded, *scored*
probability.

- **`forecast`** — operationalize the question (exact resolution criterion + date), research it
  (outside view first), reason through a structured scratchpad, pool multiple independent draws,
  and record the result. Effort auto-scales (`auto` / `low` / `medium` / `high`).
- **`calibrate`** — resolve recorded forecasts when their date passes, compute the Brier score and
  the direction of miscalibration, and turn misses into lessons.
- **`forecast_scaffold` (Python, stdlib-only)** — the journal (append-only JSONL), scoring,
  aggregation math, validators, and CLI that both skills call.

The methodology is markdown you can read and fork; everything that churns — model preferences,
draw counts, clamp bands — lives in [config/forecast.toml](config/forecast.toml), not in the
skills. The design is distilled from the published evidence on LLM and human forecasting
(Halawi et al. 2024; the Metaculus AI tournament results and surveys; Good Judgment Project
training RCTs; the aggregation literature). Sources and rationale: [docs/design.md](docs/design.md).

## Install

**Claude Code (plugin):**

```
/plugin marketplace add edisonymy/forecast-scaffold
/plugin install forecast-scaffold@forecast-scaffold
```

**claude.ai / Claude web** (skills upload): build the bundles with
`bash scripts/build_skill_bundles.sh` and upload `dist/*.zip` in Settings → Skills (org admins
can deploy them org-wide).

**Any agent that reads the open SKILL.md standard:** point it at `skills/`.

**Just the Python core:** `pip install .` (no dependencies), then `forecast-scaffold --help`.

## The loop, in 60 seconds

```bash
# 1. record a forecast (the skills do this for you, with research + ensembling first)
python skills/forecast/scripts/fsj.py record \
  --question "Will the Bank of England cut Bank Rate before 2026-10-01?" \
  --probability 0.63 --resolve-by 2026-10-01 \
  --criterion "a cut announced at any MPC meeting before the date, per bankofengland.co.uk" \
  --reference-class "MPC easing cycles since 2000" --base-rate 0.55 \
  --reasoning "market-implied ~0.68; shaded toward the base rate for over-confidence"

# 2. when the date passes: resolve it
python skills/forecast/scripts/fsj.py due
python skills/forecast/scripts/fsj.py resolve --id <id> --outcome true --note "cut on 2026-09-17"

# 3. score yourself
python skills/forecast/scripts/fsj.py score
# Calibration over N = 1 resolved forecast(s): Brier 0.137, direction: insufficient data ...
```

The journal is a plain JSONL file (`$FORECAST_JOURNAL`, default `./forecasts.jsonl`) —
[docs/schema.md](docs/schema.md) is the spec. Commit it to git and your track record is public and
tamper-evident. (Inside a clone of *this* repo, `forecasts.jsonl` is deliberately gitignored —
keep your journal in your own repo, or point `$FORECAST_JOURNAL` somewhere you commit.)

## Surfaces

| Surface | How | Journal persistence |
|---|---|---|
| Claude Code / compatible CLIs | plugin install (above) | project file — durable |
| Agent SDK / headless | load `skills/`, run scheduled `forecast` + `calibrate` passes | file you control — durable |
| claude.ai web | upload `dist/*.skill` bundles | no durable file system: the skill emits journal lines for you to keep |

## Is it any good? (honest status)

This scaffold encodes what measurably works, but the only honest answer is a scored track record.
The milestone ladder we hold it to, in order: beat always-0.5 → beat a zero-shot frontier model on
paired questions → approach the Metaculus community prediction → place in a
[Metaculus FutureEval](https://www.metaculus.com/futureeval/) bot-tournament season. The
tournament harness that drives these same skills lives in [bot/](bot/README.md); no live seasons
have been entered yet, so there is no track record to show — the evaluation protocol it will be
held to is in [docs/evaluation.md](docs/evaluation.md).

## Design principles

1. **Judgment in markdown, arithmetic in code.** Skills never restate math; `fsj.py` owns
   scoring, pooling, clamps. Constants exist in exactly one place.
2. **Model-agnostic by construction.** Model choice dominates scaffolding, and any scaffold pinned
   to a model decays with it — so models are config, retrieval is whatever the host agent has, and
   the skills are plain markdown per the open Agent Skills standard.
3. **Falsifiable or it's worthless.** No forecast without a resolution criterion and a date; no
   silent re-resolution; annulment instead of convenient grading.

## License

MIT. The numeric-CDF construction (`percentiles_to_cdf` and its standardization constants) is
ported from the MIT-licensed
[forecasting-tools](https://github.com/Metaculus/forecasting-tools) project (Copyright (c) 2024
CodexVeritas), with attribution in `src/forecast_scaffold/core.py`. The aggregation rules follow
the published literature (Halawi et al. 2024; Sevilla; Satopää et al.), cited in
[docs/design.md](docs/design.md).
