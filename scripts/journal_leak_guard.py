"""Content-free leak guard for newly staged machine-generated journal lines.

The private ``LEAK_PATTERNS`` regex remains authoritative and is never printed.  The only
exception is an exact decoded pound-sign match anywhere in a valid public Metaculus or
Manifold JSON record.  This narrowly works around a bad literal currency-symbol branch in
the private pattern without weakening other matches: model reasoning may contain the
pound sign, but any different sensitive match on the same line still blocks publication.
Raw/non-record
lines, invalid patterns, zero-width matches, and every other match remain fail-closed.

The script reads additions directly from ``git diff --cached`` so historical public lines
cannot lock future publication and matched content never enters workflow logs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PUBLIC_PLATFORMS = frozenset({"manifold", "metaculus"})
PUBLIC_CURRENCY_SYMBOL = chr(0xA3)


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    added_line: int
    field: str


class GuardError(RuntimeError):
    """The staged additions could not be scanned safely."""


def _iter_strings(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            yield (*path, "<key>"), key_text
            yield from _iter_strings(child, (*path, key_text))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_strings(child, (*path, f"[{index}]"))
    elif isinstance(value, str):
        yield path, value


def _field_label(path: tuple[str, ...]) -> str:
    return ".".join(path) if path else "<raw>"


@lru_cache(maxsize=1)
def _grep_executable() -> str:
    """Find GNU grep on Linux runners and Git-for-Windows development machines."""
    direct = shutil.which("grep")
    if direct:
        return direct
    git = shutil.which("git")
    if git:
        for parent in Path(git).resolve().parents:
            for relative in (Path("usr/bin/grep.exe"), Path("usr/bin/grep")):
                candidate = parent / relative
                if candidate.is_file():
                    return str(candidate)
    raise GuardError("GNU grep is unavailable")


def _ere_matches(pattern: str, value: str) -> tuple[str, ...]:
    """Return GNU-ERE matches without writing the private pattern/content to logs."""
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C.UTF-8")
    result = subprocess.run(
        [
            _grep_executable(),
            "--only-matching",
            "--extended-regexp",
            "--ignore-case",
            "--",
            pattern,
        ],
        input=value,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        env=env,
        check=False,
    )
    if result.returncode == 1:
        return ()
    if result.returncode != 0:
        raise GuardError("the private GNU ERE could not be evaluated")
    matches = tuple(result.stdout.splitlines())
    # GNU grep reports success for a zero-width match but -o emits no text. Such a pattern
    # still matched, so return a sentinel that can never receive the public-field exception.
    return matches or ("",)


def scan_added_line(
    pattern: str, path: str, added_line: int, text: str
) -> tuple[list[Finding], int]:
    """Return blocked locations and exact-pound exceptions in a public JSON record."""
    raw_matches = Counter(_ere_matches(pattern, text))
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None

    findings: list[Finding] = []
    allowed = 0
    if not isinstance(payload, dict):
        for _match in raw_matches.elements():
            findings.append(Finding(path, added_line, "<raw>"))
        return findings, allowed

    source = payload.get("source")
    platform = str(source.get("platform", "")).lower() if isinstance(source, dict) else ""
    public_record = platform in PUBLIC_PLATFORMS
    for field_path, value in _iter_strings(payload):
        for match in _ere_matches(pattern, value):
            if raw_matches[match] > 0:
                raw_matches[match] -= 1
            public_record_currency = (
                public_record
                and PUBLIC_CURRENCY_SYMBOL in pattern
                and match == PUBLIC_CURRENCY_SYMBOL
            )
            if public_record_currency:
                allowed += 1
            else:
                findings.append(Finding(path, added_line, _field_label(field_path)))
    if any(raw_matches.values()):
        # The ERE matched serialized JSON syntax/escaping rather than a decoded string field.
        # It is not eligible for the public-contract exception.
        findings.append(Finding(path, added_line, "<raw>"))
    return findings, allowed


def staged_diff(paths: Sequence[str], *, root: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color", "--", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    if result.returncode != 0:
        raise GuardError("git could not produce the staged journal diff")
    return result.stdout


def scan_patch(pattern: str, patch: str) -> tuple[tuple[Finding, ...], int, int]:
    current_path = "<unknown>"
    additions = 0
    allowed = 0
    findings: set[Finding] = set()
    for line in patch.splitlines():
        if line.startswith("+++ "):
            current_path = line[4:].removeprefix("b/")
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        additions += 1
        new_findings, new_allowed = scan_added_line(
            pattern, current_path, additions, line[1:]
        )
        findings.update(new_findings)
        allowed += new_allowed
    return tuple(sorted(findings)), additions, allowed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="staged journal paths to scan")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    private_pattern = os.environ.get("LEAK_PATTERNS", "")
    if not private_pattern:
        print("journal leak guard configuration missing", file=sys.stderr)
        return 2
    try:
        # Compile/probe the exact GNU ERE even when this run has no staged additions. A
        # zero-width match is unsafe because GNU grep -o emits no content for it; reject it
        # rather than silently treating an unobservable match as clean.
        if (
            "" in _ere_matches(private_pattern, "")
            or "" in _ere_matches(private_pattern, "!")
        ):
            raise GuardError("the private GNU ERE has a zero-width match")
        patch = staged_diff(args.paths, root=args.root.resolve())
        findings, additions, allowed = scan_patch(private_pattern, patch)
    except (GuardError, OSError):
        print("journal leak guard could not complete safely", file=sys.stderr)
        return 2

    if findings:
        print(
            f"journal leak guard blocked {len(findings)} location(s); content suppressed",
            file=sys.stderr,
        )
        for finding in findings:
            print(
                f"  {finding.path}:added-line-{finding.added_line}:{finding.field}",
                file=sys.stderr,
            )
        return 1

    print(
        f"clean ({additions} staged added line(s); "
        f"{allowed} public-record currency match(es) allowed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
