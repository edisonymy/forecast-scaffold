"""forecast-scaffold core: schema, journal, scoring, aggregation, validation, config, CLI.

Single-file by design. This module is vendored byte-identically into each skill's
``scripts/fsj.py`` (see ``scripts/vendor_sync.py``) so that skill bundles are standalone —
it must stay pure stdlib and self-contained. Judgment lives in the skills' markdown;
this file owns only arithmetic and persistence.

Journal semantics (append-only JSONL, idempotent resolve) and the calibration report are
ported from the decision-scaffolding system this project was spun out of. Aggregation
defaults follow the published evidence: trimmed mean for correlated draws from one
forecaster (Halawi et al. 2024, arXiv:2402.18563), geometric mean of odds with the single
most extreme forecast on each end dropped for independent forecasters (Samotsvety's rule;
Sevilla, "When pooling forecasts, use the geometric mean of odds"), and a simple average
with the crowd/market number when one exists (Halawi et al.; Schoenegger et al. 2024).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 1
# The scaffold (plugin) release that produced a record. Distinct from SCHEMA_VERSION:
# schema versions the *format*, scaffold versions the *methodology*. Calibration analysis
# (e.g. a recalibration temperature) should be pinned to the major scaffold version, so
# every record must carry the version that made it. A test asserts this matches plugin.json.
SCAFFOLD_VERSION = "0.4.28"

QUESTION_TYPES = ("binary", "multiple_choice", "numeric", "discrete", "date")
STATUSES = ("draft", "open", "resolved", "annulled")
EFFORT_TIERS = ("low", "medium", "high")

#: Below this many scored forecasts, calibration is noise — report N, don't act on it.
MIN_CALIBRATION_N = 5

#: Single source of numeric truth. ``config/forecast.toml`` is the user-facing template and
#: must mirror these values (a test asserts equality); a user TOML overrides them at runtime.
DEFAULTS: dict[str, Any] = {
    "clamp": {"min": 0.02, "max": 0.98},
    "tiers": {
        # draws      = in-context estimates within ONE run (portable to any surface).
        # runs       = genuinely independent runs a harness launches and pools with
        #              geo_mean_odds (subagents/processes; the reliable differentiation lever —
        #              in-context draw instructions are demonstrably under-executed headlessly).
        # run_models = optional model ids the harness cycles through for runs after the first
        #              (cross-model diversity is the strongest documented ensemble lever:
        #              tournament winners average ~1.8 model families). Empty = one model.
        # runs sized lean (v0.4.0): research + 2 (medium) / 3 (high) reasoning runs. This
        # is a cost/quality judgment call, not a measured result: the old BTF-2 null
        # (harness - zero-shot = +0.0002 +/- 0.0148, n=85, v0.1.0) tested in-context draws,
        # not this independent-runs architecture, and its BTF-2 corpus was later found
        # partially contaminated (2026-07 probe: 8/55 questions flagged) — so it neither
        # supports nor refutes these sizes. A leak-free re-measurement is pending.
        # geo_mean_odds pools untrimmed, and the suggested-angle rotation leads with the
        # counter-biasing opposite pair so any k >= 2 stays directionally neutral.
        # min_sources = floor on DISTINCT actually-consulted sources the research (full)
        #              run must return; announced in its prompt and enforced mechanically
        #              by the bot's validate/repair loop BEFORE any forecast is accepted.
        #              Reasoning runs are exempt ([] stays honest there). Added 0.4.5:
        #              the first live batch put its most crowd-divergent calls on its
        #              thinnest research (q44381 MC: 0 sources; q44382/q44511: 2) —
        #              exactly the paths the dossier contract never covered.
        # run_angles = engineered evidence diversity. Non-empty FLIPS the bot flow: instead of
        #              one shared-dossier research run + reasoning-only runs, it launches one
        #              INDEPENDENT full-research run per angle letter (operator briefs in
        #              skills/forecast/references/research-angles.md), then pools them.
        #              Measured why: runs that share one dossier correlate ~0.97 (members
        #              disagree ~0.03), so the pool equals the member average at N times the
        #              cost — evidence diversity is the pooling prerequisite.
        #              DEFAULT since 2026-09-03 (operator decision): PARALLEL RESEARCH —
        #              "P" = plain replicate, a full research run with no lens; medium runs
        #              three, high four, matching `runs`. [] = the dossier fallback.
        #              All question types pool (binary: geo_mean_odds; continuous: per-
        #              percentile mean; MC: per-option geometric mean, renormalized).
        # share_evidence = angle mode only, PHASE 2. Every angle run also writes the
        #              estimate-free dossier; after they all finish, each run gets one
        #              reasoning-only call carrying its own dossier plus the OTHER runs'
        #              dossiers (evidence, never numbers or reasoning), and those second
        #              estimates are pooled instead. The blackboard design from
        #              docs/research-notes-multiagent-forecasting-2026-09-03 §4: shared
        #              evidence, independent judgment. OFF at every tier by operator
        #              decision (2026-09-03) — it is an experiment switch, not production:
        #              it adds ~$1.3/question at medium and the herding risk (Lorenz 2011:
        #              circulating others' views collapses variance without improving
        #              accuracy) is exactly what bench/analysis/phase_pools.py measures.
        # supervisor = angle mode only, PHASE 3. One reconciler call sees every dossier and
        #              every run's estimate WITH its reasoning, classifies the disagreements
        #              FACTUAL vs JUDGMENT, settles the factual ones, and issues the final
        #              number — the only interactive design with forecasting-specific
        #              quantitative evidence (AIA Forecaster 0.1182 -> 0.1125; Cassi). It
        #              never averages: the pools are already the averaging arm, and both
        #              are journaled so the reconciler is scored against them.
        # supervisor_search_spread / supervisor_search_spread_iqr = the disagreement
        #              thresholds that decide whether the reconciler gets web tools. Runs
        #              that already agree have no factual dispute to settle, so a cheap
        #              REASONING-ONLY reconciliation is enough; only genuine disagreement
        #              buys the research-capable call (~$1.0-1.5 more). The first threshold
        #              is on a probability scale (binary: max-min probability; MC: max-min
        #              of the top option); the second is for continuous questions and is
        #              measured in IQR units (max-min of the run medians divided by the
        #              pooled p75-p25, so 1.0 means "the medians differ by a full IQR").
        #              Unused where supervisor is false.
        # RESEARCH DEPTH IS NOW UNIFORM ACROSS TIERS (operator, 2026-09-03): searches=5 and
        # min_sources=3 everywhere. A tier is a statement about how much INDEPENDENT
        # judgment a question gets — the run count and what synthesizes it — not about how
        # carefully any one run reads the world. The old ladder (1/5/12 searches, 1/3/5
        # sources) made a low-tier run research badly on purpose, which is the one economy
        # the survey evidence contradicts outright: "research sources used" is the single
        # strongest measured correlate of bot performance (r=+0.42, p=.006, Fall 2025
        # FutureEval survey), while aggregation counts were not significant.
        "low": {"draws": 1, "searches": 5, "runs": 1, "run_models": [], "min_sources": 3,
                "run_angles": [], "share_evidence": False, "supervisor": False,
                "supervisor_search_spread": 0.10, "supervisor_search_spread_iqr": 0.75},
        "medium": {"draws": 5, "searches": 5, "runs": 3, "run_models": [], "min_sources": 3,
                   "run_angles": ["P", "P", "P"], "share_evidence": False,
                   "supervisor": True, "supervisor_search_spread": 0.10,
                   "supervisor_search_spread_iqr": 0.75},
        "high": {"draws": 12, "searches": 5, "runs": 4, "run_models": [], "min_sources": 3,
                 "run_angles": ["P", "P", "P", "P"], "share_evidence": False,
                 "supervisor": True, "supervisor_search_spread": 0.08,
                 "supervisor_search_spread_iqr": 0.5},
    },
    # 0.8 = Halawi et al.'s validated optimum ("4x weight for the crowd", NeurIPS 2024)
    # for blending with the SAME question's human crowd/market — the chat/CLI use where a
    # judged-relevant value is passed explicitly. The BOT harness never blends (v0.4.11,
    # operator decision): cross-platform contract equivalence takes judgment, so market
    # anchoring lives in the agent's research step, disclosed in its reasoning.
    "blend": {"crowd_weight": 0.8},
    "aggregation": {"method": "trimmed_mean"},
    "journal": {"path": "forecasts.jsonl"},
    # Informational: which models the host agent should prefer. The scaffold never calls
    # models itself — the host agent does — so these are hints, not dependencies.
    "models": {"triage": "", "notes": "model choice dominates scaffolding; keep this fresh"},
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _is_iso_date(value: str) -> bool:
    """Strict YYYY-MM-DD. Anything else breaks the journal's date comparisons."""
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


class AlreadyResolved(ValueError):
    """A resolution is calibration *data* — replacing one must be explicit, never silent."""


# --------------------------------------------------------------------------- config


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | None = None) -> dict[str, Any]:
    """DEFAULTS, overridden by a TOML file if one is found.

    Search order: explicit ``path`` arg -> ``$FORECAST_CONFIG`` -> ``./forecast.toml``.
    A missing *implicit* ./forecast.toml is fine (defaults apply), but an explicitly named
    file that doesn't exist raises — a typo'd config must never silently change behavior.
    """
    explicit = path or os.environ.get("FORECAST_CONFIG")
    candidate = explicit or "forecast.toml"
    file = Path(candidate)
    if not file.is_file():
        if explicit:
            raise FileNotFoundError(f"config file not found: {explicit}")
        copy: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        return copy
    with file.open("rb") as fh:
        return _merge(DEFAULTS, tomllib.load(fh))


# --------------------------------------------------------------------------- schema


