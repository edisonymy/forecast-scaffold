"""Tournament bot: drives the SAME forecast skill the plugin ships, headlessly.

For each open question in a tournament: triage the effort tier (auto = one cheap agent
call), run the forecast skill via a headless agent (``claude -p`` by default) under a
fenced-JSON output contract, validate (one repair retry), record to the bot's public
journal, and submit — unless ``--dry-run``.

The bot is a *consumer* of the skills: no forecasting logic lives here, only plumbing.

Usage:
    python bot/run_bot.py --tournament <id-or-slug> --dry-run
    python bot/run_bot.py --tournament <id-or-slug> --limit 5
Env:
    METACULUS_TOKEN   required to submit (reads may work without)
    FORECAST_JOURNAL  overrides the journal path (default bot/journal/forecasts.jsonl)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ruff: noqa: E402  (imports follow the sys.path bootstrap above)
from metaculus import MetaculusClient

from forecast_scaffold.core import (
    ForecastRecord,
    Journal,
    _utc_now,
    clamp,
    load_config,
    percentiles_to_cdf,
    validate_mc,
    validate_percentiles,
    validate_probability,
)

SKILL = ROOT / "skills" / "forecast"
DEFAULT_JOURNAL = ROOT / "bot" / "journal" / "forecasts.jsonl"
FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
# Continuous question types: elicited as percentiles, submitted as a CDF. Metaculus `date`
# questions are timestamp-scaled continuous questions and flow through the same path.
CONTINUOUS = ("numeric", "discrete", "date")

TRIAGE_PROMPT = (
    "You are triaging a forecasting question for effort allocation, using the forecast skill's "
    "Step 0 rubric (stakes are uniform in a tournament, so only the other signals apply): "
    "start at medium; multiple-choice, numeric, or conditional shape -> at least medium; "
    "genuinely contested with no decent anchor -> bump one tier up; near-certain status quo, or "
    "a liquid community prediction already answers it -> drop one tier down. Reply with ONLY a "
    'fenced json block like ```json {"tier": "medium"} ``` '
    "where tier is one of low|medium|high.\n\nQuestion:\n"
)

CONTRACT = """
## Output contract (bot mode — mandatory)

