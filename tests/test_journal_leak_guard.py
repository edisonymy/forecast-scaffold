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
    assert "1 public-record match" in allowed_output.out

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
    # [ADDED 2026-09-09] the tournament bot publishes trace files too; its commit step must
    # run the same redact-then-strict recovery flow, or a pound sign in a dossier blocks the
    # whole run's journal (run 34091868152, 2026-09-07).
    assert bot.count("scripts/journal_leak_guard.py") == 2
    assert "--redact-model-output" in bot


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


# --------------------------------------------------------------- [ADDED 2026-09-09] traces


def trace(**over: object) -> dict[str, object]:
    """A tournament trace document as bot/run_bot.write_trace lays it out."""
    value: dict[str, object] = {
        "record_id": "2026-09-07-abcd1234",
        "question": "What will the 54-hole leader's score to par be?",
        "url": "https://www.metaculus.com/questions/1/",
        "source": {"platform": "metaculus"},
        "calls": [
            {
                "run_index": 0,
                "reasoning": "run one reasoning",
                "dossier": "run one dossier",
                "sources": ["https://example.org/a"],
                "reference_class": "leader score",
            },
            {
                "run_index": 1,
                "reasoning": "supervisor reasoning",
                "reconciliation": "what settled it",
                "disagreements": [{"claim": "claim text", "kind": "factual"}],
                "sources": ["https://example.org/b"],
            },
        ],
    }
    value.update(over)
    return value


def new_file_patch(path: str, text: str) -> str:
    lines = text.split("\n")
    return (
        f"diff --git a/{path} b/{path}\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        + "".join(f"+{line}\n" for line in lines)
        + "\\ No newline at end of file\n"
    )


def test_trace_file_is_scanned_as_one_document_with_the_currency_exception() -> None:
    symbol = chr(0xA3)
    doc = trace()
    doc["calls"][0]["dossier"] = f"a {symbol}20m redevelopment"
    doc["calls"][1]["disagreements"][0]["claim"] = f"the {symbol}20m plan"
    text = json.dumps(doc, ensure_ascii=False, indent=1)
    patch = new_file_patch("bot/journal/traces/2026-09-07-abcd1234.json", text)

    findings, additions, allowed = guard.scan_patch(symbol, patch)

    assert findings == ()
    assert additions == text.count("\n") + 1
    assert allowed == 2


def test_trace_lines_still_fail_closed_when_the_file_is_not_wholly_new() -> None:
    symbol = chr(0xA3)
    patch = (
        "diff --git a/bot/journal/traces/x.json b/bot/journal/traces/x.json\n"
        "--- a/bot/journal/traces/x.json\n"
        "+++ b/bot/journal/traces/x.json\n"
        "@@ -3,0 +4 @@\n"
        f'+ "dossier": "a {symbol}20m plan",\n'
    )
    findings, _additions, allowed = guard.scan_patch(symbol, patch)
    assert findings == (guard.Finding("bot/journal/traces/x.json", 1, "<raw>"),)
    assert allowed == 0


def test_trace_private_match_reports_the_field_path_and_blocks() -> None:
    private_marker = "private" + "-marker-739"
    doc = trace()
    doc["calls"][1]["reconciliation"] = f"settled by {private_marker}"
    doc["calls"][0]["sources"] = [f"https://example.org/{private_marker}"]
    patch = new_file_patch(
        "bot/journal/traces/t.json", json.dumps(doc, ensure_ascii=False, indent=1)
    )
    findings, _additions, _allowed = guard.scan_patch(private_marker, patch)
    assert findings == (
        guard.Finding("bot/journal/traces/t.json", 1, "calls.[0].sources.[0]"),
        guard.Finding("bot/journal/traces/t.json", 1, "calls.[1].reconciliation"),
    )
    assert private_marker not in repr(findings)