@dataclass
class ForecastRecord:
    """One forecast: a well-posed question, a probability (or distribution), provenance,
    and eventually a resolution. Serialized as one JSON object per JSONL line.

    Only ``question`` is required; a record can be opened as a draft and filled in.
    Versioning policy: additive optional fields do NOT bump ``schema_version``; breaking
    changes bump it, and readers must accept version N and N-1.
    """

    question: str
    id: str = ""
    schema_version: int = SCHEMA_VERSION
    scaffold_version: str = SCAFFOLD_VERSION  # methodology version that produced this record
    created: str = field(default_factory=_utc_now)
    forecast_at: str | None = None  # when the probability was committed (pre-registration)
    status: str = "open"  # "draft" | "open" | "resolved" | "annulled"
    # Provenance (v0.4.8): True when the producing run never submitted anywhere (--dry-run
    # / --post backtests). None on pre-0.4.8 records — treat None as "assume live". A
    # dry-run record must never be scored as part of the live track record.
    dry_run: bool | None = None

    # estimand
    question_type: str = "binary"
    resolution_criterion: str = ""  # verbatim; carried into every reasoning prompt
    resolve_by: str | None = None  # ISO date by which the answer should be known
    source: dict[str, Any] | None = None  # {"platform", "question_id", "url"}
    reference_class: str = ""  # the outside-view anchor
    base_rate: float | None = None  # its numeric base rate, when one exists
    # Optional audit trail for the base rate: the enumerated cases, one string per instance,
    # ending "-> yes" / "-> no" when countable. When present, the base rate can be *computed*
    # (smoothed count) instead of estimated, and readers of the public journal can check it.
    reference_class_instances: list[str] | None = None
    why_it_matters: str = ""  # VOI: which decision this forecast moves
    parent_id: str | None = None  # decomposition parent (fast-proxy linkage)
    fast_proxy: bool = False

    # forecast (shape gated by question_type; see validate_record)
    probability: float | None = None  # binary
    # The pooled binary probability BEFORE post-hoc logistic recalibration (Platt scaling)
    # was applied. Additive/optional (v0.4.19): defaults None and is written ONLY when a
    # recalibration actually moved the number, so a deployment with no recalibration.json
    # journals byte-identically to before this field existed. When set, ``probability`` is
    # the recalibrated value that was submitted and this is the raw pre-recalibration one.
    raw_probability: float | None = None
    options: list[str] | None = None  # multiple_choice
    probabilities: list[float] | None = None  # multiple_choice, parallel to options
    percentiles: dict[str, float] | None = None  # numeric/date: {"10": v, ...} monotone
    expected_value: float | None = None  # optional point estimate / EV alongside percentiles
    # Declared out-of-bound ("escape") mass on an OPEN bound (v0.4.23). Unconditional
    # probabilities in [0, 0.5] that the outcome lands entirely outside the question's range;
    # when either is set the five percentiles describe the distribution CONDITIONAL on landing
    # inside. Valid only where the corresponding bound is open (see validate_escape_mass).
    # Both default None and are omitted from the serialized record, so a run that declares
    # nothing journals byte-identically to before this field existed.
    p_below_lower: float | None = None  # numeric/discrete/date: P(outcome < range_min)
    p_above_upper: float | None = None  # numeric/discrete/date: P(outcome > range_max)
    # Self-stated dispersion contract (v0.4.26): the 10-90 width in question units that the
    # run's OWN dispersion analysis implies, stated before the percentiles were tuned, and
    # the named computation behind it. The bot's validate/repair loop refuses a percentile
    # set materially narrower than this number (bot/run_bot.py, DISPERSION_WIDTH_FLOOR) —
    # motivated by the 2026-08-10 Parana miss (q45325, -142.4), whose reasoning stated an
    # 11-day SD of 0.5 m and whose declared 10-90 implied 0.34: the prose held the right
    # dispersion and the percentiles quietly clipped it. All three fields default None and
    # are omitted from the serialized record, so prior records are byte-identical.
    dispersion_90_10: float | None = None
    dispersion_basis: str | None = None  # e.g. "SD of 11-day changes 0.5 m x 2.56"
    # Attempt-0 percentiles from a run whose first payload the dispersion-width guard
    # rejected (kept ONLY in that case), so resolution can score the guard's effect paired
    # against the run's own pre-guard answer — no A/B arm needed.
    percentiles_pre_guard: dict[str, float] | None = None
    # Multi-run provenance for the non-binary types (v0.4.28), the analogue of ``raw_draws``
    # on binaries: the per-run values the pool was built from, research run FIRST, plus that
    # research run's own answer kept separately as the single-run COUNTERFACTUAL. Journaling
    # both is what makes pooling scorable paired at resolution (run 1 is exactly what the
    # old forced-single-run harness would have submitted) with no A/B arm and no lost
    # question, the same trick as ``percentiles_pre_guard``. All five default None and are
    # dropped from the serialized record, so a single-run forecast journals byte-identically
    # to before these fields existed.
    run_percentiles: list[dict[str, float]] | None = None  # continuous: one dict per run
    run_escapes: list[list[float | None]] | None = None  # continuous: [below, above] per run
    percentiles_run1: dict[str, float] | None = None  # continuous: the research run's own
    run_probabilities: list[dict[str, float]] | None = None  # MC: {option: p} per run
    probabilities_run1: dict[str, float] | None = None  # MC: the research run's own
    # Phased angle mode (v0.4.28). Phase 1 = the independent research runs. Phase 2
    # (tier ``share_evidence``, off by default) re-asks each run for an estimate after
    # circulating the OTHER runs' estimate-free dossiers; when it runs, the per-run fields
    # above hold the PHASE-2 numbers and these hold phase 1, so both pools stay scorable.
    # Phase 3 (tier ``supervisor``) reconciles the runs and its number is what was
    # submitted. ``pool_phase1``/``pool_phase2`` are the pooled values in the question
    # type's own shape (binary: float; continuous: the five percentiles; MC: {option: p});
    # ``spread_phase1``/``spread_phase2`` are that phase's disagreement (binary: max-min
    # probability; continuous: max-min of the run medians in question units; MC: max-min of
    # the pooled leader's probability) — the herding check in
    # bench/analysis/phase_pools.py needs the spreads, not just the scores, because
    # variance collapse without an accuracy gain is the documented failure of circulating
    # forecasts (Lorenz et al. 2011). All default None and are dropped from the serialized
    # record, so a plain pooled forecast journals byte-identically to before.
    raw_draws_phase1: list[float] | None = None  # binary: the phase-1 per-run probabilities
    run_percentiles_phase1: list[dict[str, float]] | None = None  # continuous, per run
    run_escapes_phase1: list[list[float | None]] | None = None  # continuous, parallel
    run_probabilities_phase1: list[dict[str, float]] | None = None  # MC, per run
    pool_phase1: float | dict[str, float] | None = None
    pool_phase2: float | dict[str, float] | None = None
    spread_phase1: float | None = None
    spread_phase2: float | None = None
    # {"estimate", "reconciliation", "sources", "mode", "spread", "cost_usd"} — the
    # reconciler's own output, kept whether or not its number was the one submitted.
    supervisor: dict[str, Any] | None = None
    # Continuous-question submission provenance (v0.4.13): the exact CDF submitted to the
    # platform and the scaling it was built against. Percentiles alone can't be reconstructed
    # into the ~201-point object the platform scored (open/closed bounds, log scaling, and the
    # per-bin cap all bend the curve), so a preregistration record must carry both. Both None
    # for non-continuous questions.
    submitted_cdf: list[float] | None = None  # numeric/date: the CDF sent to the platform
    scaling: dict[str, Any] | None = None  # {range_min, range_max, zero_point, *_open, cdf_size}
    raw_draws: list[float] | None = None  # individual ensemble draws (audit trail)
    aggregation: str | None = None  # e.g. "trimmed_mean(n=5)"
    effort: str | None = None  # "low" | "medium" | "high", "(auto)" suffix if auto-triaged
    model: str = ""  # free string; never hardcoded in skills
    provider: str | None = None  # billing/routing path, e.g. "subscription" | "openrouter"
    # Whether the run was crowd-blind (measurement mode). Explicit since 0.4.3: the old
    # proxy — crowd.shown_to_agent — stopped encoding the mode when v0.4.2 made it
    # always-False on bot records (a bot-visible "crowd" is other bots' aggregate and is
    # never shown), which would have relabelled every sighted record as blind in scoring.
    blind: bool | None = None
    crowd: dict[str, Any] | None = None  # {"value", "source", "at"} captured at forecast time
    cost_usd: float | None = None  # what producing this forecast cost (all agent calls)
    # Path, RELATIVE TO THE JOURNAL FILE'S DIRECTORY, of the per-question trace: one JSON
    # object per agent call (phase, run index, model, cost, seconds, that run's own
    # estimate, its reasoning, its sources, its dossier) plus the pools and what was
    # submitted. The journal answers "what did it forecast"; the trace answers "what did
    # each run actually think", which is the thing a reviewer cannot reconstruct from a
    # pooled number. Written after the record, best-effort: a trace that fails to write
    # prints and never blocks a forecast, so this can be absent on any row.
    trace_path: str | None = None
    reasoning: str = ""
    what_would_change_my_mind: list[str] = field(default_factory=list)
    research: dict[str, Any] | None = None  # {"n_searches", "sources": [...]}

    # resolution: {"outcome": bool|float|str, "resolved_on": iso, "note": str}
    resolution: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.question or not self.question.strip():
            raise ValueError("question must be non-empty")
        if self.question_type not in QUESTION_TYPES:
            raise ValueError(f"question_type must be one of {QUESTION_TYPES}")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if self.probability is not None and not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be in [0, 1]")
        if not self.id:
            self.id = f"{self.created[:10]}-{uuid4().hex[:8]}"

    # -- lifecycle -----------------------------------------------------------
    def resolve(
        self,
        outcome: bool | float | str,
        *,
        note: str = "",
        resolved_on: str | None = None,
    ) -> ForecastRecord:
        self.resolution = {
            "outcome": outcome,
            "resolved_on": resolved_on or _today(),
            "note": note,
        }
        self.status = "resolved"
        return self

    def annul(self, note: str = "", *, overwrite: bool = False) -> ForecastRecord:
        """Mark the question annulled/ambiguous — excluded from all scoring.

        Annulling an already-resolved record destroys calibration data, so it demands the
        same explicit ``overwrite`` that re-resolving does."""
        if self.resolution is not None and not overwrite:
            raise AlreadyResolved(
                f"record {self.id!r} already carries a resolution; "
                "pass overwrite=True (CLI: --overwrite) to annul it anyway"
            )
        self.status = "annulled"
        self.resolution = {"outcome": None, "resolved_on": _today(), "note": note}
        return self

    @property
    def scorable(self) -> bool:
        """True when this record can enter the Brier score: a resolved (not annulled)
        binary forecast with a probability and a boolean outcome."""
        return (
            self.status == "resolved"
            and self.question_type == "binary"
            and self.probability is not None
            and self.resolution is not None
            and isinstance(self.resolution.get("outcome"), bool)
        )

    # -- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForecastRecord:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------- journal


def default_journal_path() -> Path:
    return Path(os.environ.get("FORECAST_JOURNAL") or DEFAULTS["journal"]["path"])


