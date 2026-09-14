"""Second-eyes methodology fixes across the bench (v0.4.20 review).

Four verified holes, one test home:
1. readout_tranche1.py — coverage report, comparable common-set summary, the
   pre-registered differential-attrition rule, and provenance-gated --exclude-qid.
   Exercised in-process on SYNTHETIC fixtures only (sets/probe defaults monkeypatched):
   the real tranche results file is pre-registered and must never be peeked at from tests.
2. report.py — banner when a results file carries unrecognized tiers or nonzero runs
   (a general report is not the preregistered tranche readout).
3. evidence_ablation.py — emitted set files carry no answer-bearing keys; the printed
   command uses --leakfree none (--blind never existed); structural guard refuses less.
4. contamination_probe.py — majority-class baseline follows the probed subset, with the
   full-set YES rate printed alongside for divergence.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bench" / "analysis"))
sys.path.insert(0, str(ROOT / "bot"))

# ruff: noqa: E402  (imports follow the sys.path bootstrap above)
import contamination_probe as probe
import evidence_ablation as ablation
import readout_tranche1 as readout
import report

# ---------------------------------------------------------------------------
# FIX 1: readout_tranche1.py (in-process on synthetic fixtures — never the real file)
# ---------------------------------------------------------------------------

def _readout_fixtures(
    tmp_path: Path,
    res_by_qid: dict[str, int],
    arm_probs: dict[str, dict[str, float]],
    reasoning: dict[str, str] | None = None,
) -> tuple[Path, Path, Path]:
    """Synthetic (sets, probe, results) jsonl triple for the readout CLI."""
    sets = tmp_path / "sets.jsonl"
    sets.write_text("\n".join(json.dumps(
        {"id": q, "resolution": y, "crowd": {"value": 0.6}})
        for q, y in res_by_qid.items()), encoding="utf-8")
    probe_file = tmp_path / "probe.jsonl"
    probe_file.write_text("", encoding="utf-8")  # no contamination flags
    rows = []
    for tier, probs in arm_probs.items():
        for q, p in probs.items():
            row = {"qid": q, "tier": tier, "run": 0, "probability": p, "cost_usd": 0.01}
            if reasoning and q in reasoning:
                row["reasoning"] = reasoning[q]
            rows.append(json.dumps(row))
    results = tmp_path / "results.jsonl"
    results.write_text("\n".join(rows), encoding="utf-8")
    return sets, probe_file, results


def _run_readout(monkeypatch: pytest.MonkeyPatch, sets: Path, probe_file: Path,
                 results: Path, *extra: str) -> None:
    """Drive the upstream CLI (--results [+ --exclude-qid]) in-process. The sets and probe
    paths are the module's registered defaults, monkeypatched to synthetic fixtures so the
    real, pre-registered tranche artifacts are never read from a test."""
    monkeypatch.setattr(readout, "DEFAULT_SETS", sets)
    monkeypatch.setattr(readout, "DEFAULT_PROBE", probe_file)
    assert readout.main(["--results", str(results), *extra]) == 0


class TestReadoutCoverage:
    def test_missing_qid_is_named_and_counted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        res = {f"q{i}": i % 2 for i in range(1, 7)}
        full = {q: 0.7 for q in res}
        arm_probs = {"plain": {q: p for q, p in full.items() if q != "q3"},
                     "high": dict(full), "angles": dict(full)}
        _run_readout(monkeypatch, *_readout_fixtures(tmp_path, res, arm_probs))
        out = capsys.readouterr().out
        assert "n_union=6" in out
        assert re.search(r"plain\s+5/6\s+missing: q3", out)
        assert re.search(r"high\s+6/6\s+missing: none", out)
        assert re.search(r"angles\s+6/6\s+missing: none", out)
        # complete-case summary is the intersection: q3 drops out for every arm
        assert "n_common=5" in out
        # the own-set table survives, clearly labeled as non-comparable
        assert "OWN scorable set" in out and "NOT comparable" in out


class TestReadoutAttrition:
    """Rule: fires iff angles coverage >5% below high AND high's Brier on the
    angle-missing qids is >= 0.02 worse than on the angle-complete qids."""

    def _fixture(self, tmp_path: Path, n: int, hard_missing: bool) -> tuple[Path, Path, Path]:
        qids = [f"q{i:02d}" for i in range(1, n + 1)]
        res = dict.fromkeys(qids, 1)
        high = dict.fromkeys(qids, 0.9)
        high[qids[-1]] = 0.5 if hard_missing else 0.9  # Brier 0.25 vs 0.01 on the rest
        angles = {q: 0.7 for q in qids[:-1]}  # last qid missing -> gap = 1/n
        plain = dict.fromkeys(qids, 0.7)
        return _readout_fixtures(tmp_path, res,
                                 {"plain": plain, "high": high, "angles": angles})

    def test_fires_when_both_conditions_hold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # gap 1/10 = 10% > 5%; split 0.25 - 0.01 = 0.24 >= 0.02
        _run_readout(monkeypatch, *self._fixture(tmp_path, n=10, hard_missing=True))
        out = capsys.readouterr().out
        assert "ATTRITION-COMPROMISED" in out
        assert "resume the missing angles cells" in out

    def test_silent_when_missing_qids_are_not_harder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # gap 10% > 5%, but split = 0.00 < 0.02 -> coverage alone must not fire it
        _run_readout(monkeypatch, *self._fixture(tmp_path, n=10, hard_missing=False))
        out = capsys.readouterr().out
        assert "ATTRITION-COMPROMISED" not in out
        assert "attrition non-differential" in out

    def test_silent_at_exactly_five_percent_gap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # gap 1/20 = 5.0% is NOT "more than 5%", even with a 0.24 split
        _run_readout(monkeypatch, *self._fixture(tmp_path, n=20, hard_missing=True))
        out = capsys.readouterr().out
        assert "ATTRITION-COMPROMISED" not in out
        assert "attrition non-differential" in out


class TestReadoutExclusionProvenance:
    def _fixture(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        res = {f"q{i}": 1 for i in range(1, 7)}
        full = {q: 0.7 for q in res}
        arm_probs = {a: dict(full) for a in ("plain", "high", "angles")}
        # q2's run-0 reasoning asserts remembered outcome -> memory_screen candidate
        return _readout_fixtures(tmp_path, res, arm_probs,
                                 reasoning={"q2": "I recall the decision was announced."})

    def test_unvetted_qid_errors_with_its_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sets, probe_file, results = self._fixture(tmp_path)
        monkeypatch.setattr(readout, "DEFAULT_SETS", sets)
        monkeypatch.setattr(readout, "DEFAULT_PROBE", probe_file)
        with pytest.raises(SystemExit) as excinfo:
            readout.main(["--results", str(results), "--exclude-qid", "q4"])
        assert "q4" in str(excinfo.value) and "rejected" in str(excinfo.value)

    def test_memory_screen_candidate_is_accepted_with_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _run_readout(monkeypatch, *self._fixture(tmp_path), "--exclude-qid", "q2")
        out = capsys.readouterr().out
        assert "exclusion q2 accepted" in out
        assert "memory_screen prefilter match" in out
        assert "n_union=5" in out  # q2 actually excluded from every arm


# ---------------------------------------------------------------------------
# FIX 2: report.py banner for non-standard results files
# ---------------------------------------------------------------------------

def _run_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                rows: list[dict]) -> str:
    specs = [{"id": "q1", "resolution": 1}, {"id": "q2", "resolution": 0}]
    set_path = tmp_path / "genset.jsonl"
    set_path.write_text("\n".join(json.dumps(s) for s in specs), encoding="utf-8")
    results_dir = tmp_path / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / "genset.results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(report, "RESULTS_DIR", results_dir)
    assert report.main([str(set_path)]) == 0
    return (results_dir / "genset.report.md").read_text(encoding="utf-8")


BANNER = "NOT the preregistered tranche readout"


class TestReportBanner:
    def test_silent_for_single_run_standard_tiers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [{"qid": "q1", "tier": "high", "run": 0, "probability": 0.8},
                {"qid": "q1", "tier": "zero", "probability": 0.6},  # legacy: no run key
                {"qid": "q2", "tier": "high", "run": 0, "probability": 0.3}]
        assert BANNER not in _run_report(monkeypatch, tmp_path, rows)

    def test_banner_counts_odd_tiers_and_nonzero_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [{"qid": "q1", "tier": "plain", "run": 0, "probability": 0.7},
                {"qid": "q1", "tier": "angles", "run": 0, "probability": 0.6},
                {"qid": "q1", "tier": "high", "run": 0, "probability": 0.8},
                {"qid": "q1", "tier": "high", "run": 1, "probability": 0.9}]
        text = _run_report(monkeypatch, tmp_path, rows)
        assert BANNER in text
        assert "NOTE: 2 rows in unrecognized tiers / 1 nonzero runs pooled" in text
        assert "bench/analysis/readout_tranche1.py" in text


# ---------------------------------------------------------------------------
# FIX 3: evidence_ablation.py — leak-free command + answer-free set files
# ---------------------------------------------------------------------------

SRC_SPECS = [
    {"id": "btf2:a1", "source": "btf2", "question": "Will A?",
     "criteria": "Resolves YES if A.", "as_of": "2025-10-01",
     "background": "AS-OF DATE: 2025-10-01 - forecast as of this date.\n\n"
                   "Dossier body long enough to truncate. " * 20,
     "crowd": {"value": 0.8, "source": "btf2:futuresearch-sota", "at": "2025-10-01"},
     "resolution": 1},
    {"id": "btf2:a2", "source": "btf2", "question": "Will B?",
     "criteria": "Resolves YES if B.", "as_of": "2025-10-01",
     "background": "AS-OF DATE: 2025-10-01 - forecast as of this date.\n\nShort dossier.",
     "crowd": {"value": 0.2, "source": "btf2:futuresearch-sota", "at": "2025-10-01"},
     "resolution": 0},
]


class TestEvidenceAblation:
    def _build(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str]:
        src = tmp_path / "src.jsonl"
        src.write_text("\n".join(json.dumps(s) for s in SRC_SPECS), encoding="utf-8")
        assert ablation.main([str(src)]) == 0
        return src, capsys.readouterr().out

    def test_emitted_rows_carry_no_answer_keys(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src, _ = self._build(tmp_path, capsys)
        for arm in ("full", "half", "stub", "none"):
            rows = [json.loads(line) for line in
                    (tmp_path / f"src-ev-{arm}.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()]
            assert rows, arm
            for row in rows:
                assert "resolution" not in row
                assert "crowd" not in row
                assert row["id"].endswith(f"#ev-{arm}")

    def test_answers_sidecar_keeps_ground_truth_by_suffixed_qid(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._build(tmp_path, capsys)
        answers = {json.loads(line)["id"]: json.loads(line)["resolution"] for line in
                   (tmp_path / "answers" / "src-ev-full.jsonl")
                   .read_text(encoding="utf-8").splitlines() if line.strip()}
        assert answers == {"btf2:a1#ev-full": 1, "btf2:a2#ev-full": 0}

    def test_printed_commands_use_leakfree_none_not_blind(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, out = self._build(tmp_path, capsys)
        cmds = [line.strip() for line in out.splitlines()
                if line.strip().startswith("python bench/run_bench.py")]
        assert len(cmds) == 4
        for cmd in cmds:
            assert "--leakfree none" in cmd
            assert "--blind" not in cmd
            assert "--tiers zero" in cmd

    def test_guard_refuses_non_leakfree_command(self) -> None:
        with pytest.raises(SystemExit, match="leakfree"):
            ablation.require_leakfree("python bench/run_bench.py x.jsonl --tiers zero")
        with pytest.raises(SystemExit, match="leakfree"):
            ablation.require_leakfree(
                "python bench/run_bench.py x.jsonl --tiers zero --leakfree none --blind")

    def test_report_scores_an_arm_via_the_sidecar(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The documented scoring path works end to end: report.py pointed at the
        answers sidecar (same stem as the arm's set) finds the tagged results file and
        scores the suffixed qids against the sidecar's resolutions."""
        self._build(tmp_path, capsys)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "src-ev-full.ev-full.results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in [
                {"qid": "btf2:a1#ev-full", "tier": "zero", "run": 0, "probability": 0.9},
                {"qid": "btf2:a2#ev-full", "tier": "zero", "run": 0, "probability": 0.3},
            ]), encoding="utf-8")
        monkeypatch.setattr(report, "RESULTS_DIR", results_dir)
        sidecar = tmp_path / "answers" / "src-ev-full.jsonl"
        assert report.main([str(sidecar), "--tag", "ev-full"]) == 0
        text = (results_dir / "src-ev-full.ev-full.report.md").read_text(encoding="utf-8")
        assert "resolution scoring" in text
        assert "| zero | 2 |" in text  # both qids scored against the sidecar