def test_metaculus_record_match_verbatim_in_its_own_question_is_allowed() -> None:
    marker = "net" + " worth"
    pattern = "net.?worth"
    payload = record(
        question=f"Will Elon Musk's {marker} exceed $1T?",
        source={"platform": "metaculus"},
        reasoning=f"Forbes tracks his {marker} daily",
        research={"sources": [f"https://example.org/musk-net-worth-{2026}"]},
    )
    findings, allowed = guard.scan_added_line(
        pattern, "journal.jsonl", 1, json.dumps(payload, ensure_ascii=False)
    )
    assert findings == []
    assert allowed == 3

    # Same record on Manifold: the market text is user-authored, no exception.
    manifold = dict(payload, source={"platform": "manifold"})
    findings, allowed = guard.scan_added_line(
        pattern, "journal.jsonl", 1, json.dumps(manifold, ensure_ascii=False)
    )
    assert {f.field for f in findings} == {"question", "reasoning", "research.sources.[0]"}
    assert allowed == 0


def test_metaculus_match_absent_from_the_question_still_blocks() -> None:
    private_marker = "private" + "-marker-739"
    payload = record(
        question="Public question", source={"platform": "metaculus"},
        reasoning=f"mentions {private_marker}",
    )
    findings, allowed = guard.scan_added_line(
        private_marker, "journal.jsonl", 1, json.dumps(payload)
    )
    assert findings == [guard.Finding("journal.jsonl", 1, "reasoning")]
    assert allowed == 0


def test_cli_redacts_model_authored_trace_text_and_refuses_sources(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    traces = tmp_path / "bot" / "journal" / "traces"
    traces.mkdir(parents=True)
    private_marker = "private" + "-marker-739"
    doc = trace()
    doc["calls"][0]["dossier"] = f"digest mentions {private_marker}"
    doc["calls"][1]["disagreements"][0]["claim"] = f"claim about {private_marker}"
    target = traces / "2026-09-07-abcd1234.json"
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    subprocess.run(["git", "add", "--", "bot/journal/"], cwd=tmp_path, check=True)
    monkeypatch.setenv("LEAK_PATTERNS", private_marker)

    assert guard.main(["--root", str(tmp_path), "bot/journal/"]) == 1
    blocked = capsys.readouterr()
    assert "calls.[0].dossier" in blocked.err
    assert "calls.[1].disagreements.[0].claim" in blocked.err
    assert private_marker not in blocked.out + blocked.err

    assert guard.main(
        ["--root", str(tmp_path), "--redact-model-output", "bot/journal/"]
    ) == 0
    assert "redacted 2 model-output field(s)" in capsys.readouterr().out
    rewritten = json.loads(target.read_text(encoding="utf-8"))
    assert rewritten["calls"][0]["dossier"] == guard.MODEL_OUTPUT_REDACTION
    assert rewritten["calls"][1]["disagreements"][0]["claim"] == guard.MODEL_OUTPUT_REDACTION
    assert rewritten["calls"][0]["reasoning"] == "run one reasoning"
    assert private_marker not in target.read_text(encoding="utf-8")
    subprocess.run(["git", "add", "--", "bot/journal/"], cwd=tmp_path, check=True)
    assert guard.main(["--root", str(tmp_path), "bot/journal/"]) == 0

    # A match in a source URL is protected: refuse, leave the file untouched.
    other = traces / "2026-09-07-ffff0000.json"
    doc = trace(record_id="2026-09-07-ffff0000")
    doc["calls"][0]["sources"] = [f"https://example.org/{private_marker}"]
    doc["calls"][0]["reasoning"] = f"also {private_marker}"
    original = json.dumps(doc, ensure_ascii=False, indent=1)
    other.write_text(original, encoding="utf-8")
    subprocess.run(["git", "add", "--", "bot/journal/"], cwd=tmp_path, check=True)
    assert guard.main(
        ["--root", str(tmp_path), "--redact-model-output", "bot/journal/"]
    ) == 1
    assert "calls.[0].sources.[0]" in capsys.readouterr().err
    assert other.read_text(encoding="utf-8") == original