class Journal:
    """Append-only JSONL store of :class:`ForecastRecord`. ``resolve`` rewrites the file in
    place (git history preserves the pre-image — that, plus platform timestamps, is the
    tamper-evidence story)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_journal_path()

    def append(self, record: ForecastRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_json() + "\n")

    def __iter__(self) -> Iterator[ForecastRecord]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield ForecastRecord.from_dict(json.loads(line))

    def all(self) -> list[ForecastRecord]:
        return list(self)

    def get(self, record_id: str) -> ForecastRecord | None:
        return next((r for r in self if r.id == record_id), None)

    def open_records(self) -> list[ForecastRecord]:
        return [r for r in self if r.status in ("draft", "open")]

    def due(self, today: str | None = None) -> list[ForecastRecord]:
        """Open forecasts whose resolve_by date has passed — what ``calibrate`` resolves.

        Dates compare as ``date`` objects; a record with an unparseable ``resolve_by``
        (pre-validation legacy) is reported as due rather than silently lost forever."""
        cutoff = date.fromisoformat(today) if today else date.today()
        due: list[ForecastRecord] = []
        for r in self.open_records():
            if r.resolve_by is None:
                continue
            try:
                if date.fromisoformat(r.resolve_by) <= cutoff:
                    due.append(r)
            except ValueError:
                due.append(r)  # malformed date: surface it so it gets fixed, don't hide it
        return due

    def rewrite(self, records: list[ForecastRecord]) -> None:
        # Atomic: a crash mid-rewrite must not truncate the only copy of the journal.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(record.to_json() + "\n")
        os.replace(tmp, self.path)

    def resolve(
        self,
        record_id: str,
        outcome: bool | float | str,
        *,
        note: str = "",
        resolved_on: str | None = None,
        overwrite: bool = False,
    ) -> bool:
        """Resolve ``record_id`` in place and persist. Returns False if no such record.
        Raises :class:`AlreadyResolved` on a second resolution unless ``overwrite=True``."""
        records = self.all()
        found = False
        for record in records:
            if record.id == record_id:
                if record.resolution is not None and not overwrite:
                    raise AlreadyResolved(
                        f"record {record_id!r} is already resolved; "
                        "pass overwrite=True (CLI: --overwrite) to replace its resolution"
                    )
                if record.question_type == "binary" and not isinstance(outcome, bool):
                    raise ValueError(
                        f"a binary forecast resolves to true/false, got {outcome!r} — "
                        "a non-boolean outcome would silently drop it from scoring"
                    )
                record.resolve(outcome, note=note, resolved_on=resolved_on)
                found = True
        if found:
            self.rewrite(records)
        return found

    def annul(self, record_id: str, note: str = "", *, overwrite: bool = False) -> bool:
        records = self.all()
        found = False
        for record in records:
            if record.id == record_id:
                record.annul(note, overwrite=overwrite)
                found = True
        if found:
            self.rewrite(records)
        return found


# --------------------------------------------------------------------------- scoring


def brier_score(records: list[ForecastRecord]) -> float | None:
    """Mean Brier over scorable records. Lower is better; 0.25 = always guessing 50%.
    Annulled records never enter. Returns None when nothing is scorable."""
    scored = [r for r in records if r.scorable]
    if not scored:
        return None
    total = 0.0
    for r in scored:
        assert r.probability is not None and r.resolution is not None
        hit = 1.0 if r.resolution["outcome"] else 0.0
        total += (r.probability - hit) ** 2
    return total / len(scored)


@dataclass
class CalibrationReport:
    """Calibration of resolved forecasts, honest about small N."""

    n: int
    brier: float | None
    mean_predicted: float | None
    mean_realized: float | None
    direction: str  # "over-confident" | "under-confident" | "well-calibrated" | "insufficient data"
    high_confidence: str = ""  # note on p>=0.7 calls, where over-confidence usually shows
    detail: str = ""

    def summary(self) -> str:
        if self.n == 0:
            return "No resolved forecasts yet — nothing to score."
        lines = [f"Calibration over N = {self.n} resolved forecast(s):"]
        lines.append(
            f"  Brier score      : {self.brier:.3f}" if self.brier is not None
            else "  Brier score      : n/a"
        )
        if self.mean_predicted is not None and self.mean_realized is not None:
            lines.append(
                f"  mean predicted P : {self.mean_predicted:.2f}   "
                f"realized rate : {self.mean_realized:.2f}"
            )
        lines.append(f"  Direction        : {self.direction}")
        if self.high_confidence:
            lines.append(f"  High-confidence  : {self.high_confidence}")
        if self.n < MIN_CALIBRATION_N:
            lines.append(f"  (N < {MIN_CALIBRATION_N}: noise — report it, don't act on it.)")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calibration_report(records: list[ForecastRecord]) -> CalibrationReport:
    """Brier plus the direction of miscalibration — the actionable part.

    Direction is computed on *folded* confidence (``max(p, 1-p)`` vs how often the favored
    side occurred), so a journal of confident-NO calls is judged the same as confident-YES —
    a raw predicted-minus-realized gap would label an overconfident NO-sayer "under-confident".
    """
    scored: list[tuple[float, bool]] = []
    for r in records:
        if r.scorable:
            assert r.probability is not None and r.resolution is not None
            scored.append((r.probability, bool(r.resolution["outcome"])))
    n = len(scored)
    brier = brier_score(records)
    if n == 0:
        return CalibrationReport(0, brier, None, None, "insufficient data")

    mean_pred = sum(p for p, _ in scored) / n
    mean_real = sum(1.0 for _, hit in scored if hit) / n

    # Fold at 0.5: confidence = max(p, 1-p); "right" = the favored side occurred.
    folded = [(max(p, 1.0 - p), hit if p >= 0.5 else not hit) for p, hit in scored]
    mean_conf = sum(c for c, _ in folded) / n
    right_rate = sum(1.0 for _, right in folded if right) / n

    confident = [(c, right) for c, right in folded if c >= 0.7]
    high_note = ""
    if confident:
        conf_right = sum(1.0 for _, right in confident if right) / len(confident)
        conf_mean = sum(c for c, _ in confident) / len(confident)
        high_note = (
            f"{len(confident)} confident call(s) (p>=0.7 or <=0.3) were right "
            f"{conf_right:.0%} (claimed ~{conf_mean:.0%})"
        )

    # Tolerance keeps the exact-boundary case deterministic across Python versions
    # (3.12's sum() is more accurate than 3.11's, which flips 1e-16-level noise).
    gap = mean_conf - right_rate
    threshold = 0.1 + 1e-9
    if n < MIN_CALIBRATION_N:
        direction = "insufficient data"
    elif gap > threshold:
        direction = "over-confident"
    elif gap < -threshold:
        direction = "under-confident"
    else:
        direction = "well-calibrated"

    return CalibrationReport(
        n=n,
        brier=brier,
        mean_predicted=mean_pred,
        mean_realized=mean_real,
        direction=direction,
        high_confidence=high_note,
        detail=f"folded confidence-minus-accuracy gap = {gap:+.2f}",
    )


#: Keys ``score_by`` accepts, and the order ``--by`` groups render in when multiple are given.
GROUP_KEYS = ("scaffold_version", "blind", "model", "effort", "provider", "question_type")

#: Anchor rule (owner's instruction): Brier scores are only comparable within a methodology
#: version and a blind/sighted condition, so this is the default even when nobody passes
#: ``--by`` — silently pooling across versions or blind/sighted makes the numbers meaningless.
DEFAULT_GROUP_KEYS = ("scaffold_version", "blind")

_UNKNOWN = "?"  # missing field on an otherwise-groupable record


def _group_key_value(record: ForecastRecord, key: str) -> str:
    """The label a record sorts under for one ``--by`` key. Missing fields group as ``"?"``,
    except ``blind`` with no crowd captured at all, which is genuinely a third state
    (neither confirmed blind nor confirmed sighted) and is labelled ``"unknown"``."""
    if key == "blind":
        if record.blind is not None:
            return "blind" if record.blind else "sighted"
        # Legacy records (pre-0.4.3) encoded the mode in crowd.shown_to_agent; that stays
        # correct for everything published before v0.4.2 pinned the flag to False.
        if record.crowd is None:
            return "unknown"
        shown = record.crowd.get("shown_to_agent")
        if shown is None:
            return "unknown"
        return "blind" if shown is False else "sighted"
    value = getattr(record, key, None)
    if value is None or value == "":
        return _UNKNOWN
    return str(value)


@dataclass
class GroupedCalibrationReport:
    """``calibration_report`` stratified by one or more of :data:`GROUP_KEYS`, plus a
    pooled-across-everything line kept for context (never for iteration decisions)."""

    keys: tuple[str, ...]
    groups: list[tuple[dict[str, str], CalibrationReport]]
    pooled: CalibrationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "keys": list(self.keys),
            "groups": [{"group": g, "report": r.to_dict()} for g, r in self.groups],
            "pooled": self.pooled.to_dict(),
        }

    def summary(self) -> str:
        lines = [f"Grouped by: {', '.join(self.keys)}"]
        for group, report in self.groups:
            label = ", ".join(f"{k}={v}" for k, v in group.items())
            lines.append(f"\n-- {label} --")
            lines.append(report.summary())
        lines.append("\n-- pooled (all groups) --")
        lines.append(self.pooled.summary())
        return "\n".join(lines)


def score_by(records: list[ForecastRecord], keys: tuple[str, ...]) -> GroupedCalibrationReport:
    """Stratify :func:`calibration_report` by ``keys`` (each one of :data:`GROUP_KEYS`).

    Groups are sorted by their label tuple for deterministic output. Each group gets its own
    honest small-N treatment (same threshold and wording as the pooled report) — stratifying
    only shrinks N per group, so the noise warning matters *more* here, not less. The pooled
    line is always included, clearly labelled, so pooled and stratified numbers are never
    mistaken for each other.
    """
    unknown = [k for k in keys if k not in GROUP_KEYS]
    if unknown:
        raise ValueError(f"unknown --by key(s) {unknown}; choose from {list(GROUP_KEYS)}")
    if not keys:
        raise ValueError(f"--by needs at least one key from {list(GROUP_KEYS)}")

    buckets: dict[tuple[str, ...], list[ForecastRecord]] = {}
    for r in records:
        label = tuple(_group_key_value(r, k) for k in keys)
        buckets.setdefault(label, []).append(r)

    groups = [
        (dict(zip(keys, label, strict=True)), calibration_report(bucket))
        for label, bucket in sorted(buckets.items())
    ]
    return GroupedCalibrationReport(keys=keys, groups=groups, pooled=calibration_report(records))


# --------------------------------------------------------------------------- recalibration
#
# Post-hoc logistic recalibration (Platt scaling): fit a 2-parameter logistic map
#     outcome ~ sigmoid(a * logit(p) + b)
# from the deployment's OWN resolved history, then apply it to future probabilities before
# submission. This is the highest-value portable lever from the Metaculus bot ecosystem.
#
# Measured on our pastcast data (760 forecasts, honest question-level 5-fold CV):
# Brier 0.1997 -> 0.1761 with a fitted slope a=0.573 — opus-4-6 is OVERconfident on hard-news
# questions and wants shrinking toward 0.5 (a<1). CRITICAL: the sign is model- and
# distribution-specific. Live tournament bots are frequently UNDER-confident (a>1). So this
# layer MUST be fitted from the deployment's own resolved history and it SHIPS INERT (the
# identity map) until fitted — we never hardcode a shrink or a stretch.

#: A 2-parameter logistic fit needs materially more data than the 1-threshold direction
#: check's MIN_CALIBRATION_N=5. With two free parameters, N in the single digits overfits the
#: noise and can emit a map worse than identity; 40 is a conservative floor for a stable slope.
RECAL_MIN_N = 40

_RECAL_EPS = 1e-6  # clamp p away from {0,1} so logit stays finite


def _logit(p: float) -> float:
    p = min(1.0 - _RECAL_EPS, max(_RECAL_EPS, float(p)))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    # Overflow-safe on both tails.
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def fit_platt(
    pairs: list[tuple[float, Any]], min_n: int = RECAL_MIN_N
) -> tuple[float, float]:
    """Fit ``(a, b)`` maximizing the log-likelihood of ``outcome ~ sigmoid(a*logit(p)+b)``
    over ``(probability, outcome)`` pairs by batch gradient ascent.

    Returns the identity map ``(1.0, 0.0)`` when there are fewer than ``min_n`` pairs — too
    little data to fit two parameters without overfitting. Probabilities are clamped to
    ``(1e-6, 1-1e-6)`` before the logit so 0/1 inputs stay finite."""
    pts = [(_logit(p), 1.0 if o else 0.0) for p, o in pairs]
    if len(pts) < min_n:
        return (1.0, 0.0)
    a, b = 1.0, 0.0
    n = len(pts)
    lr = 0.1
    for _ in range(1000):
        grad_a = 0.0
        grad_b = 0.0
        for x, y in pts:
            err = y - _sigmoid(a * x + b)  # d/dtheta of log-likelihood
            grad_a += err * x
            grad_b += err
        a += lr * grad_a / n
        b += lr * grad_b / n
    return (a, b)


def apply_recalibration(
    p: float, a: float, b: float, clamp_band: tuple[float, float] | None = None
) -> float:
    """Map ``p`` through the fitted logistic: ``sigmoid(a*logit(p)+b)``, clamped to
    ``clamp_band`` (default ``None`` = the DEFAULTS clamp band). Returns ``p`` unchanged —
    exact identity — when ``(a, b)`` is the identity map ``(1.0, 0.0)``, so an unfitted
    deployment is byte-for-byte a no-op.

    ``clamp_band`` exists for callers whose own submission band is WIDER than the DEFAULTS
    one (the bot clamps to [0.01, 0.99]): ACTIVATING recalibration must never tighten the
    effective band, so such a caller passes its band instead of inheriting the default."""
    if (a, b) == (1.0, 0.0):
        return p
    if clamp_band is None:
        lo = float(DEFAULTS["clamp"]["min"])
        hi = float(DEFAULTS["clamp"]["max"])
    else:
        lo, hi = float(clamp_band[0]), float(clamp_band[1])
    return clamp(_sigmoid(a * _logit(p) + b), lo, hi)


def recalibration_cv(
    pairs: list[tuple[float, Any]], folds: int = 5
) -> dict[str, float]:
    """k-fold out-of-sample check. For each held-out fold, fit Platt on the other folds and
    score raw vs recalibrated Brier on the held-out points. Returns ``raw_brier``,
    ``recal_brier``, ``delta`` (recal - raw; NEGATIVE means recalibration helped), and
    ``mean_slope`` (mean fitted ``a`` across folds).

    Used to REFUSE emitting params when the out-of-sample ``delta >= 0`` (recalibration did
    not beat identity). Folds fit with ``min_n=1`` so each training split actually fits — the
    RECAL_MIN_N gate is applied separately to the full dataset by the caller."""
    data = list(pairs)
    n = len(data)
    if n == 0:
        return {"raw_brier": 0.0, "recal_brier": 0.0, "delta": 0.0, "mean_slope": 1.0}
    k = min(folds, n)
    raw_sq = 0.0
    recal_sq = 0.0
    scored = 0
    slopes: list[float] = []
    for fold in range(k):
        test = [data[i] for i in range(n) if i % k == fold]
        train = [data[i] for i in range(n) if i % k != fold]
        if not test or not train:
            continue
        a, b = fit_platt(train, min_n=1)
        slopes.append(a)
        for p, o in test:
            y = 1.0 if o else 0.0
            raw_sq += (float(p) - y) ** 2
            recal_sq += (apply_recalibration(float(p), a, b) - y) ** 2
            scored += 1
    if scored == 0:
        return {"raw_brier": 0.0, "recal_brier": 0.0, "delta": 0.0, "mean_slope": 1.0}
    raw_brier = raw_sq / scored
    recal_brier = recal_sq / scored
    return {
        "raw_brier": raw_brier,
        "recal_brier": recal_brier,
        "delta": recal_brier - raw_brier,
        "mean_slope": sum(slopes) / len(slopes) if slopes else 1.0,
    }


def load_recalibration(path: str | Path) -> tuple[float, float]:
    """Read fitted ``(a, b)`` from the params JSON written by ``calibrate-fit``. Returns the
    identity map ``(1.0, 0.0)`` on a missing or malformed file, so a deployment with no params
    file behaves exactly as if recalibration were absent (ships inert)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return (float(data["a"]), float(data["b"]))
    except (OSError, ValueError, KeyError, TypeError):
        return (1.0, 0.0)


