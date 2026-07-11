"""Offline converter: BTF-2 ``aux/scraped_pages.parquet`` -> a searchable SQLite index.

WHAT THE PARQUET ACTUALLY IS (verified against the real 483 MB file, 15,554,402 rows):
a three-column crawl **manifest**, not a text corpus. Every row is::

    question_id: str   # which BTF-2 question this URL was scraped for
    url:         str   # the page FutureSearch's SOTA agent scraped
    date_scraped:str   # ISO crawl timestamp, e.g. "2025-10-22T21:30:11.912205"

There is NO page title, NO page body, and NO publish date anywhere in the dataset
(the sibling ``btf2_questions_and_forecasts.parquet`` holds the questions, not page
text). So this index cannot store "the article text" — that text is not distributed.
What it CAN do, and what removes the discovery confound from teacher comparisons, is make
the *set of source URLs the SOTA agent actually consulted* searchable: our research runs
can find the same sources by keyword, then retrieve the real pre-cutoff content through
the time-locked ``fetch_page`` (Wayback) path. The FTS index is therefore built over text
DERIVED FROM THE URL (host + decoded path/query tokens) plus a readable slug title — the
only lexical content the manifest carries.

Output: ``bench/corpus/btf2_corpus.sqlite`` with
  * ``pages``      -- url PRIMARY KEY, title, date_scraped, question_id, text (url-derived)
  * ``pages_fts``  -- FTS5 external-content index over (title, text), ranked by bm25
  * ``corpus_meta``-- build stats + the scrape-window caveat TimeVault surfaces to the agent

Dedup grain: the manifest's grain is (question_id, url); ``pages`` is keyed by url, so
rows are collapsed to one per url keeping the EARLIEST ``date_scraped`` (the tightest true
"we knew this URL by then" instant, which is what the cutoff gate checks) and the
question_id of that earliest scrape as a representative linkage.

pyarrow is used HERE ONLY (a one-time offline step); nothing at runtime imports it. Run::

    python bench/build_corpus_index.py                    # build if absent
    python bench/build_corpus_index.py --force            # rebuild in place
    python bench/build_corpus_index.py --limit-row-groups 2   # quick smoke build
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_SRC = ROOT / "corpus" / "scraped_pages.parquet"
DEFAULT_DB = ROOT / "corpus" / "btf2_corpus.sqlite"

# The documented corpus-level caveat. date_scraped is a CRAWL date, not a publish date:
# the whole scrape ran 2025-10-13..28 while question as-of dates sit ~10-15..23, so a URL's
# crawl instant can post-date the question's as-of by a few days — the identical skew the
# SOTA teacher saw. TimeVault reads this out of corpus_meta and annotates every result.
CORPUS_CAVEAT = (
    "BTF-2 scrape manifest (discovery only): each hit is a URL FutureSearch's SOTA agent "
    "scraped, NOT page content — the dataset ships no page text. 'date' is the crawl "
    "timestamp (scrape window 2025-10-13..2025-10-28), not a publish date, so it can "
    "post-date a question's as-of by a few days (same skew the teacher had). Retrieve the "
    "actual pre-cutoff content by passing the url to fetch_page (time-locked Wayback)."
)

_TOK = re.compile(r"[^0-9A-Za-z]+")
_EXT = re.compile(r"\.(html?|php|aspx?|jsp|cfm)$", re.IGNORECASE)


def derive_title_text(url: str) -> tuple[str, str]:
    """(readable title, FTS-searchable text) from a URL — the only lexical content here.

    title: the last meaningful path segment, url-decoded, separators -> spaces (falls back
    to the host). text: host + every decoded path/query token, so keyword search over the
    manifest hits the slug words the teacher's sources carry in their URLs.
    """
    parts = urllib.parse.urlsplit(url)
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = urllib.parse.unquote(parts.path or "")
    query = urllib.parse.unquote(parts.query or "")

    segs = [s for s in path.split("/") if s]
    last = _EXT.sub("", segs[-1]) if segs else host
    title = " ".join(w for w in _TOK.split(last) if w).strip() or host
    text = " ".join(w for w in _TOK.split(f"{host} {path} {query}") if w)
    return title[:300], text


def _tune(con: sqlite3.Connection) -> None:
    """Bulk-load pragmas: durability is irrelevant for a rebuildable offline artifact."""
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-262144")  # ~256 MB page cache


def _load_staging(con: sqlite3.Connection, src: Path, limit_row_groups: int) -> int:
    """Stream the parquet row-group by row-group into an unindexed staging table."""
    import pyarrow.parquet as pq  # local import: pyarrow is an offline-only dependency

    con.execute("DROP TABLE IF EXISTS staging")
    con.execute("CREATE TABLE staging(url TEXT, date_scraped TEXT, question_id TEXT)")
    pf = pq.ParquetFile(str(src))
    n_groups = pf.metadata.num_row_groups
    if limit_row_groups > 0:
        n_groups = min(n_groups, limit_row_groups)
    total = 0
    for i in range(n_groups):
        rg = pf.read_row_group(i, columns=["url", "date_scraped", "question_id"])
        urls = rg.column("url").to_pylist()
        dates = rg.column("date_scraped").to_pylist()
        qids = rg.column("question_id").to_pylist()
        batch = [(u, d, q) for u, d, q in zip(urls, dates, qids) if u]
        con.executemany("INSERT INTO staging VALUES(?,?,?)", batch)
        total += len(batch)
        if i % 8 == 7:
            con.commit()
            print(f"  loaded row group {i + 1}/{n_groups} ({total:,} rows)", flush=True)
    con.commit()
    print(f"  staging loaded: {total:,} rows from {n_groups} row group(s)", flush=True)
    return total


def _build_pages(con: sqlite3.Connection) -> int:
    """Collapse staging to one row per url (earliest scrape) and derive title/text."""
    # SQLite bare-column rule: with a single MIN() aggregate, bare columns come from the
    # row holding that minimum — so question_id is the earliest scrape's question.
    con.execute("DROP TABLE IF EXISTS dedup")
    con.execute(
        "CREATE TABLE dedup AS "
        "SELECT url, MIN(date_scraped) AS date_scraped, question_id "
        "FROM staging GROUP BY url"
    )
    con.execute("DROP TABLE staging")  # free ~parquet-worth of temp disk before deriving

    con.execute("DROP TABLE IF EXISTS pages")
    con.execute(
        "CREATE TABLE pages("
        "url TEXT PRIMARY KEY, title TEXT, date_scraped TEXT, question_id TEXT, text TEXT)"
    )
    read = con.cursor()
    read.execute("SELECT url, date_scraped, question_id FROM dedup")
    write = con.cursor()
    n = 0
    while True:
        chunk = read.fetchmany(20000)
        if not chunk:
            break
        rows = []
        for url, date_scraped, qid in chunk:
            title, text = derive_title_text(url)
            rows.append((url, title, date_scraped, qid, text))
        write.executemany(
            "INSERT OR IGNORE INTO pages VALUES(?,?,?,?,?)", rows
        )
        n += len(rows)
        if n % 200000 == 0:
            con.commit()
            print(f"  derived + inserted {n:,} unique pages", flush=True)
    con.commit()
    con.execute("DROP TABLE dedup")
    print(f"  pages built: {n:,} unique urls", flush=True)
    return n


def _build_fts(con: sqlite3.Connection) -> str:
    """FTS5 external-content index over (title, text); FTS4 fallback if FTS5 is absent."""
    fts = "fts5"
    try:
        con.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(x)")
        con.execute("DROP TABLE _fts_probe")
    except sqlite3.OperationalError:
        fts = "fts4"

    con.execute("DROP TABLE IF EXISTS pages_fts")
    if fts == "fts5":
        con.execute(
            "CREATE VIRTUAL TABLE pages_fts USING fts5("
            "title, text, content='pages', content_rowid='rowid')"
        )
        con.execute(
            "INSERT INTO pages_fts(rowid, title, text) "
            "SELECT rowid, title, text FROM pages"
        )
    else:  # FTS4 has no external-content/bm25; store a self-contained content index
        con.execute("CREATE VIRTUAL TABLE pages_fts USING fts4(url, title, text)")
        con.execute("INSERT INTO pages_fts(url, title, text) SELECT url, title, text FROM pages")
    con.commit()
    print(f"  fts index built ({fts})", flush=True)
    return fts


def _write_meta(con: sqlite3.Connection, fts: str, n_pages: int) -> tuple[str, str]:
    lo, hi = con.execute(
        "SELECT MIN(date_scraped), MAX(date_scraped) FROM pages"
    ).fetchone()
    con.execute("DROP TABLE IF EXISTS corpus_meta")
    con.execute("CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT)")
    meta = {
        "source": "BTF-2/BTF-2 aux/scraped_pages.parquet",
        "grain": "manifest (question_id,url,date_scraped) -> one row per url, earliest scrape",
        "n_pages": str(n_pages),
        "fts": fts,
        "scrape_window_start": lo or "",
        "scrape_window_end": hi or "",
        "date_field": "date_scraped (CRAWL time, not publish date)",
        "has_page_text": "no (dataset ships URLs only; use fetch_page for content)",
        "caveat": CORPUS_CAVEAT,
    }
    con.executemany("INSERT INTO corpus_meta VALUES(?,?)", list(meta.items()))
    con.commit()
    return lo or "?", hi or "?"


def build(src: Path, db: Path, force: bool, limit_row_groups: int = 0) -> int:
    if not src.exists():
        print(f"source parquet not found: {src}", file=sys.stderr)
        return 2
    if db.exists() and not force:
        print(f"{db} already exists; pass --force to rebuild "
              f"({db.stat().st_size / 1e6:.1f} MB)", file=sys.stderr)
        return 1
    if db.exists():
        db.unlink()
    db.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db))
    try:
        _tune(con)
        print(f"building {db} from {src} ({src.stat().st_size / 1e6:.0f} MB)", flush=True)
        _load_staging(con, src, limit_row_groups)
        n_pages = _build_pages(con)
        fts = _build_fts(con)
        lo, hi = _write_meta(con, fts, n_pages)
        print("  vacuuming...", flush=True)
        con.execute("PRAGMA journal_mode=DELETE")  # VACUUM needs a rollback journal
        con.execute("VACUUM")
    finally:
        con.close()

    size_mb = db.stat().st_size / 1e6
    print(f"\nDONE: {n_pages:,} pages, fts={fts}, scrape window {lo}..{hi}")
    print(f"      {db} = {size_mb:.1f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--force", action="store_true", help="rebuild even if the db exists")
    parser.add_argument("--limit-row-groups", type=int, default=0,
                        help="index only the first N parquet row groups (smoke build; 0=all)")
    args = parser.parse_args(argv)
    return build(args.src, args.db, args.force, args.limit_row_groups)


if __name__ == "__main__":
    raise SystemExit(main())
