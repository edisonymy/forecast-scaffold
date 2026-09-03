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


def test_exact_currency_symbol_allowed_anywhere_in_public_record() -> None:
    symbol = chr(0xA3)
    pattern = symbol
    public = record(
        question=f"Public {symbol} question",
        resolution_criterion=f"Public {symbol} contract",
        reasoning=f"model compares prices in {symbol}",
        nested={"notes": [f"another {symbol} amount"]},
    )
    findings, allowed = guard.scan_added_line(
        pattern, "journal.jsonl", 1, json.dumps(public, ensure_ascii=False)
    )

    assert findings == []
    assert allowed == 4

    private = record(reasoning=f"model wrote {symbol} here", source={"platform": "other"})
    findings, allowed = guard.scan_added_line(
        pattern, "journal.jsonl", 1, json.dumps(private, ensure_ascii=False)
    )
    assert findings == [guard.Finding("journal.jsonl", 1, "reasoning")]
    assert allowed == 0


def test_encoded_currency_symbol_is_allowed_after_json_decode() -> None:
    symbol = chr(0xA3)
    payload = record(reasoning=f"model wrote {symbol} here")

    findings, allowed = guard.scan_added_line(
        symbol, "journal.jsonl", 1, json.dumps(payload, ensure_ascii=True)
    )

    assert findings == []
    assert allowed == 1


def test_currency_plus_sensitive_match_still_blocks_sensitive_field() -> None:
    symbol = chr(0xA3)
    private_marker = "private" + "-marker-739"
    payload = record(reasoning=f"price {symbol}; secret {private_marker}")

    findings, allowed = guard.scan_added_line(
        f"{symbol}|{private_marker}",
        "journal.jsonl",
        2,
        json.dumps(payload, ensure_ascii=False),
    )

    assert findings == [guard.Finding("journal.jsonl", 2, "reasoning")]
    assert allowed == 1


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
    patch = (
        "diff --git a/journal b/journal\n"
        "+++ b/journal\n"
        "@@ -0,0 +1 @@\n"
        f"+{payload}\n"
    )

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
    assert "1 public-record currency match" in allowed_output.out

    monkeypatch.setenv("LEAK_PATTERNS", private_marker)
    assert guard.main(["--root", str(tmp_path), "bot/journal/manifold.jsonl"]) == 1
    blocked_output = capsys.readouterr()
    assert "reasoning" in blocked_output.err
    assert private_marker not in blocked_output.out + blocked_output.err


def test_cli_can_redact_only_matching_model_output_fields(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "bot" / "journal" / "manifold.jsonl"
    target.parent.mkdir(parents=True)
    private_marker = "private" + "-marker-739"
    historical = json.dumps(record(reasoning="historical public analysis")) + "\n"
    target.write_text(historical, encoding="utf-8")
    subprocess.run(["git", "add", "--", str(target)], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "historical journal",
        ],
        cwd=tmp_path,
        check=True,
    )
    target.write_text(
        historical
        +
        json.dumps(
            record(
                reasoning=f"analysis includes {private_marker}",
                what_would_change_my_mind=[
                    "a public update",
                    f"private detail {private_marker}",
                ],
            ),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "--", str(target)], cwd=tmp_path, check=True)
    monkeypatch.setenv("LEAK_PATTERNS", private_marker)

    assert (
        guard.main(
            [
                "--root",
                str(tmp_path),
                "--redact-model-output",
                "bot/journal/manifold.jsonl",
            ]
        )
        == 0
    )
    redaction_output = capsys.readouterr()
    assert "redacted 2 model-output field(s)" in redaction_output.out
    assert private_marker not in redaction_output.out + redaction_output.err

    lines = target.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["reasoning"] == "historical public analysis"
    payload = json.loads(lines[1])
    assert payload["reasoning"] == guard.MODEL_OUTPUT_REDACTION
    assert payload["what_would_change_my_mind"] == [
        "a public update",
        guard.MODEL_OUTPUT_REDACTION,
    ]
    assert private_marker not in target.read_text(encoding="utf-8")

    subprocess.run(["git", "add", "--", str(target)], cwd=tmp_path, check=True)
    assert guard.main(["--root", str(tmp_path), "bot/journal/manifold.jsonl"]) == 0