def extremize_logodds(p: float, d: float = math.sqrt(3)) -> float:
    """The AIA Forecaster's data-free extremizing fallback: ``sigmoid(d*logit(p))``, pushing
    probabilities AWAY from 0.5 (``d=sqrt(3)`` is their published default). Implemented but
    deliberately NOT wired as a default: our pastcast shows the OPPOSITE sign (we are
    overconfident and want shrinking, a<1), so this is an A/B candidate only, never shipped."""
    return _sigmoid(d * _logit(p))


# --------------------------------------------------------------------------- aggregation


def clamp(p: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, p))


def _check_draws(draws: list[float]) -> None:
    if not draws:
        raise ValueError("draws must be non-empty")
    bad = [d for d in draws if not 0.0 <= d <= 1.0]
    if bad:
        raise ValueError(f"draws must be probabilities in [0, 1], got {bad}")


def trimmed_mean(draws: list[float]) -> float:
    """Mean after dropping the single lowest and highest draw (when n >= 4).

    The right pool for *correlated* draws — multiple runs of the same forecaster/model —
    where trimming, not extremizing, is the correct robustification (Halawi et al. 2024;
    self-ensembles share their information, so extremizing double-counts)."""
    _check_draws(draws)
    if len(draws) >= 4:
        draws = sorted(draws)[1:-1]
    return sum(draws) / len(draws)


def geo_mean_odds(draws: list[float], *, drop_extremes: bool = False) -> float:
    """Geometric mean of odds — the right pool for *independent* forecasters (different
    models/agents with genuinely different information). Inputs are nudged away from 0/1
    to avoid degenerate odds.

    ``drop_extremes`` (opt-in, off since v0.4.0) removes the single most extreme forecast
    on each end at n >= 4 — Samotsvety's rule, calibrated on ~7 humans with genuinely
    diverse information. A rank-symmetric trim is logit-asymmetric near the boundary: on
    one-sided pools it moves the pool TOWARD the extreme ([0.03, 0.03, 0.05, 0.12] pools
    to 0.049 untrimmed but 0.039 trimmed) and at n=4 it keeps only the middle two draws —
    deleting the dissenting lens exactly where issue #10 found over-commitment. Use
    ``median`` for a contaminated pool instead of trimming healthy ones."""
    _check_draws(draws)
    eps = 1e-4
    ps = [clamp(p, eps, 1.0 - eps) for p in draws]
    if drop_extremes and len(ps) >= 4:
        ps = sorted(ps)[1:-1]
    log_odds_sum = sum(math.log(p / (1.0 - p)) for p in ps)
    pooled = math.exp(log_odds_sum / len(ps))
    return pooled / (1.0 + pooled)


def median(draws: list[float]) -> float:
    _check_draws(draws)
    return float(statistics.median(draws))


# Annotated explicitly: geo_mean_odds carries an extra keyword-only parameter, so without
# this mypy joins the three signatures into a type it refuses to call at the lookup below.
# Every method is invoked purely positionally as (draws) -> float, which is this type.
AGGREGATION_METHODS: dict[str, Callable[[list[float]], float]] = {
    "trimmed_mean": trimmed_mean,
    "geo_mean_odds": geo_mean_odds,
    "median": median,
}


def blend_with_crowd(p: float, crowd: float, *, crowd_weight: float = 0.5) -> float:
    """Blend an aggregate with an existing crowd/market number. A simple average beat both
    the system and the crowd alone in the published evidence — treat the crowd as a strong
    anchor, not an opponent."""
    if not 0.0 <= crowd_weight <= 1.0:
        raise ValueError("crowd_weight must be in [0, 1]")
    return crowd_weight * crowd + (1.0 - crowd_weight) * p


