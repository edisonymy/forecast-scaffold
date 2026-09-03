"""Same-template priors: what happened the last time we forecast this question's template.

MiniBench regenerates the same question templates every wave ("What will Bitcoin's price be
on <date>?", "How many Starlink satellites will be in orbit on <date>?", the DDR5 spot
price, Foro Penal's prisoner count, the Nino 3.4 anomaly ...), and the seasonal tournament
repeats templates with a new deadline. For a recurring template the resolved prior
versions are the best reference class there is: the exact outcome, where it fell in OUR
submitted distribution (PIT), and how wide we were. The Spring 2026 bot-maker survey's
strongest research correlate was "checks similar questions" (r=+0.34); this is the local,
zero-cost, portable form of that move.

Everything here is RECORD-ONLY by construction: the section states facts (prior outcome,
our prior median and quartiles, the PIT) and never says which way to move the number.
The facts come from ``bot/journal/resolutions.jsonl`` (written by bench/sync_resolutions.py)
joined to the journal rows they resolved.

Matching is deliberately dumb and conservative: token-set Jaccard over titles with dates,
numbers, accents and stopwords stripped, same question type, threshold 0.4 (the LLM-written
MiniBench templates vary their wording wave to wave — "What will the closing price of
Bitcoin (BTC) be on ..." vs "What will Bitcoin's price be on ..." is 0.5; unrelated
same-entity questions such as Bitcoin ETF flows or BTC dominance sit at 0.14-0.25). A false
match costs a few prompt tokens; a missed match costs nothing.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

MONTHS = (  # noqa: SIM905
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec"
).split()
STOPWORDS = set(
    "a an the of on in at by for to from as be will what how many much which who whom whose "  # noqa: SIM905,E501
    "is are was were do does did its it this that these those and or with between before "
    "after until than per about over under above below during within into onto out up down "
    "off no not any all more most less least than there their they them his her he she we our "
    "us you your".split()
) | set(MONTHS)
DEFAULT_MIN_JACCARD = 0.4
DEFAULT_MAX_MATCHES = 3
QUANTILE_KEYS = ("10", "25", "50", "75", "90")


def normalize_title(title: str) -> frozenset[str]:
    """Token set of a question title with dates, numbers, ordinals and stopwords removed."""
    text = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"\d+(st|nd|rd|th)\b", " ", text)
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"[\d.,%$]+", " ", text)
    tokens = re.findall(r"[a-z][a-z'-]*", text)
    return frozenset(t for t in tokens if t not in STOPWORDS and len(t) > 1)


def _family(question_type: Any) -> str:
    """numeric/discrete/date share one family: the same template is regenerated as
    ``discrete`` one wave and ``numeric`` the next (the Nino-3.4 anomaly did exactly that),
    and the facts reported are identical for both."""
    return "continuous" if question_type in ("numeric", "discrete", "date") else str(question_type)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def resolved_history(overlay_rows: list[dict[str, Any]], journal_rows: list[dict[str, Any]],
                     ) -> list[dict[str, Any]]:
    """Join resolved overlay rows (latest per question id) to the journal row that forecast
    them. Returns one dict per resolved question with the fields the section needs."""
    latest: dict[int, dict[str, Any]] = {}
    for row in overlay_rows:
        try:
            latest[int(row["question_id"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    by_record: dict[str, dict[str, Any]] = {}
    by_qid: dict[int, dict[str, Any]] = {}
    for row in journal_rows:
        if row.get("dry_run"):
            continue
        if row.get("id"):
            by_record[str(row["id"])] = row
        src = row.get("source") or {}
        qid = src.get("question_id") if isinstance(src, dict) else None
        if qid is not None:
            prev = by_qid.get(int(qid))
            if prev is None or str(row.get("forecast_at")) > str(prev.get("forecast_at")):
                by_qid[int(qid)] = row
    out: list[dict[str, Any]] = []
    for qid, res in latest.items():
        if res.get("status") != "resolved" or res.get("annulled"):
            continue
        journal = by_record.get(str(res.get("record_id"))) or by_qid.get(qid)
        if journal is None:
            continue
        out.append({
            "question_id": qid,
            "title": str(res.get("title") or journal.get("question") or ""),
            "question_type": str(res.get("question_type") or journal.get("question_type") or ""),
            "outcome": res.get("outcome"),
            "resolution_raw": res.get("resolution_raw"),
            "resolved_at": str(res.get("resolved_at") or res.get("close_time") or "")[:10],
            "pit": res.get("pit"),
            "probability": journal.get("probability"),
            "percentiles": journal.get("percentiles"),
            "tokens": normalize_title(str(res.get("title") or journal.get("question") or "")),
        })
    return out


def similar_resolved(
    title: str,
    question_type: str,
    history: list[dict[str, Any]],
    *,
    min_jaccard: float = DEFAULT_MIN_JACCARD,
    max_matches: int = DEFAULT_MAX_MATCHES,
) -> list[tuple[float, dict[str, Any]]]:
    """Resolved same-type questions whose stripped title overlaps ``title`` at or above the
    threshold, best overlap first, most recent first on ties."""
    tokens = normalize_title(title)
    if not tokens:
        return []
    scored = []
    family = _family(question_type)
    for item in history:
        if _family(item.get("question_type")) != family:
            continue
        sim = jaccard(tokens, item["tokens"])
        if sim >= min_jaccard:
            scored.append((sim, item))
    scored.sort(key=lambda pair: pair[1].get("resolved_at") or "", reverse=True)
    scored.sort(key=lambda pair: pair[0], reverse=True)  # stable: recent-first on ties
    return scored[:max_matches]


def _fmt_num(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:.4g}"


def format_prior_facts(matches: list[tuple[float, dict[str, Any]]]) -> str:
    """Record-only section. Empty string when there is nothing to report."""
    if not matches:
        return ""
    # DATA-ONLY (operator, 2026-09-03): prior OUTCOMES only. Our own earlier submission
    # and where the outcome fell in it were removed — a one-sample "lesson" about our last
    # miss is self-correction bait, not a reference class.
    lines = [
        "## Prior resolved questions of the same template (record-only data)",
        "",
        "These are the platform outcomes of earlier questions with the same template. They",
        "are data about the template — facts to write down beside the rest of the research",
        "— never a directive about this question.",
    ]
    for _sim, item in matches:
        head = f"- {item['resolved_at'] or 'resolved'}: \"{item['title'][:110]}\""
        outcome = item.get("outcome")
        if item["question_type"] == "binary":
            res_s = "yes" if outcome is True else "no" if outcome is False else str(outcome)
            lines.append(f"{head} -> resolved {res_s}.")
        elif isinstance(outcome, (int, float)):
            lines.append(f"{head} -> resolved {_fmt_num(outcome)}.")
        else:
            lines.append(f"{head} -> resolved {outcome!r}.")
    return "\n".join(lines) + "\n"


def prior_facts_section(
    title: str,
    question_type: str,
    *,
    overlay_path: Path,
    journal_path: Path,
    exclude_question_id: int | None = None,
) -> str:
    """Convenience wrapper: load both files, join, match, format."""
    history = resolved_history(load_jsonl(overlay_path), load_jsonl(journal_path))
    if exclude_question_id is not None:
        history = [h for h in history if h["question_id"] != exclude_question_id]
    return format_prior_facts(similar_resolved(title, question_type, history))
