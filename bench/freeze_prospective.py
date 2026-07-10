"""Freeze currently-open tournament questions into a preregistration set, resolve them later.

Why this exists (2026-07): the contamination probe (bench/contamination_probe.py, v0.4.7)
proved the newest models memorize recent outcomes past their stated cutoffs — claude-sonnet-5,
the live bot's model (trained Jan 2026), confidently recalls how pre-2026 questions resolved
with every tool stripped. That makes PASTCASTING a new model on an already-resolved question
invalid: you cannot separate scaffold skill from memorized outcome. The model's own weights are
the one leak bench/timevault.py cannot close, and no set-selection or tool discipline closes it
for a model whose training window already covers the resolution.

The clean evaluation path is PROSPECTIVE FREEZING. Snapshot questions that are OPEN today — their
outcomes do not exist yet, so no model, however recent, can have memorized them — then wait
(weeks) for them to resolve. Score any model later on the frozen snapshot with research
time-locked to the freeze instant via the timevault (bench/timevault.py takes an arbitrary
cutoff, so freezing the QUESTION SET is sufficient; the corpora are pulled from the Wayback
Machine at scoring time, hard-bounded at `frozen_at`). Freezing controls TOOL leakage; the weight
leak stays a set-selection duty — only evaluate a model whose training cutoff predates `frozen_at`.

The freeze file is a PREREGISTRATION ARTIFACT. Regenerating it — re-running `freeze` over the same
window and overwriting — would silently swap the question set that was committed to before the
outcomes were known, which is exactly what preregistration exists to prevent. So `freeze` refuses
to overwrite an existing file without --force, and `resolve` only ever fills the `resolution`
field (and an `annulled` flag), never touching a frozen field.

Two subcommands:

    # Snapshot today's open binary questions from the bot's tournaments (preregister once):
    python bench/freeze_prospective.py freeze
    python bench/freeze_prospective.py freeze --tournament summer-futureeval-2026,minibench \
        --out bench/sets/prospective-2026-07-10.jsonl

    # Weeks later, fill in outcomes for whatever has resolved (idempotent; re-run any time):
    python bench/freeze_prospective.py resolve bench/sets/prospective-2026-07-10.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

# ruff: noqa: E402  (import follows the sys.path bootstrap above)
from metaculus import MetaculusClient, MetaculusError

# The bot's live targets (bot.yml's TOURNAMENT_ID repo var): the seasonal FutureEval plus the
# biweekly MiniBench. --tournament overrides or extends this comma-separated list of slugs/ids.
DEFAULT_TOURNAMENTS = "summer-futureeval-2026,minibench"
SETS_DIR = ROOT / "bench" / "sets"
# A bot token never sees the HUMAN community prediction (Metaculus firewalls it everywhere off
# bot tournaments, and even inside them the value is a bots-only aggregate) — labelled honestly.
CROWD_SOURCE = "metaculus bot aggregate"


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_out_path(today: str | None = None) -> Path:
    """bench/sets/prospective-<YYYY-MM-DD>.jsonl for today (UTC)."""
    day = today or datetime.now(UTC).strftime("%Y-%m-%d")
    return SETS_DIR / f"prospective-{day}.jsonl"


def _build_row(
    post: dict[str, Any], question: dict[str, Any], frozen_at: str
) -> dict[str, Any]:
    """One frozen set-file row: the standard schema plus `frozen_at` and `resolution: null`.

    Field order is fixed here so the output is byte-stable and `resolve` can rewrite it in
    place. The criteria carry the fine print inline (the crowd — and the resolver — price it),
    and the Metaculus id/url are recorded so outcomes can be fetched back later.
    """
    qid = question.get("id")
    pid = post.get("id")
    title = str(question.get("title") or post.get("title") or "untitled")
    criteria = str(question.get("resolution_criteria") or "").strip()
    fine_print = str(question.get("fine_print") or "").strip()
    if fine_print:
        criteria = (criteria + f"\n\n## Fine print\n{fine_print}").strip()
    if not criteria:
        # Metaculus occasionally returns empty criteria; the title is the resolvable contract.
        criteria = f"(no criteria published) Resolves per the question as stated: {title}"
    resolve_by = str(
        question.get("scheduled_resolve_time")
        or question.get("scheduled_close_time")
        or ""
    )[:10] or None
    # Often null for a bot token — record it, never fail on it (task requirement).
    crowd_value = MetaculusClient.community_prediction(question)
    return {
        "id": f"metaculus:{qid}",
        "source": "metaculus",
        "url": f"https://www.metaculus.com/questions/{pid}/",
        "post_id": pid,
        "question_id": qid,
        "question": title,
        "background": str(question.get("description") or "")[:8000],
        "criteria": criteria,
        "resolve_by": resolve_by,
        "crowd": {"value": crowd_value, "at": frozen_at, "source": CROWD_SOURCE},
        "frozen_at": frozen_at,
        "resolution": None,
    }


def freeze(
    client: MetaculusClient,
    tournaments: str = DEFAULT_TOURNAMENTS,
    out_path: str | Path | None = None,
    *,
    force: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Snapshot every OPEN binary question across the tournament slugs into a frozen set file.

    Refuses to overwrite an existing file without ``force`` — a frozen set must never be
    silently regenerated (that would defeat preregistration). Rows are deduped by question id
    (a question cross-listed in two tournaments is frozen once) and sorted by id for a
    deterministic, diffable file. All rows share a single ``frozen_at`` instant.
    """
    out_path = Path(out_path) if out_path is not None else default_out_path()
    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} already exists — a frozen set is a preregistration artifact and must "
            f"not be silently regenerated; pass --force only if you truly mean to discard and "
            f"replace it"
        )
    frozen_at = _utc_now_iso()
    slugs = [s.strip() for s in str(tournaments).split(",") if s.strip()]
    seen_posts: set[Any] = set()
    rows_by_id: dict[str, dict[str, Any]] = {}
    skipped_nonbinary = skipped_nonopen = 0
    for slug in slugs:
        posts = client.open_posts(slug, limit=limit)
        new_posts = [p for p in posts if p.get("id") not in seen_posts]
        seen_posts.update(p.get("id") for p in posts)
        print(f"{len(posts)} open post(s) in {slug} ({len(new_posts)} new)")
        for post in new_posts:
            for question in MetaculusClient.questions_of(post):
                if question.get("type") != "binary":
                    skipped_nonbinary += 1
                    continue
                # Group posts open overall while a subquestion opens/closes on its own clock;
                # only freeze the ones genuinely open now. Missing status fails open (treated
                # open), matching open_posts' own statuses=open filter.
                status = str(question.get("status") or "open")
                if status != "open":
                    skipped_nonopen += 1
                    continue
                row = _build_row(post, question, frozen_at)
                rows_by_id.setdefault(row["id"], row)
    rows = sorted(rows_by_id.values(), key=lambda r: r["id"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"froze {len(rows)} open binary question(s) at {frozen_at} -> {out_path} "
        f"(skipped {skipped_nonbinary} non-binary, {skipped_nonopen} not-open)"
    )
    return {
        "frozen_at": frozen_at,
        "n_frozen": len(rows),
        "skipped_nonbinary": skipped_nonbinary,
        "skipped_nonopen": skipped_nonopen,
        "out": str(out_path),
    }


def _interpret_resolution(res_str: Any) -> tuple[int | None, bool]:
    """Map a Metaculus binary ``resolution`` string to (resolution, annulled).

    yes/no -> 1/0; annulled/ambiguous -> (None, annulled=True); anything else (null, empty,
    an as-yet-unresolved question) -> (None, False), i.e. still open, leave the row untouched.
    """
    text = str(res_str or "").strip().lower()
    if text == "yes":
        return 1, False
    if text == "no":
        return 0, False
    if text in ("annulled", "ambiguous"):
        return None, True
    return None, False


def _find_question(detail: dict[str, Any], qid: Any) -> dict[str, Any] | None:
    """The subquestion matching qid inside a fetched post (or the lone question of a
    single-question post when the id can't be matched)."""
    questions = MetaculusClient.questions_of(detail)
    for question in questions:
        if question.get("id") == qid:
            return question
    return questions[0] if len(questions) == 1 else None


def resolve(client: MetaculusClient, set_path: str | Path) -> dict[str, Any]:
    """Fill ``resolution`` (1/0) for questions now resolved YES/NO; flag annulled/ambiguous
    ones ``"annulled": true`` with resolution null; leave open ones exactly as they were.

    The file is rewritten in place, preserving every frozen field byte-for-byte on rows that
    did not change (unchanged rows keep their original line verbatim), so running twice changes
    nothing.
    """
    set_path = Path(set_path)
    raw_lines = [ln for ln in set_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    detail_cache: dict[Any, dict[str, Any]] = {}
    out_lines: list[str] = []
    n_resolved = n_open = n_annulled = n_errors = 0
    for raw in raw_lines:
        row = json.loads(raw)
        pid = row.get("post_id")
        qid = row.get("question_id")
        current = (row.get("resolution"), bool(row.get("annulled")))
        fetched_ok = True
        res_str: Any = None
        if pid is None:
            fetched_ok = False
        else:
            try:
                if pid not in detail_cache:
                    detail_cache[pid] = client.post_detail(pid)
                res_str = (_find_question(detail_cache[pid], qid) or {}).get("resolution")
            except MetaculusError as exc:
                fetched_ok = False
                print(f"  warn: could not fetch post {pid} ({exc}); "
                      f"leaving {row.get('id')} untouched")
        # A fetch failure leaves the row at its current state — the question is fine, the
        # network wasn't; hourly-safe, never destructive.
        new_res, new_annulled = (
            _interpret_resolution(res_str) if fetched_ok else current
        )
        if not fetched_ok:
            n_errors += 1
        if new_annulled:
            n_annulled += 1
        elif new_res in (0, 1):
            n_resolved += 1
        else:
            n_open += 1
        if (new_res, new_annulled) == current:
            out_lines.append(raw)  # byte-for-byte preserved
            continue
        row["resolution"] = new_res
        if new_annulled:
            row["annulled"] = True
        elif "annulled" in row:
            del row["annulled"]
        out_lines.append(json.dumps(row, ensure_ascii=False))
    set_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    summary = (f"{len(raw_lines)} frozen | {n_resolved} resolved | "
               f"{n_open} open | {n_annulled} annulled")
    if n_errors:
        summary += f" | {n_errors} fetch error(s)"
    print(summary)
    return {
        "n_frozen": len(raw_lines),
        "n_resolved": n_resolved,
        "n_open": n_open,
        "n_annulled": n_annulled,
        "n_errors": n_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_freeze = sub.add_parser("freeze", help="snapshot open binary questions into a frozen set")
    p_freeze.add_argument(
        "--tournament", default=DEFAULT_TOURNAMENTS,
        help="comma-separated tournament id(s)/slug(s) to freeze "
             f"(default: {DEFAULT_TOURNAMENTS})",
    )
    p_freeze.add_argument(
        "--out", default=None,
        help="output path (default: bench/sets/prospective-<today-UTC>.jsonl)",
    )
    p_freeze.add_argument(
        "--force", action="store_true",
        help="overwrite an existing frozen set (refused by default — a frozen set is a "
             "preregistration artifact and must not be silently regenerated)",
    )
    p_freeze.add_argument("--limit", type=int, default=50,
                          help="max open posts to fetch per tournament (default 50)")

    p_resolve = sub.add_parser("resolve", help="fill outcomes for questions that have resolved")
    p_resolve.add_argument("set_file", help="frozen set file to update in place")

    args = parser.parse_args(argv)
    client = MetaculusClient()
    if args.cmd == "freeze":
        try:
            freeze(client, args.tournament, args.out, force=args.force, limit=args.limit)
        except FileExistsError as exc:
            print(exc)
            return 1
        return 0
    resolve(client, args.set_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