def aggregate_binary(
    draws: list[float],
    *,
    method: str = "trimmed_mean",
    crowd: float | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[float, str]:
    """Pool draws -> optionally blend with the crowd -> clamp. Returns (p, description)."""
    cfg = config or DEFAULTS
    if method not in AGGREGATION_METHODS:
        raise ValueError(f"method must be one of {sorted(AGGREGATION_METHODS)}")
    pooled = AGGREGATION_METHODS[method](draws)
    desc = f"{method}(n={len(draws)})"
    if crowd is not None:
        weight = float(cfg["blend"]["crowd_weight"])
        pooled = blend_with_crowd(pooled, crowd, crowd_weight=weight)
        desc += f" blended with crowd={crowd} (weight {weight})"
    lo, hi = float(cfg["clamp"]["min"]), float(cfg["clamp"]["max"])
    clamped = clamp(pooled, lo, hi)
    if clamped != pooled:
        desc += f" clamped to [{lo}, {hi}]"
    return clamped, desc


# ------------------------------------------------------- pooling: non-binary question types

#: The percentile keys pooling is defined over. ``validate_percentiles`` requires exactly
#: these five in every continuous payload, so they are the only keys guaranteed present in
#: EVERY run; a run's extra keys (e.g. "5") are dropped by the pool rather than averaged
#: against runs that never declared them.
POOL_PERCENTILE_KEYS = ("10", "25", "50", "75", "90")


def pool_percentiles(
    runs: list[dict[str, float]], *, zero_point: float | None = None
) -> dict[str, float]:
    """Quantile-average ("Vincentize") several independent runs' percentiles into one set.

    Per-key arithmetic mean across runs. This is the SHAPE-PRESERVING pool: the result's
    interval is the average of the members' intervals, recentred on their average location,
    so three unimodal runs pool to one unimodal distribution. Be precise about what it does
    and does not do — three runs that differ only in LOCATION pool to the same width they
    each declared, not a wider one; only members with genuinely different spreads (or
    different escape masses) move the width. The alternative, averaging the CDFs vertically
    (a linear opinion pool), does add the between-run variance to the width, at the cost of
    a lumpy multi-modal mixture. Vincentization is what the harness preregistered; every
    pooled record journals the research run's own percentiles as the single-run
    counterfactual, so which one is right becomes a measurement rather than an argument.

    ``zero_point`` mirrors the question's scaling (``_scale_location``): when it is set the
    question's own axis is logarithmic, so the mean is taken over ``log|x - zero_point|`` and
    mapped back. Averaging 10 and 1000 linearly gives 505 — a decade from the geometric
    center the platform's scale implies — so a linear mean on a log question would move the
    pool almost as far as the disagreement it is pooling. Every value must sit strictly on
    ONE side of the zero point (the platform guarantees this: a valid zero_point lies outside
    the question's range).

    The result is strictly increasing: the mean of strictly increasing sequences is strictly
    increasing in exact arithmetic, so any inversion here is float noise — repaired by
    sorting the pooled values ascending and nudging ties apart, never by raising.
    """
    if not runs:
        raise ValueError("runs must be non-empty")
    values: dict[str, list[float]] = {}
    for i, run in enumerate(runs):
        for key in POOL_PERCENTILE_KEYS:
            if key not in run:
                raise ValueError(f"run {i} is missing percentile key {key!r}")
            values.setdefault(key, []).append(float(run[key]))

    def mean_of(key: str) -> float:
        column = values[key]
        if zero_point is None:
            return sum(column) / len(column)
        shifted = [v - zero_point for v in column]
        if all(d > 0 for d in shifted):
            sign = 1.0
        elif all(d < 0 for d in shifted):
            sign = -1.0
        else:
            raise ValueError(
                f"percentile p{key} straddles zero_point={zero_point}; a log-scaled "
                "question's zero point must lie outside every declared value"
            )
        logs = [math.log(abs(d)) for d in shifted]
        return zero_point + sign * math.exp(sum(logs) / len(logs))

    pooled = [mean_of(key) for key in POOL_PERCENTILE_KEYS]
    if any(a >= b for a, b in zip(pooled, pooled[1:], strict=False)):
        pooled.sort()
        for i in range(1, len(pooled)):
            if pooled[i] <= pooled[i - 1]:
                pooled[i] = math.nextafter(pooled[i - 1], math.inf)
    return dict(zip(POOL_PERCENTILE_KEYS, pooled, strict=True))


def pool_escape_mass(values: list[float | None]) -> float | None:
    """Arithmetic mean of the declared out-of-bound masses, ignoring the runs that declared
    nothing; ``None`` when no run declared any.

    Not a mean over ALL runs on purpose: a run that omits the field has said nothing about
    the tail (the contract asks for it only when the run can name a mechanism), not that the
    tail is zero — averaging a silence in as a 0 would let one silent run halve a declared
    escape and reintroduce exactly the undeclared-tail failure the field exists to fix."""
    present = [float(v) for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def pool_mc(runs: list[dict[str, float]]) -> dict[str, float]:
    """Per-option geometric mean over runs, renormalized to sum to 1.

    The multiple-choice analogue of ``geo_mean_odds`` for independent forecasters: it pools
    in log space, so an option one run has all but ruled out cannot be dragged up by a
    single confident dissenter the way an arithmetic mean would. Options are the keys of the
    first run; every run must offer the same option set (a run that renamed a label is a
    contract violation, not something to silently pool over)."""
    if not runs:
        raise ValueError("runs must be non-empty")
    options = list(runs[0])
    if not options:
        raise ValueError("runs must carry at least one option")
    for i, run in enumerate(runs[1:], start=1):
        if set(run) != set(options):
            raise ValueError(
                f"run {i} has options {sorted(run)}, expected {sorted(options)} — "
                "every run must use the same exact option labels"
            )
    eps = 1e-6  # keeps log finite on an option a run priced at exactly 0
    pooled = {
        option: math.exp(
            sum(math.log(max(float(run[option]), eps)) for run in runs) / len(runs)
        )
        for option in options
    }
    total = sum(pooled.values())
    return {option: value / total for option, value in pooled.items()}


# --------------------------------------------------------------------------- validation


def validate_probability(p: float, config: dict[str, Any] | None = None) -> list[str]:
    """Warnings (never blocking) that flag the measured LLM forecasting failure modes."""
    cfg = config or DEFAULTS
    warnings: list[str] = []
    lo, hi = float(cfg["clamp"]["min"]), float(cfg["clamp"]["max"])
    if p < lo or p > hi:
        warnings.append(
            f"p={p} is outside the default clamp [{lo}, {hi}] — keep it only if the "
            "evidence is genuinely overwhelming and documented in the reasoning"
        )
    if abs(p - 0.5) < 1e-9:
        warnings.append(
            "50% is a positive claim of perfectly balanced evidence — justify it "
            "explicitly or go find the base rate"
        )
    elif abs(p * 100 - round(p * 100)) < 1e-9 and round(p * 100) % 5 == 0:
        warnings.append(
            f"p={p} sits on the 5%-grid (round-number bias) — state why the last digit "
            "is what it is, at 1% granularity"
        )
    return warnings


def validate_percentiles(percentiles: dict[str, float]) -> list[str]:
    """Errors for a numeric forecast's declared percentiles: required keys present, every
    key a percentile in (0, 100), and the FULL set strictly monotone (extra keys count —
    they are consumed downstream by the CDF construction)."""
    errors: list[str] = []
    required = ["10", "25", "50", "75", "90"]
    missing = [k for k in required if k not in percentiles]
    if missing:
        errors.append(f"missing percentile keys: {missing} (need {required})")
        return errors
    parsed: list[tuple[float, str, float]] = []
    for key, value in percentiles.items():
        try:
            fraction = float(key)
        except ValueError:
            errors.append(f"percentile key {key!r} is not a number")
            continue
        if not 0.0 < fraction < 100.0:
            errors.append(f"percentile key {key!r} must be in (0, 100)")
            continue
        parsed.append((fraction, key, value))
    parsed.sort()
    for (fa, ka, a), (fb, kb, b) in zip(parsed, parsed[1:], strict=False):
        if fa == fb:
            # Distinct string keys that float to the same percentile (e.g. "50" and "50.0")
            # both land in the dict but collapse to one point; which value the CDF
            # construction consumes is accidental, so this is a repairable error, not a pass.
            errors.append(
                f"percentile keys {ka!r} and {kb!r} both normalize to {fa} — "
                "duplicate percentile; use one key"
            )
        elif a >= b:
            errors.append(f"percentiles must be strictly increasing: p{ka}={a} >= p{kb}={b}")
    return errors


#: Ceiling on each declared escape mass, and on the two together. A forecaster who believes
#: more than half the mass sits outside the range has the wrong range, not a tail — and past
#: 0.6 combined there is barely an in-range distribution left to elicit. Both are guard rails
#: against a fat-finger 0.9, not calibration advice.
MAX_ESCAPE_MASS = 0.5
MAX_TOTAL_ESCAPE_MASS = 0.6


def validate_escape_mass(
    p_below_lower: float | None,
    p_above_upper: float | None,
    *,
    lower_open: bool,
    upper_open: bool,
) -> list[str]:
    """Errors for the declared out-of-bound mass on a numeric/discrete/date forecast.

    The two fields are unconditional probabilities that the outcome escapes the question's
    range entirely (regime break, collapse, redenomination). They are meaningful ONLY where
    the platform left that bound open: past a CLOSED bound the outcome cannot land, so a
    declaration there is a misread of the question, not a bold tail — an error, never a
    silent zero. See ``percentiles_to_cdf`` for how the mass reaches the submitted CDF.
    """
    errors: list[str] = []
    for name, value, is_open, bound in (
        ("p_below_lower", p_below_lower, lower_open, "lower"),
        ("p_above_upper", p_above_upper, upper_open, "upper"),
    ):
        if value is None:
            continue
        if not isinstance(value, int | float) or isinstance(value, bool):
            errors.append(f"{name} must be a number, got {value!r}")
            continue
        if not is_open:
            errors.append(
                f"{name} is only valid when the {bound} bound is OPEN — this question's "
                f"{bound} bound is closed, so no outcome can land past it"
            )
        if not 0.0 <= float(value) <= MAX_ESCAPE_MASS:
            errors.append(f"{name}={value} must be in [0, {MAX_ESCAPE_MASS}]")
    if errors:
        return errors
    total = float(p_below_lower or 0.0) + float(p_above_upper or 0.0)
    if total > MAX_TOTAL_ESCAPE_MASS:
        errors.append(
            f"p_below_lower + p_above_upper = {total:.3f} exceeds {MAX_TOTAL_ESCAPE_MASS} — "
            "that much escape mass means the range is wrong, not the tails"
        )
    return errors


def validate_mc(options: list[str], probabilities: list[float]) -> list[str]:
    errors: list[str] = []
    if len(options) != len(probabilities):
        errors.append(f"{len(options)} options but {len(probabilities)} probabilities")
        return errors
    if len(options) < 2:
        errors.append("multiple_choice needs at least 2 options")
    if any(p < 0.0 or p > 1.0 for p in probabilities):
        errors.append("every probability must be in [0, 1]")
    total = sum(probabilities)
    if abs(total - 1.0) > 0.01:
        errors.append(f"probabilities sum to {total:.3f}, must sum to 1 (±0.01) — renormalize")
    return errors


def validate_record(
    record: ForecastRecord, config: dict[str, Any] | None = None
) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings). Errors block recording; warnings are printed."""
    errors: list[str] = []
    warnings: list[str] = []
    if record.status == "open":
        if not record.resolution_criterion:
            errors.append("an open forecast needs a resolution_criterion (what counts, exactly?)")
        if not record.resolve_by:
            errors.append("an open forecast needs a resolve_by date")
    if record.resolve_by is not None and not _is_iso_date(record.resolve_by):
        errors.append(
            f"resolve_by must be an ISO date (YYYY-MM-DD), got {record.resolve_by!r} — "
            "anything else silently never comes due"
        )
    if record.question_type == "binary":
        if record.status == "open" and record.probability is None:
            errors.append("a binary forecast needs a probability")
        if record.probability is not None:
            warnings.extend(validate_probability(record.probability, config))
    elif record.question_type == "multiple_choice":
        if record.options is None or record.probabilities is None:
            errors.append("multiple_choice needs parallel options and probabilities lists")
        else:
            errors.extend(validate_mc(record.options, record.probabilities))
    elif record.question_type in ("numeric", "discrete", "date"):
        if record.percentiles is None:
            errors.append(f"{record.question_type} needs percentiles {{10,25,50,75,90}}")
        else:
            errors.extend(validate_percentiles(record.percentiles))
        # Which bounds are open lives in ``scaling`` (written by whatever built the CDF). With
        # no scaling there is nothing to contradict, so only the range/sum checks can run —
        # assume open rather than invent a rejection the recorder cannot act on.
        scaling = record.scaling or {}
        errors.extend(
            validate_escape_mass(
                record.p_below_lower,
                record.p_above_upper,
                lower_open=bool(scaling.get("lower_open", True)),
                upper_open=bool(scaling.get("upper_open", True)),
            )
        )
    if record.question_type not in ("numeric", "discrete", "date") and (
        record.p_below_lower is not None or record.p_above_upper is not None
    ):
        errors.append(
            "p_below_lower / p_above_upper describe a numeric range and are meaningless on a "
            f"{record.question_type} forecast"
        )
    if not record.reference_class and record.status == "open":
        warnings.append("no reference_class — the outside view is the most valuable single step")
    return errors, warnings


# --------------------------------------------------------------- numeric CDF (Metaculus-style)
# The percentile->CDF construction and standardization below are ported (rewritten in pure
# stdlib) from the forecasting-tools project (github.com/Metaculus/forecasting-tools,
# forecasting_tools/data_models/numeric_report.py), Copyright (c) 2024 CodexVeritas,
# released under the MIT License; the full permission notice is in that repository's
# LICENSE file. The 0.01*location mixing term guarantees the platform's minimum CDF step
# (5e-05 per bin over 200 bins) and the 0.001 constant provides the open-bound tail mass.

DEFAULT_CDF_SIZE = 201
MAX_PMF_VALUE = 0.2
MIN_CDF_STEP = 5e-05


def _scale_location(
    value: float, range_min: float, range_max: float, zero_point: float | None
) -> float:
    """Map a nominal value onto [0, 1]: linear, or log-scaled when a zero_point is set."""
    if zero_point is None:
        return (value - range_min) / (range_max - range_min)
    deriv_ratio = (range_max - zero_point) / (range_min - zero_point)
    return (
        math.log((value - range_min) * (deriv_ratio - 1) + (range_max - range_min))
        - math.log(range_max - range_min)
    ) / math.log(deriv_ratio)


def _cap_pmf(pmf: list[float], cap: float, total: float) -> list[float]:
    """Scale the PMF so no bin exceeds ``cap`` while the mass still sums to ``total``:
    binary-search the scale factor s in sum(min(p*s, cap)) = total."""
    if all(p <= cap for p in pmf):
        return pmf
    lo, hi = 1.0, 2.0
    while sum(min(p * hi, cap) for p in pmf) < total and hi < 1e9:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if sum(min(p * mid, cap) for p in pmf) < total:
            lo = mid
        else:
            hi = mid
    return [min(p * hi, cap) for p in pmf]


#: Absolute mass the standardization spreads uniformly over the location axis so that every
#: consecutive CDF step clears ``MIN_CDF_STEP``: 0.01 / 200 bins = 5e-05 exactly, at the
#: platform's 201 points. It is the ``0.01 * x`` term of the inherited
#: ``0.988*r + 0.01*x + 0.001`` mixture, pulled out so the declared-escape-mass path can
#: reuse it against an interior span that is no longer ~0.99.
CDF_UNIFORM_MIX = 0.01

#: The platform's floor on the mass outside an open bound. A CDF that declares less is
#: rejected, so this is also the mass an undeclared open tail silently gets.
MIN_OPEN_TAIL = 0.001


def _pchip_slopes(xs: list[float], ys: list[float]) -> list[float]:
    """Fritsch-Carlson monotone tangents at each knot of an increasing-x point set.

    Interior tangents are the weighted harmonic mean of adjacent secant slopes (zero at a
    local flat/extremum), endpoint tangents the one-sided three-point estimate clamped the
    way SciPy's PCHIP clamps it. With monotone ys the resulting cubic Hermite interpolant
    is monotone — which is what lets ``percentiles_to_cdf`` use it on a CDF.
    """
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    delta = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            m[i] = 0.0
        else:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])

    def endpoint(h0: float, h1: float, d0: float, d1: float) -> float:
        d = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if d * d0 <= 0:
            return 0.0
        if d1 * d0 <= 0 and abs(d) > 3 * abs(d0):
            return 3 * d0
        return d

    if n == 2:
        m[0] = m[1] = delta[0]
    else:
        m[0] = endpoint(h[0], h[1], delta[0], delta[1])
        m[-1] = endpoint(h[-2], h[-3] if n > 3 else h[-2], delta[-1], delta[-2])
    return m


def _pchip_eval(xs: list[float], ys: list[float], grid: list[float]) -> list[float]:
    """Values of the monotone cubic Hermite (PCHIP) interpolant at ``grid`` locations,
    which must lie within [xs[0], xs[-1]]. Pure stdlib; ~exact match to SciPy PchipInterpolator."""
    m = _pchip_slopes(xs, ys)
    out: list[float] = []
    seg = 0
    for x in grid:
        while seg < len(xs) - 2 and xs[seg + 1] < x:
            seg += 1
        h = xs[seg + 1] - xs[seg]
        t = (x - xs[seg]) / h
        h00 = (1 + 2 * t) * (1 - t) ** 2
        h10 = t * (1 - t) ** 2
        h01 = t * t * (3 - 2 * t)
        h11 = t * t * (t - 1)
        out.append(
            h00 * ys[seg] + h10 * h * m[seg] + h01 * ys[seg + 1] + h11 * h * m[seg + 1]
        )
    return out


def percentiles_to_cdf(
    percentiles: dict[str, float],
    range_min: float,
    range_max: float,
    *,
    lower_open: bool = False,
    upper_open: bool = False,
    zero_point: float | None = None,
    cdf_size: int = DEFAULT_CDF_SIZE,
    p_below_lower: float | None = None,
    p_above_upper: float | None = None,
    interpolation: str = "linear",
) -> list[float]:
    """Declared percentiles -> a platform-valid CDF evaluated at ``cdf_size`` equally spaced
    locations: monotone, min step, capped per-bin mass, correct open/closed-bound tails.

    ``interpolation`` (v0.4.25) selects how the cumulative fraction is interpolated between
    the anchors: ``"linear"`` (default — bit-for-bit the historical construction, whose PDF
    is a staircase: flat slab between adjacent percentiles and flat thin slabs from p10/p90
    to the bounds) or ``"pchip"`` (monotone cubic Hermite through the SAME anchors — a C1
    CDF, i.e. a continuous PDF, peaked in the interior with tails that decay toward the
    bound anchors; declared quantiles are preserved exactly). Production submits pchip:
    backtested on the 69 resolved MiniBench numerics of the 2026-07-13/-27/-08-10 waves it
    scored +2.8/question over linear (paired CI90 [+0.3, +5.3], positive in every wave;
    ``bench/analysis/minibench_smooth_cdf.py``), mostly by no longer starving "near miss"
    outcomes just past p90/p10 of density the way a to-the-bound flat slab does. The
    library default stays ``"linear"`` so that rebuilding a historical journal row
    reproduces the CDF that was actually submitted.

    Declared values must be strictly increasing and strictly inside (range_min, range_max);
    a log-scaled question's ``zero_point`` must lie outside [range_min, range_max].

    ``p_below_lower`` / ``p_above_upper`` (v0.4.23, optional) are the forecaster's declared
    UNCONDITIONAL probability that the outcome escapes the range past an open bound; passing
    one leaves the other at 0.0. When either is given, the five percentiles are read as the
    distribution CONDITIONAL on landing inside the range: fraction ``f`` is placed at
    ``p_below + f*(1 - p_below - p_above)``, the endpoints carry the declared masses, and the
    standardization preserves them instead of normalizing them away.

    Why the option exists: the 2026-07-27 MiniBench wave lost two questions (BMEX market cap
    q45012, England bluetongue q44967) to outcomes that landed OUTSIDE the range. The
    strictly-inside-bounds contract had no way to say so — that run's own journal reads "I
    would place ~12-15% below the $100k range floor, which the strictly-inside-bounds format
    compresses into the 10th percentile at $102k" — and the standardization below pins an
    undeclared open tail at ``MIN_OPEN_TAIL``, so both scored exactly 50*ln(0.001/0.05) =
    -195.6, the worst payable value. With ``p_below_lower=0.13`` the same forecast scores
    50*ln(0.13/0.05) = +47.8.

    With BOTH new arguments omitted the construction is bit-for-bit what it was before
    v0.4.23, including this inherited distortion: an open bound assumes half the outermost
    declared decile lies outside the range, the standardization then rescales the interpolated
    curve onto the ``MIN_OPEN_TAIL`` tails, and with exactly ONE open bound the interior
    percentiles shift by up to ~2.6pp (the declared median sits at ~0.475 with only the lower
    bound open). That matches the upstream reference implementation and platform rules.

    Pure and total: every infeasible combination raises ``ValueError`` rather than returning
    a CDF the platform would reject.
    """
    errors = validate_percentiles(percentiles)
    if errors:
        raise ValueError("; ".join(errors))
    errors = validate_escape_mass(
        p_below_lower, p_above_upper, lower_open=lower_open, upper_open=upper_open
    )
    if errors:
        raise ValueError("; ".join(errors))
    if range_min >= range_max:
        raise ValueError("range_min must be < range_max")
    if cdf_size < 3:
        raise ValueError("cdf_size must be >= 3")
    if interpolation not in ("linear", "pchip"):
        raise ValueError(f'interpolation must be "linear" or "pchip", got {interpolation!r}')
    if zero_point is not None and range_min <= zero_point <= range_max:
        raise ValueError(
            f"zero_point ({zero_point}) must lie outside [range_min, range_max] — "
            "inside the range the log scaling is undefined"
        )

    declared = sorted((float(k) / 100.0, v) for k, v in percentiles.items())
    if not all(range_min < v < range_max for _, v in declared):
        raise ValueError(
            "declared percentile values must lie strictly inside (range_min, range_max)"
        )

    escape_declared = p_below_lower is not None or p_above_upper is not None
    p_below = float(p_below_lower or 0.0)
    p_above = float(p_above_upper or 0.0)

    # (location in [0,1], cumulative fraction) anchors, incl. the bound anchors.
    locs = [clamp(_scale_location(v, range_min, range_max, zero_point), 0.0, 1.0)
            for _, v in declared]
    if escape_declared:
        # Declared percentiles are conditional on landing inside: stretch them onto the
        # interior span [p_below, 1 - p_above] and pin the bound anchors on the escape mass.
        inside = 1.0 - p_below - p_above
        points = [(loc, p_below + f * inside) for loc, (f, _) in zip(locs, declared, strict=True)]
        lower_frac, upper_frac = p_below, 1.0 - p_above
    else:
        points = [(loc, f) for loc, (f, _) in zip(locs, declared, strict=True)]
        min_frac, max_frac = declared[0][0], declared[-1][0]
        lower_frac = 0.5 * min_frac if lower_open else 0.0
        upper_frac = 1.0 - 0.5 * (1.0 - max_frac) if upper_open else 1.0
    anchors = [(0.0, lower_frac), *points, (1.0, upper_frac)]
    for (loc_a, _), (loc_b, _) in zip(anchors, anchors[1:], strict=False):
        if loc_a >= loc_b:
            raise ValueError("scaled percentile locations must be strictly increasing")

    # Interpolate the cumulative fraction at each evaluation location: piecewise-linear
    # (historical), or monotone cubic through the same anchors (smooth PDF, v0.4.25).
    locations = [i / (cdf_size - 1) for i in range(cdf_size)]
    if interpolation == "pchip":
        raw = _pchip_eval([a[0] for a in anchors], [a[1] for a in anchors], locations)
    else:
        raw = []
        seg = 0
        for x in locations:
            while seg < len(anchors) - 2 and anchors[seg + 1][0] < x:
                seg += 1
            (x0, y0), (x1, y1) = anchors[seg], anchors[seg + 1]
            raw.append(y0 + (y1 - y0) * (x - x0) / (x1 - x0))

    # Standardize: rescale to [0,1], then mix in the location term that enforces the minimum
    # step, plus the 0.001 tail offset for each open bound.
    span = raw[-1] - raw[0]
    rescaled = [(y - raw[0]) / span for y in raw]
    if escape_declared:
        # Same mixture, generalized: cdf = start + (span - u)*shape + u*location, where the
        # endpoints now honor the DECLARED escape mass (floored at the platform's minimum
        # open tail) instead of the fixed 0.001. Substituting p_below = p_above = 0.001 and
        # a full span reproduces the four literal branches below exactly; the uniform term
        # stays absolute (not scaled by the span) because that is what buys MIN_CDF_STEP.
        start = max(p_below, MIN_OPEN_TAIL) if lower_open else 0.0
        end = 1.0 - (max(p_above, MIN_OPEN_TAIL) if upper_open else 0.0)
        interior = end - start
        if interior <= CDF_UNIFORM_MIX:  # pragma: no cover - MAX_TOTAL_ESCAPE_MASS forbids it
            raise ValueError(
                f"declared escape mass leaves only {interior:.4f} of probability inside the "
                f"range — less than the {CDF_UNIFORM_MIX} the minimum-step term needs"
            )
        cdf = [start + (interior - CDF_UNIFORM_MIX) * r + CDF_UNIFORM_MIX * x
               for r, x in zip(rescaled, locations, strict=True)]
    elif lower_open and upper_open:
        cdf = [0.988 * r + 0.01 * x + 0.001 for r, x in zip(rescaled, locations, strict=True)]
    elif lower_open:
        cdf = [0.989 * r + 0.01 * x + 0.001 for r, x in zip(rescaled, locations, strict=True)]
    elif upper_open:
        cdf = [0.989 * r + 0.01 * x for r, x in zip(rescaled, locations, strict=True)]
    else:
        cdf = [0.99 * r + 0.01 * x for r, x in zip(rescaled, locations, strict=True)]

    # Cap per-bin mass and rebuild the CDF from the fixed endpoints.
    cap = min(1.0, MAX_PMF_VALUE * (200.0 / (cdf_size - 1)))
    pmf = [b - a for a, b in zip(cdf, cdf[1:], strict=False)]
    pmf = _cap_pmf(pmf, cap, cdf[-1] - cdf[0])
    out = [cdf[0]]
    for p in pmf:
        out.append(out[-1] + p)
    out = [round(v, 10) for v in out]

    problems = validate_cdf(out, lower_open=lower_open, upper_open=upper_open, cdf_size=cdf_size)
    if problems:  # pragma: no cover - construction guarantees validity
        raise ValueError("constructed CDF failed validation: " + "; ".join(problems))
    return out


def validate_cdf(
    cdf: list[float],
    *,
    lower_open: bool = False,
    upper_open: bool = False,
    cdf_size: int = DEFAULT_CDF_SIZE,
) -> list[str]:
    """Errors for a CDF against the platform constraints (length, monotone min step,
    per-bin mass cap, open/closed tail masses)."""
    errors: list[str] = []
    eps = 1e-9
    if len(cdf) != cdf_size:
        errors.append(f"CDF has {len(cdf)} points, expected {cdf_size}")
        return errors
    cap = min(1.0, MAX_PMF_VALUE * (200.0 / (cdf_size - 1)))
    for i, (a, b) in enumerate(zip(cdf, cdf[1:], strict=False)):
        step = b - a
        if step < MIN_CDF_STEP - eps:
            errors.append(f"step {step:.2e} at index {i} below minimum {MIN_CDF_STEP}")
            break
        if step > cap + eps:
            errors.append(f"bin mass {step:.4f} at index {i} above cap {cap}")
            break
    if lower_open:
        if cdf[0] < 0.001 - eps:
            errors.append(f"open lower bound needs cdf[0] >= 0.001, got {cdf[0]}")
    elif abs(cdf[0]) > eps:
        errors.append(f"closed lower bound needs cdf[0] == 0.0, got {cdf[0]}")
    if upper_open:
        if cdf[-1] > 0.999 + eps:
            errors.append(f"open upper bound needs cdf[-1] <= 0.999, got {cdf[-1]}")
    elif abs(cdf[-1] - 1.0) > eps:
        errors.append(f"closed upper bound needs cdf[-1] == 1.0, got {cdf[-1]}")
    return errors


# --------------------------------------------------------------------------- interop


def to_decision_record(record: ForecastRecord) -> dict[str, Any]:
    """Map a ForecastRecord onto the decision-journal ``DecisionRecord`` shape
    (``method="forecast"``, estimand metadata as ``assumptions`` provenance strings).
    The mapping table lives in docs/schema.md."""
    kind = {"binary": "probability", "multiple_choice": "probability"}.get(
        record.question_type, "magnitude"
    )
    assumptions = [f"estimand_kind: {kind}"]
    if record.reference_class:
        assumptions.append(f"reference_class: {record.reference_class}")
    if record.why_it_matters:
        assumptions.append(f"VOI: {record.why_it_matters}")
    if record.resolution_criterion:
        assumptions.append(f"resolves_when: {record.resolution_criterion}")
    if record.parent_id:
        assumptions.append(f"parent_decision: {record.parent_id}")
    if record.fast_proxy:
        assumptions.append("fast_proxy: true")

    if record.status == "annulled":
        assumptions.append("annulled: true")
    prediction: dict[str, Any] = {
        "expectation": record.question,
        "probability": record.probability,
        "resolve_by": record.resolve_by,
    }
    # Additive (v0.4.13): the binary `probability` slot is null for MC/numeric questions, so
    # exporting only it silently dropped the actual forecast. Carry the type-specific payload
    # too — existing binary exports are unchanged (these keys are simply absent).
    if record.options is not None and record.probabilities is not None:
        prediction["options"] = list(record.options)
        prediction["probabilities"] = list(record.probabilities)
    if record.percentiles is not None:
        prediction["percentiles"] = dict(record.percentiles)
    out: dict[str, Any] = {
        "title": record.question,
        "id": record.id,
        "created": record.created[:10],
        "status": "resolved" if record.status in ("resolved", "annulled") else "open",
        "method": "forecast",
        "needs_system2": False,
        "rationale": record.reasoning,
        "assumptions": assumptions,
        "what_would_change_my_mind": list(record.what_would_change_my_mind),
        "prediction": prediction,
    }
    if record.resolution is not None:
        outcome = record.resolution.get("outcome")
        out["resolution"] = {
            "what_happened": record.resolution.get("note", ""),
            "resolved_on": record.resolution.get("resolved_on"),
            "realized": outcome if isinstance(outcome, bool) else None,
        }
    return out


# --------------------------------------------------------------------------- CLI


def _parse_draws(raw: str) -> list[float]:
    return [float(x) for x in raw.replace(" ", "").split(",") if x]


def _parse_outcome(raw: str) -> bool | float | str:
    lowered = raw.strip().lower()
    if lowered in ("true", "yes", "y", "t", "1", "1.0"):
        return True
    if lowered in ("false", "no", "n", "f", "0", "0.0"):
        return False
    try:
        return float(raw)
    except ValueError:
        return raw


def _config_or_none(args: argparse.Namespace) -> dict[str, Any] | None:
    """Load config, or print the error and return None (caller exits 2)."""
    try:
        return load_config(args.config)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _journal_from(args: argparse.Namespace, config: dict[str, Any]) -> Journal:
    """--journal beats $FORECAST_JOURNAL beats the config's [journal] path."""
    path = args.journal or os.environ.get("FORECAST_JOURNAL") or config["journal"]["path"]
    return Journal(path)


def _cmd_record(args: argparse.Namespace) -> int:
    config = _config_or_none(args)
    if config is None:
        return 2
    try:
        if args.record_json:
            record = ForecastRecord.from_dict(json.loads(args.record_json))
        else:
            has_forecast = any(
                x is not None for x in (args.probability, args.probabilities, args.percentiles)
            )
            record = ForecastRecord(
                question=args.question or "",
                question_type=args.type,
                status=args.status,
                resolution_criterion=args.criterion or "",
                resolve_by=args.resolve_by,
                reference_class=args.reference_class or "",
                base_rate=args.base_rate,
                why_it_matters=args.why or "",
                parent_id=args.parent_id,
                fast_proxy=args.fast_proxy,
                probability=args.probability,
                options=args.options.split(",") if args.options else None,
                probabilities=_parse_draws(args.probabilities) if args.probabilities else None,
                percentiles=_parse_percentiles(args.percentiles) if args.percentiles else None,
                p_below_lower=args.p_below_lower,
                p_above_upper=args.p_above_upper,
                raw_draws=_parse_draws(args.draws) if args.draws else None,
                aggregation=args.aggregation,
                effort=args.effort,
                model=args.model or "",
                crowd=(
                    {"value": args.crowd_value, "source": args.crowd_source or "", "at": _utc_now()}
                    if args.crowd_value is not None
                    else None
                ),
                reasoning=args.reasoning or "",
                what_would_change_my_mind=args.changes_mind or [],
                forecast_at=_utc_now() if has_forecast else None,
            )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    errors, warnings = validate_record(record, config)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(record.to_json())
        return 0
    journal = _journal_from(args, config)
    journal.append(record)
    print(record.id)
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    config = _config_or_none(args)
    if config is None:
        return 2
    journal = _journal_from(args, config)
    if args.resolved_on and not _is_iso_date(args.resolved_on):
        print(f"error: --resolved-on must be YYYY-MM-DD, got {args.resolved_on!r}", file=sys.stderr)
        return 2
    try:
        if args.annul:
            found = journal.annul(args.id, note=args.note or "", overwrite=args.overwrite)
        else:
            if args.outcome is None:
                print("error: --outcome is required (or pass --annul)", file=sys.stderr)
                return 2
            found = journal.resolve(
                args.id,
                _parse_outcome(args.outcome),
                note=args.note or "",
                resolved_on=args.resolved_on,
                overwrite=args.overwrite,
            )
    except AlreadyResolved as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not found:
        print(f"error: no record with id {args.id!r}", file=sys.stderr)
        return 2
    print(f"{'annulled' if args.annul else 'resolved'} {args.id}")
    return 0


def _cmd_due(args: argparse.Namespace) -> int:
    config = _config_or_none(args)
    if config is None:
        return 2
    if args.today and not _is_iso_date(args.today):
        print(f"error: --today must be YYYY-MM-DD, got {args.today!r}", file=sys.stderr)
        return 2
    journal = _journal_from(args, config)
    due = journal.due(args.today)
    if args.json:
        print(json.dumps([r.to_dict() for r in due], ensure_ascii=False))
        return 0
    if not due:
        print("Nothing due.")
        return 0
    for r in due:
        p = f"p={r.probability}" if r.probability is not None else f"[{r.question_type}]"
        print(f"{r.id}  (resolve by {r.resolve_by}, {p})  {r.question}")
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    config = _config_or_none(args)
    if config is None:
        return 2
    journal = _journal_from(args, config)
    keys = tuple(k.strip() for k in args.by.split(",") if k.strip())
    try:
        grouped = score_by(journal.all(), keys)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(grouped.to_dict(), ensure_ascii=False) if args.json else grouped.summary())
    return 0


