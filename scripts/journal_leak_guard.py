"""Content-free leak guard for newly staged machine-generated journal lines.

The private ``LEAK_PATTERNS`` regex remains authoritative and is never printed.  Three
narrow exceptions exist, all of them for text the public platform itself already published:

* an exact decoded pound-sign match anywhere in a valid public Metaculus or Manifold JSON
  record (a bad literal currency-symbol branch in the private pattern);
* [ADDED 2026-09-09] in a **Metaculus** record, a match whose text also occurs verbatim in
  the record's own ``question`` / ``resolution_criterion`` — the tournament assigns the
  question and Metaculus publishes it, so a deny-list branch that matches the question's
  own words (public financial vocabulary, say) cannot be a leak of the operator's data.
  Hyphens/underscores in the match are read as spaces so a source URL slug qualifies;
* [ADDED 2026-09-09] per-question trace files (``bot/journal/traces/<id>.json``) are
  pretty-printed multi-line JSON, so they are scanned as one decoded document rather than
  line by line — a trace line on its own is not a record and would always fail as ``<raw>``.
  Only a wholly new trace file qualifies; a modified one falls back to per-line scanning.

Model reasoning may therefore contain the pound sign or the question's own words, but any
different sensitive match on the same line still blocks publication.  Raw/non-record lines,
invalid patterns, zero-width matches, and every other match remain fail-closed.  The
optional ``--redact-model-output`` recovery mode may replace an entire newly-added
``reasoning``, ``reference_class`` or ``what_would_change_my_mind`` item — or, in a trace,
one model-authored string under ``calls.[i].{reasoning,dossier,reconciliation,
disagreements,named_scenarios,reference_class}`` — with a neutral marker.  It refuses to
alter public questions/contracts, sources, metadata, keys, or raw JSON; callers must
re-stage and run the strict default scan before publication.

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
from pathlib import Path, PurePosixPath
from typing import Any

PUBLIC_PLATFORMS = frozenset({"manifold", "metaculus"})
# Platforms whose question/contract text is authored and published by the platform itself
# (assigned to the bot, never chosen by it), so a match inside it is public by construction.
PLATFORM_PUBLISHED_TEXT = frozenset({"metaculus"})
PUBLIC_TEXT_FIELDS = ("question", "resolution_criterion")
PUBLIC_CURRENCY_SYMBOL = chr(0xA3)
MODEL_OUTPUT_REDACTION = "[redacted by publication privacy guard]"
# Model-authored free text. reference_class was added 2026-09-09 after a run's trace was
# blocked on a reference-class sentence made of public tournament vocabulary.
RECORD_MODEL_FIELDS = frozenset({"reasoning", "reference_class"})
TRACE_MODEL_FIELDS = frozenset(
    {"reasoning", "dossier", "reconciliation", "disagreements", "named_scenarios",
     "reference_class"}
)
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_CHANGE_MIND_ITEM = re.compile(r"^what_would_change_my_mind\.\[(\d+)\]$")
_TRACE_CALL_FIELD = re.compile(r"^calls\.\[\d+\]\.([a-z_]+)(?:\.|$)")
_FIELD_INDEX = re.compile(r"^\[(\d+)\]$")
_MATCH_SEPARATORS = re.compile(r"[-_\s]+")


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
    new_file: bool = False


@dataclass(frozen=True)
class TraceDocument:
    """A wholly new pretty-printed trace file, reassembled from its staged additions."""

    path: str
    first_ordinal: int
    line_count: int
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


def _normalize_match(text: str) -> str:
    """Case-fold and read hyphens/underscores as spaces so URL slugs compare to prose."""
    return _MATCH_SEPARATORS.sub(" ", text).strip().lower()


def _platform_published_text(payload: dict[str, Any], platform: str) -> str:
    """The record's platform-authored public text (normalized), or "" when none applies."""
    if platform not in PLATFORM_PUBLISHED_TEXT:
        return ""
    parts = [payload.get(name) for name in PUBLIC_TEXT_FIELDS]
    return " ".join(_normalize_match(p) for p in parts if isinstance(p, str) and p)


