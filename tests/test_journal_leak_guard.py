"""Guards for the content-free staged-journal leak scanner."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import journal_leak_guard as guard  # noqa: E402


def record(**over: object) -> dict[str, object]:
    value: dict[str, object] = {
        "question": "Public question",
        "resolution_criterion": "Public contract",
        "source": {"platform": "manifold"},
        "reasoning": "model-authored analysis",
    }
    value.update(over)
    return value


def test_exact_currency_symbol_allowed_only_in_public_contract_fields() -> None:
    symbol = chr(0xA3)
    pattern = symbol
    public = record(
        question=f"Public {symbol} question",
        resolution_criterion=f"Public {symbol} contract",
    )
    findings, allowed = guard.scan_added_line(
        pattern, "journal.jsonl", 1, json.dumps(public, ensure_ascii=False)
    )

    assert findings == []
    assert allowed == 2

    private = record(reasoning=f"model wrote {symbol} here")
    findings, allowed = guard.scan_added_line(
        pattern, "journal.jsonl", 1, json.dumps(private, ensure_ascii=False)
    )
    assert findings == [guard.Finding("journal.jsonl", 1, "reasoning")]
    assert allowed == 0


def test_other_private_match_in_public_field_still_blocks() -> None:
    private_marker = "private" + "-marker-739"
    pattern = private_marker
    payload = record(question=f"Public question {private_marker}")

    findings, allowed = guard.scan_added_line(
        pattern, "journal.jsonl", 3, json.dumps(payload)
    )

    assert findings == [guard.Finding("journal.jsonl", 3, "question")]
    assert allowed == 0


def test_non_public_platform_gets_no_currency_exception() -> None:
    symbol = chr(0xA3)
    pattern = symbol
    payload = record(question=f"Question {symbol}", source={"platform": "other"})

    findings, allowed = guard.scan_added_line(
        pattern, "journal.jsonl", 1, json.dumps(payload, ensure_ascii=False)
    )

    assert findings == [guard.Finding("journal.jsonl", 1, "question")]
    assert allowed == 0


def test_patch_scan_reports_locations_without_content() -> None:
    private_marker = "private" + "-marker-739"
    pattern = private_marker
    payload = json.dumps(record(reasoning=private_marker))
    patch = f"diff --git a/journal b/journal\n+++ b/journal\n+{payload}\n"

    findings, additions, allowed = guard.scan_patch(pattern, patch)

    assert findings == (guard.Finding("journal", 1, "reasoning"),)
    assert additions == 1
    assert allowed == 0
    assert private_marker not in repr(findings)


def test_scanner_uses_gnu_ere_not_python_regex() -> None:
    # An unmatched close parenthesis is literal in GNU ERE but raises in Python re.
    findings, allowed = guard.scan_added_line("literal)", "journal.txt", 1, "literal)")
    assert findings == [guard.Finding("journal.txt", 1, "<raw>")]
    assert allowed == 0


def test_cli_reads_staged_diff_and_never_logs_matched_content(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "bot" / "journal" / "manifold.jsonl"
    target.parent.mkdir(parents=True)
    symbol = chr(0xA3)
    private_marker = "private" + "-marker-739"
    target.write_text(
        json.dumps(
            record(question=f"Public {symbol} question", reasoning=private_marker),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "--", str(target)], cwd=tmp_path, check=True)

    monkeypatch.setenv("LEAK_PATTERNS", symbol)
    assert guard.main(["--root", str(tmp_path), "bot/journal/manifold.jsonl"]) == 0
    allowed_output = capsys.readouterr()
    assert "1 public contract currency match" in allowed_output.out

    monkeypatch.setenv("LEAK_PATTERNS", private_marker)
    assert guard.main(["--root", str(tmp_path), "bot/journal/manifold.jsonl"]) == 1
    blocked_output = capsys.readouterr()
    assert "reasoning" in blocked_output.err
    assert private_marker not in blocked_output.out + blocked_output.err


def test_workflows_use_content_free_scanner_and_tournament_publish_safely() -> None:
    for name in ("bot.yml", "manifold.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "scripts/journal_leak_guard.py" in text
        assert "grep -niIE" not in text
    bot = (ROOT / ".github" / "workflows" / "bot.yml").read_text(encoding="utf-8")
    assert "--autostash" not in bot
    assert "- uses: actions/checkout@v4\n        with:\n          ref: main" in bot