def _cmd_calibrate_fit(args: argparse.Namespace) -> int:
    """Fit post-hoc logistic recalibration from the journal's resolved binaries and, only
    if it improves out-of-sample, write the params. Reuses the exact resolved-binary
    extraction ``calibration_report`` uses (``ForecastRecord.scorable``)."""
    config = _config_or_none(args)
    if config is None:
        return 2
    journal = _journal_from(args, config)
    pairs: list[tuple[float, bool]] = []
    for r in journal.all():
        if r.scorable:
            assert r.probability is not None and r.resolution is not None
            pairs.append((r.probability, bool(r.resolution["outcome"])))
    n = len(pairs)
    out = Path(args.out)

    cv = recalibration_cv(pairs)
    print(f"Recalibration fit over N = {n} resolved binary forecast(s):")
    print(f"  raw Brier         : {cv['raw_brier']:.4f}")
    print(f"  recalibrated Brier: {cv['recal_brier']:.4f}  (5-fold out-of-sample)")
    print(f"  CV delta          : {cv['delta']:+.4f}  (negative = recalibration helps)")
    print(f"  mean fitted slope : {cv['mean_slope']:.3f}")

    if n < RECAL_MIN_N:
        print(f"  NOT emitting: N < RECAL_MIN_N ({n} < {RECAL_MIN_N}) — a 2-param fit needs "
              "more than the direction check's 5; too-small N overfits. Shipping inert.")
        return 0
    if cv["delta"] >= 0:
        print(f"  NOT emitting: out-of-sample recalibration did not beat identity "
              f"(delta {cv['delta']:+.4f} >= 0). Shipping inert.")
        return 0

    a, b = fit_platt(pairs)
    params = {
        "a": a,
        "b": b,
        "n": n,
        "cv_delta": cv["delta"],
        "fit_at": _utc_now(),
        "scaffold_version": SCAFFOLD_VERSION,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=2)
    if a < 1.0:
        interp = "a < 1: overconfident — shrinking probabilities toward 0.5"
    elif a > 1.0:
        interp = "a > 1: under-confident — stretching probabilities away from 0.5"
    else:
        interp = "a = 1: no slope adjustment"
    print(f"  fitted a = {a:.4f}, b = {b:+.4f}   ({interp})")
    print(f"  wrote {out}")
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    config = _config_or_none(args)
    if config is None:
        return 2
    method = args.method or str(config["aggregation"]["method"])
    try:
        draws = _parse_draws(args.draws)
        p, desc = aggregate_binary(draws, method=method, crowd=args.crowd, config=config)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"probability": p, "aggregation": desc}))
    else:
        print(f"{p:.4f}  ({desc})")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    config = _config_or_none(args)
    if config is None:
        return 2
    if args.record_json:
        try:
            record = ForecastRecord.from_dict(json.loads(args.record_json))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            print(f"error: invalid record JSON: {exc}", file=sys.stderr)
            return 2
        errors, warnings = validate_record(record, config)
    elif args.probability is not None:
        errors, warnings = [], validate_probability(args.probability, config)
    else:
        print("error: pass --probability or --record-json", file=sys.stderr)
        return 2
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    return 2 if errors else 0


