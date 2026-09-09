"""The content-free deny-list narrowing tool (scripts/leak_patterns_tool.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import leak_patterns_tool as tool  # noqa: E402

HOME = "users.Edison.Yi"
WEALTH = "net.?worth"
POUND = chr(0xA3)


def test_split_respects_groups_brackets_and_escapes() -> None:
    assert tool.split_branches("a|b") == ["a", "b"]
    assert tool.split_branches("(a|b)c|[|x]|d\\|e|f") == ["(a|b)c", "[|x]", "d\\|e", "f"]
    assert tool.split_branches("solo") == ["solo"]
    assert "|".join(tool.split_branches("(a|b)|c")) == "(a|b)|c"


def test_report_flags_public_only_branches_and_keeps_private_ones(capsys) -> None:
    lines = tool.report(tool.split_branches(f"{HOME}|{WEALTH}|{POUND}|secret-street-7"))
    assert len(lines) == 4
    assert "private=home-path" in lines[0] and lines[0].endswith("keep")
    assert "wealth-phrase" in lines[1] and lines[1].endswith("DROP?")
    assert "pound-sign" in lines[2] and lines[2].endswith("DROP?")
    assert lines[3].endswith("keep (unknown)")
    for line in lines:
        assert HOME not in line and "secret-street" not in line and POUND not in line


def test_narrowed_drops_by_index_and_validation_protects_private_probes() -> None:
    pattern = f"{HOME}|{WEALTH}|{POUND}|secret-street-7"
    new = tool.narrowed(pattern, [1, 2])
    assert new == f"{HOME}|secret-street-7"
    assert tool.validate(pattern, new) == []
    assert any("home-path" in p for p in tool.validate(pattern, "secret-street-7"))
    assert any("empty" in p for p in tool.validate(pattern, ""))
    assert any("zero-width" in p for p in tool.validate(pattern, f"{HOME}|a*"))


def test_cli_dry_run_never_prints_the_pattern(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LEAK_PATTERNS", f"{HOME}|{WEALTH}|{POUND}")
    monkeypatch.setattr(tool, "set_secret", lambda new: (_ for _ in ()).throw(AssertionError))
    assert tool.main(["--report"]) == 0
    out = capsys.readouterr().out
    assert "3 top-level branch(es)" in out and HOME not in out and POUND not in out
    assert tool.main(["--drop", "1,2"]) == 0
    out = capsys.readouterr().out
    assert "narrowed: 3 -> 1 branch(es)" in out and "dry run" in out and HOME not in out
    assert tool.main(["--drop", "0"]) == 1
    assert "no longer catches PRIVATE probe home-path" in capsys.readouterr().err


def test_cli_requires_the_environment_variable(monkeypatch, capsys) -> None:
    monkeypatch.delenv("LEAK_PATTERNS", raising=False)
    assert tool.main(["--report"]) == 2