def scan_added_line(
    pattern: str, path: str, added_line: int, text: str
) -> tuple[list[Finding], int]:
    """Return blocked locations and the public-text exceptions granted in a JSON record."""
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
    published_text = _platform_published_text(payload, platform)
    for field_path, value in _iter_strings(payload):
        for match in _ere_matches(pattern, value):
            if raw_matches[match] > 0:
                raw_matches[match] -= 1
            public_record_currency = (
                public_record
                and PUBLIC_CURRENCY_SYMBOL in pattern
                and match == PUBLIC_CURRENCY_SYMBOL
            )
            published_verbatim = bool(
                match and published_text and _normalize_match(match) in published_text
            )
            if public_record_currency or published_verbatim:
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
    current_new_file = False
    next_line_number: int | None = None
    ordinal = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            next_line_number = None
            current_new_file = False
            continue
        if line.startswith("--- "):
            current_new_file = line[4:] == "/dev/null"
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
            yield AddedLine(
                current_path, ordinal, next_line_number, line[1:], current_new_file
            )
            next_line_number += 1
        elif line.startswith("-"):
            continue
        else:
            next_line_number += 1


def is_trace_path(path: str) -> bool:
    posix = PurePosixPath(path)
    return posix.suffix == ".json" and "traces" in posix.parts[:-1]


def split_additions(
    additions: Iterable[AddedLine],
) -> tuple[list[AddedLine], list[TraceDocument]]:
    """Separate per-line additions from wholly new trace files scanned as one document."""
    by_path: dict[str, list[AddedLine]] = {}
    order: list[str] = []
    for addition in additions:
        if addition.path not in by_path:
            order.append(addition.path)
        by_path.setdefault(addition.path, []).append(addition)
    per_line: list[AddedLine] = []
    documents: list[TraceDocument] = []
    for path in order:
        lines = by_path[path]
        whole_new_file = (
            is_trace_path(path)
            and all(line.new_file for line in lines)
            and [line.line_number for line in lines] == list(range(1, len(lines) + 1))
        )
        if whole_new_file:
            documents.append(
                TraceDocument(
                    path,
                    lines[0].ordinal,
                    len(lines),
                    "\n".join(line.text for line in lines),
                )
            )
        else:
            per_line.extend(lines)
    return per_line, documents


def scan_patch(pattern: str, patch: str) -> tuple[tuple[Finding, ...], int, int]:
    additions = 0
    allowed = 0
    findings: set[Finding] = set()
    per_line, documents = split_additions(_iter_patch_additions(patch))
    for addition in per_line:
        additions += 1
        new_findings, new_allowed = scan_added_line(
            pattern, addition.path, addition.ordinal, addition.text
        )
        findings.update(new_findings)
        allowed += new_allowed
    for document in documents:
        additions += document.line_count
        new_findings, new_allowed = scan_added_line(
            pattern, document.path, document.first_ordinal, document.text
        )
        findings.update(new_findings)
        allowed += new_allowed
    return tuple(sorted(findings)), additions, allowed


