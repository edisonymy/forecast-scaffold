"""Content-free leak guard for newly staged machine-generated journal lines.

The private ``LEAK_PATTERNS`` regex remains authoritative and is never printed.  The only
exception is an exact decoded pound-sign match anywhere in a valid public Metaculus or
Manifold JSON record.  This narrowly works around a bad literal currency-symbol branch in
the private pattern without weakening other matches: model reasoning may contain the
pound sign, but any different sensitive match on the same line still blocks publication.
Raw/non-record lines, invalid patterns, zero-width matches, and every other match remain
fail-closed.  The optional ``--redact-model-output`` recovery mode may replace an entire
newly-added free-text field with a neutral marker, but only the fields named in
``MODEL_REDACTABLE_FIELDS`` / ``CONTRACT_REDACTABLE_FIELDS``: the model-authored prose
(``reasoning``, ``reference_class``, ``what_would_change_my_mind`` items,
``research.sources`` items) and — on a public-platform record that journals its
``source.url`` — the platform's own ``resolution_criterion``, which stays recoverable from
that URL.  It refuses to alter the ``question`` (the record's identity), metadata, keys,
numbers, or raw JSON; callers must re-stage and run the strict default scan before
publication.

The script reads additions directly from ``git diff --cached`` so historical public lines
cannot lock future publication and matched content never enters workflow logs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
MODEL_OUTPUT_REDACTION = "[redacted by publication privacy guard]"
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_LIST_INDEX = re.compile(r"^\[(\d+)\]$")

# Free-text fields the recovery mode may blank, as field labels with list indices collapsed
# to "[]".  Two classes, and the distinction is the whole safety argument:
#
#   * model-authored prose — the agent wrote it, so a deny-list hit there is exactly what the
#     guard exists to keep out of a public file, and nothing outside this repo can restore it
#     anyway;
#   * `resolution_criterion` — NOT model-authored: on a Manifold/Metaculus record it is a
#     verbatim copy of the platform's own public contract text (`criteria_text(market)`), so
#     blanking it loses no evidence a reader cannot fetch from the journaled `source.url`.
#     Hence CONTRACT_REDACTABLE_FIELDS is gated on a public record that carries that URL.
#
# `question` is deliberately absent: it is the record's identity and the key a reader joins on,
# and a deny-list hit in a market's own title is worth an operator's eyes rather than a silent
# blank.  Keys, metadata, numbers and non-record lines stay fail-closed as before.
MODEL_REDACTABLE_FIELDS = frozenset({
    "reasoning",
    "reference_class",
    "what_would_change_my_mind.[]",
    "research.sources.[]",
})
CONTRACT_REDACTABLE_FIELDS = frozenset({"resolution_criterion"})


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    added_line: int
    field: str


@dataclass(frozen=True)
class AddedLine:
    path: str
    ordinal: int
    line_number: int
    text: str


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


def _iter_patch_additions(patch: str) -> Iterable[AddedLine]:
    """Yield staged additions with both safe display ordinals and working-tree line numbers."""
    current_path = "<unknown>"
    next_line_number: int | None = None
    ordinal = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            next_line_number = None
            continue
        if line.startswith("+++ "):
            current_path = line[4:].removeprefix("b/")
            continue
        hunk = _HUNK_HEADER.match(line)
        if hunk:
            next_line_number = int(hunk.group(1))
            continue
        if next_line_number is None or line.startswith("\\ No newline at end of file"):
            continue
        if line.startswith("+"):
            ordinal += 1
            yield AddedLine(current_path, ordinal, next_line_number, line[1:])
            next_line_number += 1
        elif line.startswith("-"):
            continue
        else:
            next_line_number += 1


def scan_patch(pattern: str, patch: str) -> tuple[tuple[Finding, ...], int, int]:
    additions = 0
    allowed = 0
    findings: set[Finding] = set()
    for addition in _iter_patch_additions(patch):
        additions += 1
        new_findings, new_allowed = scan_added_line(
            pattern, addition.path, addition.ordinal, addition.text
        )
        findings.update(new_findings)
        allowed += new_allowed
    return tuple(sorted(findings)), additions, allowed


def _field_shape(field: str) -> str:
    """A field label with list indices collapsed: ``research.sources.[5]`` ->
    ``research.sources.[]``."""
    return ".".join(
        "[]" if _LIST_INDEX.fullmatch(part) else part for part in field.split(".")
    )


def _redactable_fields(payload: dict[str, Any]) -> frozenset[str]:
    """The field shapes this record may have blanked, given where its text came from."""
    allowed = set(MODEL_REDACTABLE_FIELDS)
    source = payload.get("source")
    if isinstance(source, dict):
        platform = str(source.get("platform", "")).lower()
        # The contract text is only recoverable while the record says where to fetch it.
        if platform in PUBLIC_PLATFORMS and str(source.get("url", "")).strip():
            allowed |= CONTRACT_REDACTABLE_FIELDS
    return frozenset(allowed)


def _set_field(payload: Any, field: str, value: str) -> bool:
    """Replace the string at a decoded field label; False when the path no longer resolves.

    A label is built from decoded JSON, so a key containing a literal "." can in principle
    produce one that no longer round-trips. That resolves to False here — refusing to redact
    — rather than to a guess about which key was meant."""
    parts = field.split(".")
    target = payload
    for part in parts[:-1]:
        index = _LIST_INDEX.fullmatch(part)
        if index:
            position = int(index.group(1))
            if not isinstance(target, list) or position >= len(target):
                return False
            target = target[position]
        else:
            if not isinstance(target, dict) or part not in target:
                return False
            target = target[part]
    last = _LIST_INDEX.fullmatch(parts[-1])
    if last:
        position = int(last.group(1))
        if not isinstance(target, list) or position >= len(target):
            return False
        if not isinstance(target[position], str):
            return False
        target[position] = value
        return True
    if not isinstance(target, dict) or not isinstance(target.get(parts[-1]), str):
        return False
    target[parts[-1]] = value
    return True


def _redact_model_fields(
    pattern: str, addition: AddedLine
) -> tuple[str, tuple[str, ...]] | None:
    """Redact only the eligible free-text fields, or refuse when any protected field matched."""
    findings, _allowed = scan_added_line(
        pattern, addition.path, addition.ordinal, addition.text
    )
    if not findings:
        return addition.text, ()
    try:
        payload = json.loads(addition.text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    redactable = _redactable_fields(payload)
    fields = sorted({finding.field for finding in findings})
    if any(_field_shape(field) not in redactable for field in fields):
        return None
    for field in fields:
        if not _set_field(payload, field, MODEL_OUTPUT_REDACTION):
            return None

    redacted = json.dumps(payload, ensure_ascii=False)
    remaining, _allowed = scan_added_line(
        pattern, addition.path, addition.ordinal, redacted
    )
    if remaining:
        # A very broad deny-list may also match the neutral marker or serialized JSON.
        return None
    return redacted, tuple(fields)


def redact_staged_model_output(
    pattern: str, patch: str, *, root: Path
) -> tuple[Finding, ...] | None:
    """Rewrite eligible staged additions in the working tree; return None when unsafe.

    Returns the redacted locations (path/line/field only — never content) so the operator can
    see WHICH fields a run blanked without the private deny-list or the matched text reaching
    a workflow log."""
    replacements: dict[str, dict[int, tuple[str, str]]] = {}
    redacted_fields: list[Finding] = []
    for addition in _iter_patch_additions(patch):
        result = _redact_model_fields(pattern, addition)
        if result is None:
            return None
        redacted, fields = result
        if not fields:
            continue
        replacements.setdefault(addition.path, {})[addition.line_number] = (
            addition.text,
            redacted,
        )
        redacted_fields.extend(
            Finding(addition.path, addition.ordinal, field) for field in fields
        )

    root = root.resolve()
    prepared: list[tuple[Path, str]] = []
    for relative, line_replacements in replacements.items():
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise GuardError("a staged journal path escapes the repository") from exc
        original = target.read_text(encoding="utf-8", errors="surrogateescape")
        newline = "\r\n" if "\r\n" in original else "\n"
        trailing_newline = original.endswith(("\r", "\n"))
        lines = original.splitlines()
        for line_number, (expected, replacement) in line_replacements.items():
            index = line_number - 1
            if index < 0 or index >= len(lines) or lines[index] != expected:
                raise GuardError("the working journal no longer matches the staged diff")
            lines[index] = replacement
        rewritten = newline.join(lines) + (newline if trailing_newline else "")
        prepared.append((target, rewritten))

    for target, rewritten in prepared:
        target.write_text(
            rewritten,
            encoding="utf-8",
            errors="surrogateescape",
            newline="",
        )
    return tuple(redacted_fields)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="staged journal paths to scan")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--redact-model-output",
        action="store_true",
        help=(
            "replace matches only in the added free-text fields the allow-list "
            "covers; the question, keys, metadata and raw lines still fail closed"
        ),
    )
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
        if findings and args.redact_model_output:
            redacted = redact_staged_model_output(
                private_pattern, patch, root=args.root
            )
            if redacted is not None:
                print(
                    f"redacted {len(redacted)} model-output field(s); "
                    "re-stage and run the guard again"
                )
                for location in redacted:
                    print(
                        f"  {location.path}:added-line-{location.added_line}:"
                        f"{location.field}"
                    )
                return 0
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
