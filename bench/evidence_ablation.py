"""Inverted BTF-2: hold reasoning fixed, ablate the EVIDENCE (issue #9's experiment).

The BTF-2 pilot measured reasoning-given-evidence (null). This measures evidence
elasticity: the same zero-shot reasoning config on the same resolved questions, with the
frozen dossier served at four quality levels. The threshold hypothesis (weak search ~= no
search, but good evidence matters — SPY Lab arXiv:2506.00723) predicts Brier CLIFFS when
the dossier drops below adequacy then plateaus; flat everywhere means the model prior
dominates this corpus; a smooth slope favors evidence-volume investment.

Builds four set files from an existing BTF-2 set, then prints the run_bench commands
(cheap model, zero tier under --leakfree none, the tool-less cell — evidence is the ONLY
varying factor):

    python bench/evidence_ablation.py bench/sets/2026-07-05-btf2.jsonl

Arms: full (as shipped) | half (first 50%% of the dossier) | stub (first 500 chars) |
none (dossier removed; the AS-OF header stays so the model still knows the date).

LEAK HYGIENE: the ablation set files carry NO answer-bearing keys — ``resolution`` and
``crowd`` (the frozen SOTA teacher forecast) are stripped from every row, so even a
misconfigured run with filesystem tools cannot Grep the outcome out of its own set file.
Ground truth goes to a per-arm answers sidecar (bench/sets/answers/<stem>-ev-<arm>.jsonl,
same stem as the arm's set file) which is what report.py scores against:

    python bench/report.py bench/sets/answers/<stem>-ev-<arm>.jsonl --tag ev-<arm>

(report.py reads resolutions from the set file it is GIVEN and locates the results file
by that file's stem + tag, so pointing it at the sidecar scores the arm's rows by qid;
the vs-crowd tables are absent by design — result rows carry no crowd either.)
"""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

# Parametrically clean for BTF-2's Oct-Dec 2025 resolutions (training data ~Jul 2025)
# and ~5x cheaper than Opus 4.6. Verify the cutoff again before swapping models.
DEFAULT_MODEL = "claude-sonnet-4-5"
AGENT_CMD = (
    f"claude -p --model {DEFAULT_MODEL} --output-format json --allowed-tools Read,Glob,Grep"
)
# Keys that exist only for offline scoring (fetch_btf2.build_spec): the outcome itself,
# and the teacher forecast that all but names it. Neither belongs in a file the run reads.
ANSWER_KEYS = ("resolution", "crowd")


def require_leakfree(cmd: str) -> str:
    """Structural guard: every emitted command must run the zero tier's leak-control
    mode. --blind never existed in run_bench's argparse (the old printed command could
    not run), and without --leakfree the set files were one Grep away from the answers —
    refuse to print anything that could regress to that."""
    if "--leakfree none" not in cmd or "--blind" in cmd:
        raise SystemExit("refusing to emit ablation command without '--leakfree none' "
                         f"(or carrying '--blind'): {cmd}")
    return cmd


def split_header(background: str) -> tuple[str, str]:
    """(as-of header, dossier body). The header must survive every arm — removing the
    as-of date would confound evidence quality with temporal grounding."""
    marker = "\n\n"
    head, _, body = background.partition(marker)
    return (head, body) if body else ("", background)


def transform(background: str, arm: str) -> str:
    head, body = split_header(background)
    if arm == "full":
        return background
    if arm == "half":
        kept = body[: len(body) // 2]
        return f"{head}\n\n{kept}\n[dossier truncated at 50% for the evidence ablation]"
    if arm == "stub":
        return f"{head}\n\n{body[:500]}\n[dossier truncated to 500 chars for the ablation]"
    if arm == "none":
        return f"{head}\n\n(No research dossier available for this run.)"
    raise ValueError(arm)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("set_file", help="a BTF-2 set from bench/fetch_btf2.py")
    parser.add_argument("--budget", type=float, default=8.0,
                        help="per-arm notional budget passed to run_bench")
    parser.add_argument("--limit", type=int, default=0, help="questions per arm (0=all)")
    args = parser.parse_args(argv)

    src = Path(args.set_file)
    specs = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    answers_dir = src.parent / "answers"
    answers_dir.mkdir(exist_ok=True)
    commands = []
    for arm in ("full", "half", "stub", "none"):
        out = src.with_name(f"{src.stem}-ev-{arm}.jsonl")
        rows = []
        answers = []
        for spec in specs:
            row = {k: v for k, v in spec.items() if k not in ANSWER_KEYS}
            row["id"] = f"{spec['id']}#ev-{arm}"
            row["background"] = transform(str(spec.get("background", "")), arm)
            rows.append(json.dumps(row, ensure_ascii=False))
            # sidecar: exactly what report.py needs to score this arm's qids — no more.
            answers.append(json.dumps({"id": row["id"],
                                       "resolution": spec.get("resolution")},
                                      ensure_ascii=False))
        out.write_text("\n".join(rows) + "\n", encoding="utf-8")
        answers_path = answers_dir / out.name
        answers_path.write_text("\n".join(answers) + "\n", encoding="utf-8")
        print(f"built {out} ({len(rows)} questions, arm={arm}; "
              f"answers -> {answers_path})")
        cmd = (
            f"python bench/run_bench.py {shlex.quote(str(out))} --tiers zero "
            f"--leakfree none --budget {args.budget}"
            + (f" --limit {args.limit}" if args.limit else "")
            + f" --tag ev-{arm} --agent-cmd {shlex.quote(AGENT_CMD)}"
        )
        commands.append(require_leakfree(cmd))
    print("\nRun the arms (resumable; rerun the same command after a session cap):\n")
    for cmd in commands:
        print(" ", cmd)
    print("\nThen score each arm against its answers sidecar (the ablation set files "
          "carry no resolution):\n  python bench/report.py "
          f"{answers_dir.as_posix()}/<stem>-ev-<arm>.jsonl --tag ev-<arm>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
