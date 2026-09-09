"""The content-free deny-list narrowing tool (scripts/leak_patterns_tool.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import leak_patterns_tool as tool  # noqa: E402

# Built from parts: spelled out, this literal is exactly what a home-path branch of the
# real deny-list matches, and ci.yml greps the whole repo with it.
HOME = ".".join(["users", Path.home().name.replace(" ", ".")])
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


# ------------------------------------- [ADDED 2026-09-10] composing a replacement deny-list


def test_load_candidate_strips_comments_and_joins_branches(tmp_path: Path) -> None:
    target = tmp_path / "deny.txt"
    target.write_text(
        "# a comment\n\nalpha-9931\n   beta-4471  \n# trailing note\n", encoding="utf-8"
    )
    assert tool.load_candidate(target) == "alpha-9931|beta-4471"

    single = tmp_path / "one.txt"
    single.write_text("# note\nalpha-9931|beta-4471\n", encoding="utf-8")
    assert tool.load_candidate(single) == "alpha-9931|beta-4471"

    empty = tmp_path / "empty.txt"
    empty.write_text("# only comments\n\n", encoding="utf-8")
    try:
        tool.load_candidate(empty)
    except ValueError as exc:
        assert "no pattern lines" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("an all-comment file must not yield a pattern")


def test_repo_matches_reports_paths_and_counts_only(tmp_path: Path) -> None:
    import subprocess as sp

    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    marker = "alpha" + "-9931"
    (tmp_path / "src.py").write_text(f"value = '{marker}'\n{marker}\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("value = 1\n", encoding="utf-8")
    journal = tmp_path / "bot" / "journal"
    journal.mkdir(parents=True)
    (journal / "forecasts.jsonl").write_text(f'{{"a": "{marker}"}}\n', encoding="utf-8")
    sp.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    hits = tool.repo_matches(marker, tmp_path)

    # The journal is excluded exactly as ci.yml excludes it; clean files are absent.
    assert hits == ["src.py (2 match(es))"]
    assert marker not in " ".join(hits)


def test_candidate_cli_refuses_a_pattern_that_would_break_ci(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import subprocess as sp

    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    marker = "alpha" + "-9931"
    (tmp_path / "notes.md").write_text(f"mentions {marker}\n", encoding="utf-8")
    sp.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    candidate = tmp_path / "deny.txt"
    candidate.write_text(f"{marker}\n{HOME}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tool, "set_secret", lambda new: (_ for _ in ()).throw(AssertionError))

    assert tool.main(["--candidate", str(candidate), "--set"]) == 1
    out = capsys.readouterr()
    assert "notes.md (1 match(es))" in out.err
    assert "REFUSING" in out.err
    assert marker not in out.out


def test_candidate_cli_reports_and_installs_a_clean_pattern(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import subprocess as sp

    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "notes.md").write_text("nothing private here\n", encoding="utf-8")
    sp.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    candidate = tmp_path / "deny.txt"
    candidate.write_text(f"# mine\n{HOME}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    installed: list[str] = []
    monkeypatch.setattr(tool, "set_secret", installed.append)

    assert tool.main(["--candidate", str(candidate)]) == 0
    out = capsys.readouterr().out
    assert "repo check: clean" in out and "dry run" in out
    assert HOME not in out
    assert installed == []

    assert tool.main(["--candidate", str(candidate), "--set"]) == 0
    assert installed == [HOME]
    assert "delete the candidate file now" in capsys.readouterr().out


def test_candidate_cli_rejects_an_unusable_pattern(tmp_path: Path, monkeypatch, capsys) -> None:
    candidate = tmp_path / "deny.txt"
    candidate.write_text("a*\n", encoding="utf-8")
    monkeypatch.setattr(tool, "set_secret", lambda new: (_ for _ in ()).throw(AssertionError))
    assert tool.main(["--candidate", str(candidate), "--set"]) == 1
    assert "zero-width" in capsys.readouterr().err


def test_template_needs_no_secret_and_names_no_real_identifier(monkeypatch, capsys) -> None:
    monkeypatch.delenv("LEAK_PATTERNS", raising=False)
    assert tool.main(["--template"]) == 0
    out = capsys.readouterr().out
    assert "alternation separated by |" in out
    assert "Edison" not in out