def _redact_model_fields(
    pattern: str, addition: AddedLine
) -> tuple[str, int] | None:
    """Redact only model-authored fields, or refuse when any protected field matched."""
    findings, _allowed = scan_added_line(
        pattern, addition.path, addition.ordinal, addition.text
    )
    if not findings:
        return addition.text, 0
    fields = {finding.field for finding in findings}
    if any(
        field not in RECORD_MODEL_FIELDS and not _CHANGE_MIND_ITEM.fullmatch(field)
        for field in fields
    ):
        return None
    try:
        payload = json.loads(addition.text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    for field in fields & RECORD_MODEL_FIELDS:
        payload[field] = MODEL_OUTPUT_REDACTION
    change_mind = payload.get("what_would_change_my_mind")
    for field in fields:
        item = _CHANGE_MIND_ITEM.fullmatch(field)
        if item:
            index = int(item.group(1))
            if not isinstance(change_mind, list) or index >= len(change_mind):
                return None
            change_mind[index] = MODEL_OUTPUT_REDACTION

    redacted = json.dumps(payload, ensure_ascii=False)
    remaining, _allowed = scan_added_line(
        pattern, addition.path, addition.ordinal, redacted
    )
    if remaining:
        # A very broad deny-list may also match the neutral marker or serialized JSON.
        return None
    return redacted, len(fields)


def _is_trace_model_field(field: str) -> bool:
    """A string leaf under one call's model-authored text; never a key, never metadata."""
    if field.endswith("<key>"):
        return False
    call_field = _TRACE_CALL_FIELD.match(field)
    return bool(call_field) and call_field.group(1) in TRACE_MODEL_FIELDS


def _redact_leaf(payload: Any, field: str) -> bool:
    """Replace the string at a dotted/indexed field label; False when it is not a string."""
    node = payload
    components = field.split(".")
    for component in components[:-1]:
        index = _FIELD_INDEX.fullmatch(component)
        if index and isinstance(node, list) and int(index.group(1)) < len(node):
            node = node[int(index.group(1))]
        elif not index and isinstance(node, dict) and component in node:
            node = node[component]
        else:
            return False
    leaf = components[-1]
    index = _FIELD_INDEX.fullmatch(leaf)
    if index and isinstance(node, list) and int(index.group(1)) < len(node):
        if not isinstance(node[int(index.group(1))], str):
            return False
        node[int(index.group(1))] = MODEL_OUTPUT_REDACTION
        return True
    if not index and isinstance(node, dict) and isinstance(node.get(leaf), str):
        node[leaf] = MODEL_OUTPUT_REDACTION
        return True
    return False


def _redact_trace_document(
    pattern: str, document: TraceDocument
) -> tuple[str, int] | None:
    """Redact model-authored strings in a whole trace, or refuse when anything else matched."""
    findings, _allowed = scan_added_line(
        pattern, document.path, document.first_ordinal, document.text
    )
    if not findings:
        return document.text, 0
    fields = {finding.field for finding in findings}
    if not all(_is_trace_model_field(field) for field in fields):
        return None
    try:
        payload = json.loads(document.text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    for field in fields:
        if not _redact_leaf(payload, field):
            return None
    # Same layout bot/run_bot.write_trace produces, so the redacted file stays a trace.
    redacted = json.dumps(payload, ensure_ascii=False, indent=1)
    remaining, _allowed = scan_added_line(
        pattern, document.path, document.first_ordinal, redacted
    )
    if remaining:
        return None
    return redacted, len(fields)


def _rewrite_lines(original: str, replacements: dict[int, tuple[str, str]]) -> str:
    newline = "\r\n" if "\r\n" in original else "\n"
    trailing_newline = original.endswith(("\r", "\n"))
    lines = original.splitlines()
    for line_number, (expected, replacement) in replacements.items():
        index = line_number - 1
        if index < 0 or index >= len(lines) or lines[index] != expected:
            raise GuardError("the working journal no longer matches the staged diff")
        lines[index] = replacement
    return newline.join(lines) + (newline if trailing_newline else "")


def _rewrite_document(original: str, expected: str, replacement: str) -> str:
    newline = "\r\n" if "\r\n" in original else "\n"
    trailing_newline = original.endswith(("\r", "\n"))
    if original.splitlines() != expected.split("\n"):
        raise GuardError("the working trace no longer matches the staged diff")
    return newline.join(replacement.split("\n")) + (newline if trailing_newline else "")


def redact_staged_model_output(pattern: str, patch: str, *, root: Path) -> int | None:
    """Rewrite eligible staged additions in the working tree; return None when unsafe."""
    line_replacements: dict[str, dict[int, tuple[str, str]]] = {}
    document_replacements: dict[str, tuple[str, str]] = {}
    redacted_fields = 0
    per_line, documents = split_additions(_iter_patch_additions(patch))
    for addition in per_line:
        result = _redact_model_fields(pattern, addition)
        if result is None:
            return None
        redacted, field_count = result
        if not field_count:
            continue
        line_replacements.setdefault(addition.path, {})[addition.line_number] = (
            addition.text,
            redacted,
        )
        redacted_fields += field_count
    for document in documents:
        result = _redact_trace_document(pattern, document)
        if result is None:
            return None
        redacted, field_count = result
        if not field_count:
            continue
        document_replacements[document.path] = (document.text, redacted)
        redacted_fields += field_count

    root = root.resolve()
    prepared: list[tuple[Path, str]] = []
    for relative in [*line_replacements, *document_replacements]:
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise GuardError("a staged journal path escapes the repository") from exc
        original = target.read_text(encoding="utf-8", errors="surrogateescape")
        if relative in line_replacements:
            rewritten = _rewrite_lines(original, line_replacements[relative])
        else:
            rewritten = _rewrite_document(original, *document_replacements[relative])
        prepared.append((target, rewritten))

    for target, rewritten in prepared:
        target.write_text(
            rewritten,
            encoding="utf-8",
            errors="surrogateescape",
            newline="",
        )
    return redacted_fields


def quarantine_staged_additions(
    pattern: str, patch: str, *, root: Path
) -> tuple[int, int]:
    """[ADDED 2026-09-09] Withhold still-blocked additions instead of blocking the publish.

    Removes each blocked jsonl line and each blocked wholly-new trace file from the working
    tree so that the clean remainder can be re-staged and published. The forecast itself was
    submitted long before this step runs; the deny-list must only ever cost the public
    journal a row, never the run. Callers snapshot the journal to the run artifact BEFORE
    calling this, so a withheld row is never lost. Returns (lines removed, files removed).
    Raises GuardError rather than corrupt a file it cannot safely edit (a blocked line
    inside a modified trace, a working file that no longer matches the staged diff).
    """
    per_line, documents = split_additions(_iter_patch_additions(patch))
    line_removals: dict[str, dict[int, str]] = {}
    file_removals: list[str] = []
    for addition in per_line:
        findings, _allowed = scan_added_line(
            pattern, addition.path, addition.ordinal, addition.text
        )
        if not findings:
            continue
        if is_trace_path(addition.path):
            raise GuardError("a blocked line inside a modified trace cannot be quarantined")
        line_removals.setdefault(addition.path, {})[addition.line_number] = addition.text
    for document in documents:
        findings, _allowed = scan_added_line(
            pattern, document.path, document.first_ordinal, document.text
        )
        if findings:
            file_removals.append(document.path)

    root = root.resolve()
    prepared: list[tuple[Path, str | None]] = []
    for relative in [*line_removals, *file_removals]:
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise GuardError("a staged journal path escapes the repository") from exc
        if relative in file_removals:
            prepared.append((target, None))
            continue
        original = target.read_text(encoding="utf-8", errors="surrogateescape")
        newline = "\r\n" if "\r\n" in original else "\n"
        trailing_newline = original.endswith(("\r", "\n"))
        lines = original.splitlines()
        for line_number, expected in line_removals[relative].items():
            index = line_number - 1
            if index < 0 or index >= len(lines) or lines[index] != expected:
                raise GuardError("the working journal no longer matches the staged diff")
        drop = {number - 1 for number in line_removals[relative]}
        kept = [line for index, line in enumerate(lines) if index not in drop]
        rewritten = newline.join(kept) + (newline if trailing_newline and kept else "")
        prepared.append((target, rewritten))

    for target, rewritten in prepared:
        if rewritten is None:
            target.unlink()
        else:
            target.write_text(rewritten, encoding="utf-8", errors="surrogateescape", newline="")
    removed_lines = sum(len(v) for v in line_removals.values())
    return removed_lines, len(file_removals)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="staged journal paths to scan")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--redact-model-output",
        action="store_true",
        help=(
            "replace matches only in added reasoning/change-my-mind fields (and, in trace "
            "files, model-authored call text); protected fields still fail closed"
        ),
    )
    parser.add_argument(
        "--quarantine",
        action="store_true",
        help=(
            "withhold still-blocked added lines / new trace files from the working tree "
            "(snapshot the journal to an artifact first) instead of failing; exit 0"
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
                    f"redacted {redacted} model-output field(s); "
                    "re-stage and run the guard again"
                )
                return 0
        if findings and args.quarantine:
            lines, files = quarantine_staged_additions(
                private_pattern, patch, root=args.root
            )
            print(
                f"quarantined {lines} added line(s) and {files} new trace file(s); "
                "withheld from publication, kept in the run artifact — re-stage and run "
                "the guard again"
            )
            print(
                f"::warning::journal leak guard withheld {lines} line(s) and {files} "
                "trace file(s) from the public journal (see the run artifact)"
            )
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a", encoding="utf-8") as handle:
                    handle.write("quarantined=true\n")
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
        f"{allowed} public-record match(es) allowed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