def _cmd_config(args: argparse.Namespace) -> int:
    config = _config_or_none(args)
    if config is None:
        return 2
    print(json.dumps(config, indent=2))
    return 0


def _parse_percentiles(raw: str) -> dict[str, float]:
    """'10:5,25:8,50:12,75:20,90:35' -> {'10': 5.0, ...}"""
    out: dict[str, float] = {}
    for part in raw.split(","):
        key, _, value = part.strip().partition(":")
        out[key.strip()] = float(value)
    return out


def _cmd_cdf(args: argparse.Namespace) -> int:
    try:
        cdf = percentiles_to_cdf(
            _parse_percentiles(args.percentiles),
            args.range_min,
            args.range_max,
            lower_open=args.open_lower,
            upper_open=args.open_upper,
            zero_point=args.zero_point,
            cdf_size=args.size,
            p_below_lower=args.p_below_lower,
            p_above_upper=args.p_above_upper,
            interpolation=args.interpolation,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.p_below_lower is not None or args.p_above_upper is not None:
        # stderr, not stdout: stdout is the machine-readable CDF a caller pipes onward.
        print(
            f"tail mass in the built CDF: below range_min {cdf[0]:.4f}, "
            f"above range_max {1.0 - cdf[-1]:.4f}",
            file=sys.stderr,
        )
    print(json.dumps(cdf))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    config = _config_or_none(args)
    if config is None:
        return 2
    journal = _journal_from(args, config)
    for record in journal.all():
        print(json.dumps(to_decision_record(record), ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forecast-scaffold",
        description="Journal, scoring, aggregation, and validation for tracked forecasts.",
    )
    parser.add_argument("--config", help="path to a forecast.toml overriding defaults")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("record", help="validate and append a forecast to the journal")
    p.add_argument("--question")
    p.add_argument("--type", default="binary", choices=QUESTION_TYPES)
    p.add_argument("--status", default="open", choices=STATUSES)
    p.add_argument("--probability", type=float)
    p.add_argument("--options", help='multiple_choice: comma-separated labels, "A,B,C"')
    p.add_argument("--probabilities", help='multiple_choice: comma-separated, "0.5,0.3,0.2"')
    p.add_argument("--percentiles", help='numeric: "10:5,25:8,50:12,75:20,90:35"')
    p.add_argument(
        "--p-below-lower", dest="p_below_lower", type=float,
        help="numeric, open lower bound only: declared P(outcome < range_min)",
    )
    p.add_argument(
        "--p-above-upper", dest="p_above_upper", type=float,
        help="numeric, open upper bound only: declared P(outcome > range_max)",
    )
    p.add_argument(
        "--record-json", dest="record_json",
        help="a full record as one JSON object (alternative to the individual flags)",
    )
    p.add_argument("--resolve-by", dest="resolve_by")
    p.add_argument("--criterion", help="exact resolution criterion")
    p.add_argument("--reference-class", dest="reference_class")
    p.add_argument("--base-rate", dest="base_rate", type=float)
    p.add_argument("--why", help="VOI: which decision this moves")
    p.add_argument("--parent-id", dest="parent_id")
    p.add_argument("--fast-proxy", dest="fast_proxy", action="store_true")
    p.add_argument("--draws", help="comma-separated raw ensemble draws, e.g. 0.55,0.6,0.62")
    p.add_argument("--aggregation", help='e.g. "trimmed_mean(n=5)"')
    p.add_argument("--effort", help='"low" | "medium" | "high", add "(auto)" if auto-triaged')
    p.add_argument("--model")
    p.add_argument("--crowd-value", dest="crowd_value", type=float)
    p.add_argument("--crowd-source", dest="crowd_source")
    p.add_argument("--reasoning")
    p.add_argument("--changes-mind", dest="changes_mind", action="append")
    p.add_argument("--journal")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.set_defaults(func=_cmd_record)

    p = sub.add_parser("resolve", help="resolve (or annul) a recorded forecast")
    p.add_argument("--id", required=True)
    p.add_argument("--outcome", help="true/false for binary; a number or text otherwise")
    p.add_argument("--note")
    p.add_argument("--resolved-on", dest="resolved_on")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--annul", action="store_true", help="mark ambiguous/annulled (unscored)")
    p.add_argument("--journal")
    p.set_defaults(func=_cmd_resolve)

    p = sub.add_parser("due", help="list open forecasts past their resolve-by date")
    p.add_argument("--today", help="override today's date (ISO)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--journal")
    p.set_defaults(func=_cmd_due)

    p = sub.add_parser("score", help="Brier + calibration report over the journal, stratified")
    p.add_argument(
        "--by", default=",".join(DEFAULT_GROUP_KEYS),
        help=(
            "comma-separated group-by keys from "
            f"{{{','.join(GROUP_KEYS)}}} (default: %(default)s) — "
            "Brier scores are only comparable within matching methodology, so this anchors "
            "scoring to scaffold_version and blind/sighted even if you don't pass it"
        ),
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--journal")
    p.set_defaults(func=_cmd_score)

    p = sub.add_parser(
        "calibrate-fit",
        help="fit post-hoc logistic recalibration (Platt) from the journal's resolved history",
    )
    p.add_argument(
        "--out", default=str(Path("bot") / "journal" / "recalibration.json"),
        help="where to write the fitted params (default: %(default)s) — written only if the "
             "out-of-sample CV delta is negative and N >= RECAL_MIN_N",
    )
    p.add_argument("--journal")
    p.set_defaults(func=_cmd_calibrate_fit)

    p = sub.add_parser("aggregate", help="pool ensemble draws into one probability")
    p.add_argument("--draws", required=True, help="comma-separated, e.g. 0.55,0.6,0.62")
    p.add_argument(
        "--method", default=None, choices=sorted(AGGREGATION_METHODS),
        help="default: the [aggregation] method from config (trimmed_mean)",
    )
    p.add_argument("--crowd", type=float, help="crowd/market probability to blend with")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_aggregate)

    p = sub.add_parser("validate", help="check a probability or a full record JSON")
    p.add_argument("--probability", type=float)
    p.add_argument("--record-json", dest="record_json")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("config", help="print the effective configuration")
    p.set_defaults(func=_cmd_config)

    p = sub.add_parser("cdf", help="build a platform-valid CDF from declared percentiles")
    p.add_argument("--percentiles", required=True, help='"10:5,25:8,50:12,75:20,90:35"')
    p.add_argument("--min", dest="range_min", type=float, required=True)
    p.add_argument("--max", dest="range_max", type=float, required=True)
    p.add_argument("--open-lower", dest="open_lower", action="store_true")
    p.add_argument("--open-upper", dest="open_upper", action="store_true")
    p.add_argument("--zero-point", dest="zero_point", type=float)
    p.add_argument("--size", type=int, default=DEFAULT_CDF_SIZE)
    p.add_argument(
        "--p-below-lower", dest="p_below_lower", type=float,
        help="P(outcome escapes below range_min); open lower bound only. Declaring it makes "
             "the percentiles conditional on landing inside the range",
    )
    p.add_argument(
        "--p-above-upper", dest="p_above_upper", type=float,
        help="P(outcome escapes above range_max); open upper bound only",
    )
    p.add_argument(
        "--interpolation", choices=("pchip", "linear"), default="pchip",
        help="CDF interpolation between the declared percentiles. Default pchip (smooth "
             "monotone-cubic PDF, +2.8 pts/question over linear on 69 backtested MiniBench "
             "numerics); linear reproduces the pre-v0.4.25 staircase construction exactly",
    )
    p.set_defaults(func=_cmd_cdf)

    p = sub.add_parser("export", help="export the journal in an interop format")
    p.add_argument("--format", default="decision-record", choices=["decision-record"])
    p.add_argument("--journal")
    p.set_defaults(func=_cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
