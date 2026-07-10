"""Weights-contamination probe: can MODEL recall a resolved question's outcome, NO tools?

The one leak bench/timevault.py cannot close is the model's own training data. Stated
cutoffs under-report effective knowledge by ~3-4 months (Paleka et al., the same source
docs/evaluation.md already leans on), so admissibility of a (model, question) pair is an
EMPIRICAL question, not a model-card lookup. This probe measures it: ask the model
directly, with every tool stripped, whether each already-resolved question resolved YES
or NO, and score its recall against the stored resolutions.

Interpretation discipline (also printed with the report):
- The signal is DIFFERENTIAL: a model whose training data covers the resolution window
  (positive control, e.g. sonnet-5 on late-2025 events) should show high confident-recall
  accuracy; a model with a clearly earlier cutoff should sit near the majority-class
  baseline. Absolute accuracy alone is NOT contamination — smart models guess base rates.
- A question is flagged contaminated FOR THAT MODEL when the model claims memory
  (recall != unknown), is correct, and states confidence >= the flag threshold.
- The probe under-detects: a model can hold latent outcome knowledge it does not surface
  as explicit memory. Treat 'clean' as 'no detected recall', never as proof.

Usage:
    python bench/contamination_probe.py bench/sets/2026-07-05-btf2.jsonl \
        --models claude-opus-4-6,claude-haiku-4-5,claude-sonnet-5 \
        --qids-from bench/results/2026-07-05-btf2.opus46.results.jsonl --concurrency 6
    python bench/contamination_probe.py bench/sets/2026-07-05-btf2.jsonl --report

    # Non-Anthropic model via OpenRouter (cross-model ensemble contamination checks;
    # needs OPENROUTER_API_KEY, same routing run_bench.py --provider openrouter uses):
    python bench/contamination_probe.py bench/sets/2026-07-05-btf2.jsonl \
        --models google/gemini-2.5-pro --provider openrouter

    # Same, but via the direct native chat API (no CLI scaffolding; fixes the empty
    # result the Anthropic-compat endpoint returns for non-Anthropic models):
    python bench/contamination_probe.py bench/sets/2026-07-05-btf2.jsonl \
        --models google/gemini-2.5-pro --provider openrouter-direct
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bot"))

# ruff: noqa: E402  (imports follow the sys.path bootstrap above)
from direct_agent import run_direct
from run_bot import PROVIDERS, extract_json, openrouter_model_cmd, run_agent

from forecast_scaffold.core import SCAFFOLD_VERSION

# The direct transport is a fourth provider local to the bench (run_bot's PROVIDERS is
# read-only): a tool-less single completion straight to OpenRouter's native chat API,
# bypassing the CLI's ~22k-token scaffolding and its empty-result bug on non-Anthropic
# models. See bench/direct_agent.py for the two measured problems it fixes.
PROBE_PROVIDERS = (*PROVIDERS, "openrouter-direct")

RESULTS_DIR = ROOT / "bench" / "results"
# Belt over the absent --allowed-tools: no research or filesystem path may exist,
# otherwise "recall" silently becomes "lookup" and the measurement is void.
PROBE_DISALLOWED = ("WebSearch,WebFetch,Read,Glob,Grep,Bash,Write,Edit,"
                    "NotebookEdit,Task")
CONFIDENCE_FLAG = 0.75  # confident-correct recall at/above this marks contamination

PROBE_SYSTEM = (
    "You are taking a MEMORY RECALL test about real-world events. You have NO tools — "
    "do not attempt to use any. Answer ONLY from what you remember from training data.\n"
    "The forecasting question below is already RESOLVED: its event window is in the "
    "past. Report whether you actually REMEMBER the outcome.\n"
    "Honesty contract: 'unknown' is the CORRECT answer when you have no specific memory "
    "of relevant reporting. Inferring from base rates, plausibility, or how such "
    "questions usually resolve is NOT recall — answer 'unknown' in that case. This test "
    "measures memory, not forecasting skill; a guessed 'yes'/'no' corrupts it.\n"
    "END your reply with exactly one fenced json block, no text after it:\n"
    '```json\n{"recall": "yes", "confidence": 0.9, '
    '"basis": "<1-2 lines: the specific events/reporting you remember, or why unknown>"}'
    "\n```\n"
    'where recall is one of "yes" (you remember it resolved YES), "no" (you remember '
    'it resolved NO), or "unknown", and confidence is 0.0-1.0.'
)


def build_probe_prompt(spec: dict, today: str) -> str:
    """The question as a recall item: criteria included so the exact contract is
    identifiable; NO as-of framing — the model SHOULD use post-event knowledge here."""
    return "\n".join([
        f"Today's date: {today}. The following question's outcome was determined in "
        "the past.",
        f"# Question: {spec['question']}",
        "\n## Resolution criteria (verbatim)",
        str(spec.get("criteria", ""))[:2500],
        f"\nResolution was due by: {spec.get('resolve_by') or 'its stated window'}",
        "\nFrom your MEMORY only: did this question resolve YES or NO?",
    ])


def parse_probe_payload(payload: dict) -> tuple[str, float, str] | None:
    recall = str(payload.get("recall", "")).strip().lower()
    if recall not in ("yes", "no", "unknown"):
        return None
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    confidence = min(1.0, max(0.0, confidence))
    return recall, confidence, str(payload.get("basis", ""))[:400]


def probe_one(spec: dict, model: str, args: argparse.Namespace) -> dict | None:
    prompt = build_probe_prompt(spec, datetime.now(UTC).strftime("%Y-%m-%d"))
    provider = getattr(args, "provider", "subscription")
    direct = provider == "openrouter-direct"
    if not direct:
        base_cmd = f"claude -p --model {model} --output-format json"
        if provider == "openrouter":
            # Same rewrite run_bench.py uses: a bare Anthropic id gets "anthropic/"
            # prefixed, a slug already carrying "/" (google/gemini-2.5-pro) passes through.
            base_cmd = openrouter_model_cmd(base_cmd)
        cmd = f"{base_cmd} --disallowed-tools {PROBE_DISALLOWED}"
    # Direct transport: no CLI command and no tools to disallow — a tool-less single
    # completion is exactly the probe's contract (recall must never become lookup). Bare
    # Anthropic ids still get the "anthropic/" slug prefix the native API expects.
    direct_model = model if "/" in model else f"anthropic/{model}"
    parsed = None
    cost = 0.0
    errors: list[str] = []
    for attempt in range(2):
        attempt_prompt = prompt if attempt == 0 else (
            prompt + "\n\nYour previous output was invalid: " + "; ".join(errors)
            + "\nEmit a corrected fenced json block.")
        try:
            if direct:
                output, attempt_cost, _ = run_direct(
                    attempt_prompt, PROBE_SYSTEM, direct_model, args.timeout)
            else:
                output, attempt_cost, _ = run_agent(cmd, attempt_prompt, PROBE_SYSTEM,
                                                    args.timeout, provider)
            cost += attempt_cost
            parsed = parse_probe_payload(extract_json(output))
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            errors = [str(exc)[:200]]
            continue
        if parsed is not None:
            break
        errors = ['need {"recall": "yes"|"no"|"unknown", "confidence": 0-1, "basis"}']
    if parsed is None:
        print(f"    FAILED {model} on {spec['id']}: {errors}")
        return None
    recall, confidence, basis = parsed
    resolution = int(spec["resolution"])
    correct = None if recall == "unknown" else (recall == ("yes" if resolution else "no"))
    return {
        "qid": spec["id"], "model": model, "question": spec["question"][:160],
        "recall": recall, "confidence": confidence, "basis": basis,
        "resolution": resolution, "correct": correct, "cost_usd": round(cost, 4),
        "provider": provider,
        "scaffold_version": SCAFFOLD_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def contaminated(row: dict, threshold: float = CONFIDENCE_FLAG) -> bool:
    return bool(row.get("correct")) and float(row.get("confidence", 0.0)) >= threshold


def report(rows: list[dict], base_rate_yes: float) -> str:
    """Per-model recall table + contaminated-question lists. Pure function for tests."""
    majority = max(base_rate_yes, 1 - base_rate_yes)
    lines = [f"question-set majority-class baseline: {majority:.0%} "
             f"(YES base rate {base_rate_yes:.0%})",
             f"contamination flag: correct recall with confidence >= {CONFIDENCE_FLAG}",
             ""]
    lines.append(f"{'model':<22} {'n':>4} {'answered':>9} {'acc@ans':>8} "
                 f"{'lift':>6} {'flagged':>8}")
    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_model.setdefault(row["model"], []).append(row)
    flags: dict[str, list[str]] = {}
    for model in sorted(by_model):
        model_rows = by_model[model]
        answered = [r for r in model_rows if r["recall"] != "unknown"]
        correct = [r for r in answered if r["correct"]]
        accuracy = (len(correct) / len(answered)) if answered else 0.0
        lift = accuracy - majority if answered else 0.0
        flagged = [r["qid"] for r in model_rows if contaminated(r)]
        flags[model] = flagged
        lines.append(f"{model:<22} {len(model_rows):>4} "
                     f"{len(answered):>4} ({len(answered)/len(model_rows):>3.0%}) "
                     f"{accuracy:>7.0%} {lift:>+6.0%} {len(flagged):>8}")
    lines.append("")
    for model, qids in flags.items():
        lines.append(f"contaminated for {model} ({len(qids)}): "
                     + (", ".join(qids) if qids else "none detected"))
    lines.append("")
    lines.append("caveat: 'none detected' means no surfaced recall — latent knowledge "
                 "can still shape forecasts; treat as admissible, not proven-clean.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("set_file", help="resolved-question set (needs resolution field)")
    parser.add_argument("--models", default="claude-opus-4-6")
    parser.add_argument("--provider", default="subscription", choices=PROBE_PROVIDERS)
    parser.add_argument("--qids-from", default="",
                        help="results jsonl whose qids restrict the probe (e.g. the "
                             "scored subset of a prior bench run)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--budget", type=float, default=0.0)
    parser.add_argument("--report", action="store_true",
                        help="print the table from existing results and exit")
    args = parser.parse_args(argv)

    set_path = Path(args.set_file)
    specs = [json.loads(line) for line in set_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    specs = [s for s in specs if s.get("resolution") in (0, 1, "0", "1")]
    for spec in specs:
        spec["resolution"] = int(spec["resolution"])
    base_rate_yes = (sum(s["resolution"] for s in specs) / len(specs)) if specs else 0.0

    results_path = RESULTS_DIR / f"{set_path.stem}.probe.jsonl"
    done_rows: list[dict] = []
    if results_path.exists():
        done_rows = [json.loads(line) for line
                     in results_path.read_text(encoding="utf-8").splitlines()
                     if line.strip()]
    if args.report:
        print(report(done_rows, base_rate_yes))
        return 0

    if args.qids_from:
        keep = {json.loads(line)["qid"] for line
                in Path(args.qids_from).read_text(encoding="utf-8").splitlines()
                if line.strip()}
        specs = [s for s in specs if s["id"] in keep]
    if args.limit:
        specs = specs[: args.limit]

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    done = {(row["qid"], row["model"]) for row in done_rows}
    jobs = [(spec, model) for spec in specs for model in models
            if (spec["id"], model) not in done]
    print(f"{len(specs)} questions x {len(models)} models = {len(specs) * len(models)} "
          f"probes ({len(done)} already done, {len(jobs)} to run) -> {results_path}")

    lock = threading.Lock()
    spent = 0.0
    failures = 0
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with results_path.open("a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        def work(job: tuple[dict, str]) -> dict | None:
            if args.budget > 0:
                with lock:
                    if spent >= args.budget:
                        return None
            return probe_one(job[0], job[1], args)

        for idx, row in enumerate(
            fut.result() for fut in as_completed(pool.submit(work, j) for j in jobs)
        ):
            with lock:
                if row is None:
                    failures += 1
                    continue
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                spent += row["cost_usd"]
                done_rows.append(row)
                mark = "CONTAM" if contaminated(row) else ("ok" if row["recall"] == "unknown"
                                                           else row["recall"])
                print(f"[{idx + 1}/{len(jobs)}] {row['model']:<20} {mark:<7} "
                      f"conf={row['confidence']:.2f} ${spent:.2f} "
                      f"{row['question'][:48]}")
    print()
    print(report(done_rows, base_rate_yes))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