# ---------------------------------------------------------------------------
# FIX 4: contamination_probe.py — baseline follows the probed subset
# ---------------------------------------------------------------------------

def _probe_set(tmp_path: Path) -> Path:
    """4 resolved questions, full-set YES rate 50%; the y* pair is the YES subset."""
    specs = [{"id": "y1", "question": "A?", "criteria": "c", "resolution": 1},
             {"id": "y2", "question": "B?", "criteria": "c", "resolution": 1},
             {"id": "n1", "question": "C?", "criteria": "c", "resolution": 0},
             {"id": "n2", "question": "D?", "criteria": "c", "resolution": 0}]
    set_path = tmp_path / "probeset.jsonl"
    set_path.write_text("\n".join(json.dumps(s) for s in specs), encoding="utf-8")
    return set_path


def _probe_rows() -> list[dict]:
    return [{"qid": q, "model": "claude-opus-4-6", "recall": "unknown",
             "confidence": 0.0, "resolution": 1, "correct": None, "cost_usd": 0.0}
            for q in ("y1", "y2")]


class TestProbeBaseline:
    def test_report_prints_subset_majority_and_full_rate(self) -> None:
        text = probe.report(_probe_rows(), base_rate_yes=1.0, full_base_rate_yes=0.5)
        assert "subset YES base rate 100%" in text
        assert "full-set YES base rate: 50%" in text
        assert "model-ANSWERED rows only" in text

    def test_report_path_derives_baseline_from_the_rows_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        set_path = _probe_set(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "probeset.probe.jsonl").write_text(
            "\n".join(json.dumps(r) for r in _probe_rows()), encoding="utf-8")
        monkeypatch.setattr(probe, "RESULTS_DIR", results_dir)
        assert probe.main([str(set_path), "--report"]) == 0
        out = capsys.readouterr().out
        # probed rows cover only the YES pair -> majority baseline follows THAT subset
        assert "subset YES base rate 100%" in out
        assert "full-set YES base rate: 50%" in out

    def test_run_path_baseline_over_postfilter_specs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--qids-from restricts to the all-YES pair; both probes already done, so no
        agent runs — the final report's baseline must follow the filtered subset."""
        set_path = _probe_set(tmp_path)
        qids_file = tmp_path / "qids.jsonl"
        qids_file.write_text("\n".join(json.dumps({"qid": q}) for q in ("y1", "y2")),
                             encoding="utf-8")
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "probeset.probe.jsonl").write_text(
            "\n".join(json.dumps(r) for r in _probe_rows()), encoding="utf-8")
        monkeypatch.setattr(probe, "RESULTS_DIR", results_dir)
        assert probe.main([str(set_path), "--qids-from", str(qids_file),
                           "--models", "claude-opus-4-6"]) == 0
        out = capsys.readouterr().out
        assert "0 to run" in out  # nothing executed — both jobs were already done
        assert "subset YES base rate 100%" in out
        assert "full-set YES base rate: 50%" in out
