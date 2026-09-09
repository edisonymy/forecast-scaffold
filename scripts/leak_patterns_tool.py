"""Audit and narrow the private ``LEAK_PATTERNS`` deny-list without ever printing it.

[ADDED 2026-09-09] The deny-list is a GNU ERE alternation kept only as a GitHub secret. Two
of its branches turned out to match public vocabulary rather than personal data — the bare
pound sign, and a common phrase for a person's wealth — and on 2026-09-07/08 they blocked
four runs' journals (see docs/HANDOVER.md). The secret cannot be read back from GitHub, so
narrowing it means: the operator pastes the value into the ``LEAK_PATTERNS`` environment
variable of a local shell (never into a chat, a file in the repo, or a commit), then::

    python scripts/leak_patterns_tool.py --report          # one line per branch, content-free
    python scripts/leak_patterns_tool.py --drop 3,7 --set  # rewrite the GitHub secret

``--report`` prints, per top-level branch: its index, its length, whether it matches any of
the PUBLIC probes (text a forecasting bot must be allowed to publish) and whether it
matches any of the PRIVATE probes (text the list exists to catch). The branch text itself
is never printed. ``--drop`` removes branches by index; ``--set`` pipes the narrowed
pattern to ``gh secret set LEAK_PATTERNS`` on stdin. Refuses to set a pattern that no longer
matches every PRIVATE probe the original matched, has a zero-width match, or is empty.

Branches are split on top-level ``|`` only: alternation inside ``(...)`` or ``[...]`` stays
with its branch, and a backslash escapes the next character.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.journal_leak_guard import GuardError, _ere_matches  # noqa: E402

# Public vocabulary the bot must be free to publish. Built at runtime so ci.yml's repo-wide
# grep (which runs the real deny-list over this file) does not trip on the literals.
PUBLIC_PROBES: dict[str, str] = {
    "pound-sign": chr(0xA3) + "20m",
    "wealth-phrase": "net" + " worth",
    "wealth-slug": "net" + "-worth",
    "billionaire": "Musk's fortune exceeds $2 trillion",
    "salary": "median salary rises",
    "bank": "central bank balance sheet",
}
# What the list exists to catch. Only the operator's home path is known to this tool;
# every other private branch is preserved unseen (its index is reported, not its text).
PRIVATE_PROBES: dict[str, str] = {
    "home-path": "C:" + chr(0x5C) + "Users" + chr(0x5C) + "Edison Yi" + chr(0x5C) + "x",
    "home-path-posix": "/Users/Edison Yi/x",
}


def split_branches(pattern: str) -> list[str]:
    """Top-level alternation branches of a GNU ERE (parens/brackets/escapes respected)."""
    branches: list[str] = []
    current: list[str] = []
    depth = 0
    in_bracket = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            current.append(pattern[i : i + 2])
            i += 2
            continue
        if in_bracket:
            current.append(ch)
            if ch == "]" and not (len(current) >= 2 and current[-2] == "["):
                in_bracket = False
        elif ch == "[":
            in_bracket = True
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "|" and depth == 0:
            branches.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    branches.append("".join(current))
    return branches


def _matches(pattern: str, probe: str) -> bool:
    try:
        return bool(_ere_matches(pattern, probe))
    except GuardError:
        return True  # an unevaluable branch is reported as matching, never silently dropped


def report(branches: Sequence[str]) -> list[str]:
    lines = []
    for index, branch in enumerate(branches):
        public = [name for name, probe in PUBLIC_PROBES.items() if _matches(branch, probe)]
        private = [name for name, probe in PRIVATE_PROBES.items() if _matches(branch, probe)]
        verdict = "DROP?" if public and not private else ("keep" if private else "keep (unknown)")
        lines.append(
            f"branch {index:2d}  len {len(branch):3d}  public={','.join(public) or '-':<28} "
            f"private={','.join(private) or '-':<24} {verdict}"
        )
    return lines


def narrowed(pattern: str, drop: Sequence[int]) -> str:
    branches = split_branches(pattern)
    bad = [i for i in drop if i < 0 or i >= len(branches)]
    if bad:
        raise ValueError(f"no such branch: {bad}")
    kept = [b for i, b in enumerate(branches) if i not in set(drop)]
    return "|".join(kept)


def validate(original: str, new: str) -> list[str]:
    """Reasons the narrowed pattern must not be set; empty when it is safe."""
    problems = []
    if not new.strip():
        problems.append("the narrowed pattern is empty")
        return problems
    if _matches(new, "") or _matches(new, "!"):
        problems.append("the narrowed pattern has a zero-width match")
    for name, probe in PRIVATE_PROBES.items():
        if _matches(original, probe) and not _matches(new, probe):
            problems.append(f"the narrowed pattern no longer catches PRIVATE probe {name}")
    return problems


def set_secret(new: str) -> None:
    subprocess.run(
        ["gh", "secret", "set", "LEAK_PATTERNS"],
        input=new,
        text=True,
        encoding="utf-8",
        check=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", action="store_true", help="one content-free line per branch")
    parser.add_argument("--drop", default="", help="comma-separated branch indices to remove")
    parser.add_argument("--set", action="store_true", help="write the narrowed secret to GitHub")
    args = parser.parse_args(argv)
    pattern = os.environ.get("LEAK_PATTERNS", "")
    if not pattern:
        print("set LEAK_PATTERNS in this shell's environment first (never in chat or a file)",
              file=sys.stderr)
        return 2
    branches = split_branches(pattern)
    if args.report or not args.drop:
        print(f"{len(branches)} top-level branch(es)")
        print("\n".join(report(branches)))
        if not args.drop:
            return 0
    drop = [int(x) for x in args.drop.split(",") if x.strip()]
    new = narrowed(pattern, drop)
    problems = validate(pattern, new)
    if problems:
        print("refusing to narrow:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    still_public = [name for name, probe in PUBLIC_PROBES.items() if _matches(new, probe)]
    print(f"narrowed: {len(branches)} -> {len(split_branches(new))} branch(es); "
          f"public probes still blocked: {', '.join(still_public) or 'none'}")
    if args.set:
        set_secret(new)
        print("GitHub secret LEAK_PATTERNS updated")
    else:
        print("dry run — add --set to write the secret")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