def test_model_output_redaction_refuses_protected_fields(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "bot" / "journal" / "manifold.jsonl"
    target.parent.mkdir(parents=True)
    private_marker = "private" + "-marker-739"
    original = (
        json.dumps(
            record(
                question=f"Public question {private_marker}",
                reasoning=f"analysis includes {private_marker}",
            )
        )
        + "\n"
    )
    target.write_text(original, encoding="utf-8")
    subprocess.run(["git", "add", "--", str(target)], cwd=tmp_path, check=True)
    monkeypatch.setenv("LEAK_PATTERNS", private_marker)

    assert (
        guard.main(
            [
                "--root",
                str(tmp_path),
                "--redact-model-output",
                "bot/journal/manifold.jsonl",
            ]
        )
        == 1
    )
    blocked_output = capsys.readouterr()
    assert "question" in blocked_output.err
    assert private_marker not in blocked_output.out + blocked_output.err
    assert target.read_text(encoding="utf-8") == original


def test_raw_currency_and_zero_width_pattern_fail_closed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "journal.txt"
    target.write_text(f"raw {chr(0xA3)} text\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", str(target)], cwd=tmp_path, check=True)

    monkeypatch.setenv("LEAK_PATTERNS", chr(0xA3))
    assert guard.main(["--root", str(tmp_path), "journal.txt"]) == 1
    assert "<raw>" in capsys.readouterr().err

    monkeypatch.setenv("LEAK_PATTERNS", "a*")
    assert guard.main(["--root", str(tmp_path), "journal.txt"]) == 2
    assert "could not complete safely" in capsys.readouterr().err


def test_workflows_use_content_free_scanner_and_tournament_publish_safely() -> None:
    for name in ("bot.yml", "manifold.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "scripts/journal_leak_guard.py" in text
        assert "grep -niIE" not in text
    bot = (ROOT / ".github" / "workflows" / "bot.yml").read_text(encoding="utf-8")
    manifold = (ROOT / ".github" / "workflows" / "manifold.yml").read_text(
        encoding="utf-8"
    )
    assert "--autostash" not in bot
    assert "- uses: actions/checkout@v4\n        with:\n          ref: main" in bot
    assert "schedule:" not in bot
    assert "workflow_dispatch:" in bot
    assert "forecast-bot-kicker" in bot
    assert manifold.count("scripts/journal_leak_guard.py") == 2
    assert "--redact-model-output" in manifold


def test_bot_commit_step_scans_every_secret_it_is_handed() -> None:
    """FIX G (2026-09-03 review): the Commit journal step runs no bot, so any secret in its
    env exists only to be searched for in the journal it is about to publish. ASKNEWS_API_KEY
    was exposed there and NOT scanned — the one combination with cost and no benefit."""
    bot = (ROOT / ".github" / "workflows" / "bot.yml").read_text(encoding="utf-8")
    step = bot.split("- name: Commit journal (pre-registration)")[1].split("- name:")[0]
    exposed = {
        line.split(":")[0].strip()
        for line in step.split("run: |")[0].splitlines()
        if ": ${{ secrets." in line
    }
    scan = next(line for line in step.splitlines() if line.strip().startswith("for secret in"))
    scanned = {name.strip('";$ ') for name in scan.split()[3:] if name.startswith('"$')}
    # LEAK_PATTERNS is the deny-list itself (a pattern file, not a credential value).
    assert exposed - {"LEAK_PATTERNS"} == scanned
    assert "ASKNEWS_API_KEY" in scanned