You are running headlessly. Work through the skill (research with your available tools, the
reasoning spine, the tier's number of draws) — but do NOT run `fsj.py record` and do not write
any journal file: the harness records and submits for you; the skill's Step 5 happens outside
your context. END your reply with exactly one fenced json block, no text after it:

For binary:
```json
{"probability": 0.63, "raw_draws": [0.6, 0.65, 0.62], "reasoning": "<3-6 lines>",
 "reference_class": "...", "base_rate": 0.35, "what_would_change_my_mind": ["..."]}
```
For multiple_choice (probabilities over the EXACT option labels given, summing to 1):
```json
{"probabilities": {"<option A>": 0.5, "<option B>": 0.5}, "reasoning": "..."}
```
For numeric/discrete/date (strictly increasing, strictly inside the stated bounds; for date
questions the values are unix timestamps in seconds, matching the bounds given). Optionally
include "expected_value" (your mean/EV point estimate, same units):
```json
{"percentiles": {"10": 1.0, "25": 2.0, "50": 3.0, "75": 4.0, "90": 5.0},
 "expected_value": 3.2, "reasoning": "..."}
```
"""


def build_brief(post: dict[str, Any], question: dict[str, Any], crowd: float | None) -> str:
    parts = [
        f"# Question: {question.get('title', post.get('title', ''))}",
        f"Type: {question.get('type', 'binary')}",
        f"Closes: {question.get('scheduled_close_time', 'unknown')}",
        "\n## Resolution criteria (verbatim — the contract)",
        str(question.get("resolution_criteria", "")),
        "\n## Fine print",
        str(question.get("fine_print", "")),
        "\n## Background",
        str(question.get("description", ""))[:4000],
    ]
    if question.get("type") == "multiple_choice":
        parts.append(f"\n## Options\n{json.dumps(question.get('options') or [])}")
    if question.get("type") in CONTINUOUS:
        scaling = question.get("scaling") or {}
        parts.append(
            "\n## Bounds\n"
            f"range_min={scaling.get('range_min')} range_max={scaling.get('range_max')} "
            f"zero_point={scaling.get('zero_point')} "
            f"open_lower={question.get('open_lower_bound')} "
            f"open_upper={question.get('open_upper_bound')}"
        )
    if crowd is not None:
        parts.append(f"\n## Community prediction (at fetch time)\n{crowd}")
    return "\n".join(parts)


def run_agent(agent_cmd: str, prompt: str, system: str | None, timeout: int) -> tuple[str, float]:
    """Run the headless agent; returns (text, cost_usd).

    When the agent is ``claude -p --output-format json`` the stdout is a result envelope
    carrying ``total_cost_usd`` — unwrap it so the journal can record what each forecast
    cost. Plain-text agents just cost 0.0 (unknown).
    """
    cmd = [*shlex.split(agent_cmd), prompt]
    if system:
        cmd += ["--append-system-prompt", system]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"agent failed ({result.returncode}): {result.stderr[:500]}")
    try:
        envelope = json.loads(result.stdout)
        if isinstance(envelope, dict) and "result" in envelope:
            return str(envelope["result"]), float(envelope.get("total_cost_usd") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return result.stdout, 0.0


def extract_json(text: str) -> dict[str, Any]:
    matches = FENCED_JSON.findall(text)
    if not matches:
        raise ValueError("no fenced json block in agent output")
    parsed: dict[str, Any] = json.loads(matches[-1])
    return parsed


def triage(agent_cmd: str, brief: str, timeout: int) -> tuple[str, float]:
    try:
        output, cost = run_agent(agent_cmd, TRIAGE_PROMPT + brief[:2000], None, timeout)
        tier = extract_json(output).get("tier", "medium")
        return (tier if tier in ("low", "medium", "high") else "medium"), cost
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return "medium", 0.0


def validate_payload(payload: dict[str, Any], question: dict[str, Any]) -> list[str]:
    qtype = question.get("type", "binary")
    if qtype == "binary":
        p = payload.get("probability")
        if not isinstance(p, int | float) or not 0 < float(p) < 1:
            return [f"binary needs a probability in (0,1), got {p!r}"]
        return []
    if qtype == "multiple_choice":
        probs = payload.get("probabilities")
        if not isinstance(probs, dict):
            return ["multiple_choice needs a probabilities object"]
        options = [str(o) for o in question.get("options") or []]
        missing = [o for o in options if o not in probs]
        if missing:
            return [f"missing options: {missing}"]
        return validate_mc(list(probs.keys()), [float(v) for v in probs.values()])
    if qtype in CONTINUOUS:
        pct = payload.get("percentiles")
        if not isinstance(pct, dict):
            return [f"{qtype} needs a percentiles object"]
        return validate_percentiles({str(k): float(v) for k, v in pct.items()})
    return [f"unsupported question type {qtype!r}"]


def forecast_question(
    client: MetaculusClient,
    post: dict[str, Any],
    question: dict[str, Any],
    args: argparse.Namespace,
    config: dict[str, Any],
    journal: Journal,
) -> bool:
    crowd = client.community_prediction(question)
    brief = build_brief(post, question, crowd)
    run_cost = 0.0
    if args.effort != "auto":
        tier = args.effort
    else:
        tier, triage_cost = triage(args.agent_cmd, brief, args.timeout)
        run_cost += triage_cost
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    system = (
        f"You have this skill (references in {SKILL / 'references'}, scripts in "
        f"{SKILL / 'scripts'}):\n\n{skill_text}\n\nRun it at effort tier: {tier}.\n{CONTRACT}"
    )

    payload: dict[str, Any] | None = None
    errors: list[str] = []
    for attempt in range(2):
        prompt = brief if attempt == 0 else (
            brief + "\n\nYour previous output was invalid: "
            + "; ".join(errors) + "\nEmit a corrected fenced json block."
        )
        try:
            output, attempt_cost = run_agent(args.agent_cmd, prompt, system, args.timeout)
            run_cost += attempt_cost
            candidate = extract_json(output)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            errors = [str(exc)]
            continue
        errors = validate_payload(candidate, question)
        if not errors:
            payload = candidate
            break
    if payload is None:
        print(f"  SKIP (invalid after retry): {errors}")
        return False

    qtype = question.get("type", "binary")
    title = question.get("title", post.get("title", "untitled"))
    criterion = str(question.get("resolution_criteria", "")).strip()[:2000]
    if not criterion:
        # Metaculus sometimes returns empty criteria; the title is the resolvable contract.
        criterion = f"(no criteria published) Resolves per the question as stated: {title}"
    record = ForecastRecord(
        question=title,
        question_type=qtype if qtype in ("binary", "multiple_choice", "date") else "numeric",
        resolution_criterion=criterion,
        forecast_at=_utc_now(),
        resolve_by=str(question.get("scheduled_resolve_time", ""))[:10] or None,
        source={
            "platform": "metaculus",
            "question_id": question.get("id"),
            "url": f"https://www.metaculus.com/questions/{post.get('id')}/",
        },
        reference_class=str(payload.get("reference_class", "")),
        base_rate=payload.get("base_rate"),
        probability=float(payload["probability"]) if qtype == "binary" else None,
        options=[str(o) for o in question.get("options") or []] or None,
        probabilities=(
            [float(payload["probabilities"][str(o)]) for o in question.get("options") or []]
            if qtype == "multiple_choice" else None
        ),
        percentiles=(
            {str(k): float(v) for k, v in payload["percentiles"].items()}
            if qtype in CONTINUOUS else None
        ),
        expected_value=(
            float(payload["expected_value"])
            if payload.get("expected_value") is not None else None
        ),
        cost_usd=round(run_cost, 4) if run_cost else None,
        raw_draws=[float(d) for d in payload.get("raw_draws", [])] or None,
        effort=f"{tier} (auto)" if args.effort == "auto" else tier,
        model=args.agent_cmd,
        crowd={"value": crowd, "source": "metaculus community", "at": _utc_now()}
        if crowd else None,
        reasoning=str(payload.get("reasoning", ""))[:4000],
        what_would_change_my_mind=[str(x) for x in payload.get("what_would_change_my_mind", [])],
    )
    for warning in validate_probability(record.probability, config) if record.probability else []:
        print(f"  warning: {warning}")
    journal.append(record)
    print(f"  recorded {record.id} (tier {tier})")

    if args.dry_run:
        print("  dry-run: not submitting")
        return True

    question_id = int(question["id"])
    if qtype == "binary":
        client.submit_binary(question_id, clamp(float(payload["probability"]), 0.01, 0.99))
    elif qtype == "multiple_choice":
        probs = {str(k): float(v) for k, v in payload["probabilities"].items()}
        total = sum(probs.values())
        client.submit_multiple_choice(question_id, {k: v / total for k, v in probs.items()})
    else:
        scaling = question.get("scaling") or {}
        if scaling.get("range_min") is None or scaling.get("range_max") is None:
            raise ValueError(f"continuous question {question_id} has no numeric bounds")
        outcome_count = question.get("inbound_outcome_count")  # set on discrete questions
        cdf = percentiles_to_cdf(
            {str(k): float(v) for k, v in payload["percentiles"].items()},
            float(scaling["range_min"]),
            float(scaling["range_max"]),
            lower_open=bool(question.get("open_lower_bound")),
            upper_open=bool(question.get("open_upper_bound")),
            zero_point=scaling.get("zero_point"),
            cdf_size=int(outcome_count) + 1 if outcome_count else 201,
        )
        client.submit_cdf(question_id, cdf)
    print("  submitted")
    if args.comment and record.reasoning:
        client.comment(int(post["id"]), record.reasoning, private=True)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tournament", required=True, help="tournament id or slug")
    parser.add_argument("--dry-run", action="store_true", help="record locally, never submit")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--effort", default="auto", choices=["auto", "low", "medium", "high"])
    parser.add_argument("--agent-cmd", default="claude -p", help="headless agent command")
    parser.add_argument("--timeout", type=int, default=1200, help="seconds per agent call")
    parser.add_argument(
        "--journal", default=os.environ.get("FORECAST_JOURNAL", str(DEFAULT_JOURNAL))
    )
    parser.add_argument("--comment", action="store_true", help="post reasoning as private comment")
    parser.add_argument("--include-forecasted", action="store_true",
                        help="re-forecast questions this account already forecast")
    args = parser.parse_args(argv)

    config = load_config()
    client = MetaculusClient()
    Path(args.journal).parent.mkdir(parents=True, exist_ok=True)
    journal = Journal(args.journal)

    posts = client.open_posts(args.tournament, limit=args.limit)
    print(f"{len(posts)} open post(s) in {args.tournament}")
    done = 0
    for post in posts:
        for question in client.questions_of(post):
            if not args.include_forecasted and client.already_forecasted(question):
                continue
            print(f"- {question.get('title', post.get('title'))!r}")
            try:
                done += forecast_question(client, post, question, args, config, journal)
            except Exception as exc:  # noqa: BLE001 - one bad question must not kill the run
                print(f"  ERROR: {exc}")
    print(f"forecast {done} question(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
