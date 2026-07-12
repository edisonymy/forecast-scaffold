"""Diagnostic retrieval-recall audit for the BTF-2 corpus substrate.

The public BTF-2 release does not include the SOTA teacher's search/page-read trace or a
document-id-to-URL map, so a literal "teacher-cited load-bearing page" audit cannot be
reconstructed.  This script runs the strongest non-tautological public-data proxy:

* queries are frozen independently from the corpus/manifest;
* the relevant set is every published question->page edge for that fixed question;
* production-global retrieval is exactly what tranche1 received (8M URL-derived docs);
* question-scoped retrieval shows whether global dilution, rather than query wording, hid
  the question's own source set;
* URL retention and cutoff eligibility are reported separately from ranking; and
* optional Wayback readability is kept separate from discovery.

The fixed sample is the first 20 questions of the tranche's preregistered first-40 order.
Results are diagnostic, not a promotion gate, and must never be described as recall of
teacher-cited pages.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))

from build_corpus_index import derive_title_text  # noqa: E402
from run_bench import spec_as_of  # noqa: E402
from timevault import TimeVault, _parse_stamp, parse_cutoff  # noqa: E402

DEFAULT_GOLD = Path(__file__).with_name("substrate-recall-proxy.jsonl")
DEFAULT_SET = ROOT / "bench/sets/btf2-loop1-adm.jsonl"
DEFAULT_CORPUS = ROOT / "bench/corpus/btf2_corpus.sqlite"
DEFAULT_MANIFEST = ROOT / "bench/corpus/scraped_pages.parquet"

Json = dict[str, Any]
SourceSets = dict[str, dict[str, str]]  # qid -> url -> earliest qid-edge crawl stamp


def load_jsonl(path: Path) -> list[Json]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def bare_qid(qid: str) -> str:
    return str(qid).removeprefix("btf2:")


def canonical_qid(qid: str) -> str:
    value = str(qid)
    return value if value.startswith("btf2:") else f"btf2:{value}"


def validate_gold(gold: list[Json], specs: list[Json], max_queries: int,
                  expected_first: int) -> None:
    """Fail before touching the corpus if the preregistered sample/schema drifted."""
    if len(gold) != expected_first:
        raise ValueError(f"gold has {len(gold)} rows; expected exactly {expected_first}")
    expected = [str(row["id"]) for row in specs[:expected_first]]
    observed = [str(row.get("qid", "")) for row in gold]
    if observed != expected:
        raise ValueError("gold qids must equal the set's first fixed questions in file order")
    if len(set(observed)) != len(observed):
        raise ValueError("gold qids must be unique")
    for row in gold:
        queries = row.get("queries")
        if not isinstance(queries, list) or not 1 <= len(queries) <= max_queries:
            raise ValueError(
                f"{row.get('qid')}: queries must be a list of 1..{max_queries} strings"
            )
        if any(not isinstance(query, str) or not query.strip() for query in queries):
            raise ValueError(f"{row.get('qid')}: every query must be a non-empty string")
        if row.get("proxy_kind") != "question_source_set":
            raise ValueError(f"{row.get('qid')}: proxy_kind must be question_source_set")


def load_source_sets(manifest: Path, qids: Iterable[str]) -> SourceSets:
    """Read the selected qid edges from parquet once; do not load the 15.5M rows."""
    import pyarrow.dataset as ds

    bare = [bare_qid(qid) for qid in qids]
    dataset = ds.dataset(str(manifest), format="parquet")
    table = dataset.to_table(
        columns=["question_id", "url", "date_scraped"],
        filter=ds.field("question_id").isin(bare),
    )
    out: SourceSets = {canonical_qid(qid): {} for qid in bare}
    for raw_qid, url, stamp in zip(
        table.column("question_id").to_pylist(),
        table.column("url").to_pylist(),
        table.column("date_scraped").to_pylist(),
        strict=True,
    ):
        if not url:
            continue
        qid = canonical_qid(raw_qid)
        value = str(stamp or "")
        previous = out[qid].get(str(url))
        if previous is None or (value and value < previous):
            out[qid][str(url)] = value
    return out


def chunks(values: list[str], size: int = 800) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def production_pages(corpus: Path, source_sets: SourceSets) -> dict[str, str]:
    """Return production-index URL -> retained earliest crawl stamp for selected edges."""
    urls = sorted({url for sources in source_sets.values() for url in sources})
    found: dict[str, str] = {}
    con = sqlite3.connect(f"file:{corpus}?mode=ro", uri=True)
    try:
        for batch in chunks(urls):
            placeholders = ",".join("?" for _ in batch)
            for url, stamp in con.execute(
                f"SELECT url, date_scraped FROM pages WHERE url IN ({placeholders})", batch
            ):
                found[str(url)] = str(stamp or "")
    finally:
        con.close()
    return found


def is_eligible(stamp: str, cutoff: Any) -> bool:
    try:
        return _parse_stamp(stamp) <= cutoff
    except ValueError:
        return False


def build_scoped_index(source_sets: SourceSets) -> sqlite3.Connection:
    """Build a small URL-derived FTS index that preserves the selected qid edges."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE VIRTUAL TABLE scoped USING fts5("
        "qid UNINDEXED, url UNINDEXED, date_scraped UNINDEXED, title, text)"
    )
    batch: list[tuple[str, str, str, str, str]] = []
    for qid, sources in source_sets.items():
        for url, stamp in sources.items():
            title, text = derive_title_text(url)
            batch.append((qid, url, stamp, title, text))
            if len(batch) >= 10000:
                con.executemany("INSERT INTO scoped VALUES(?,?,?,?,?)", batch)
                batch.clear()
    if batch:
        con.executemany("INSERT INTO scoped VALUES(?,?,?,?,?)", batch)
    con.commit()
    return con


def scoped_search(con: sqlite3.Connection, qid: str, query: str, cutoff: Any,
                  limit: int) -> list[str]:
    match = TimeVault._fts_match(query)
    if not match:
        return []
    rows = con.execute(
        "SELECT url, date_scraped FROM scoped "
        "WHERE scoped MATCH ? AND qid = ? ORDER BY rank LIMIT ?",
        (match, qid, max(limit * 8, 40)),
    ).fetchall()
    out: list[str] = []
    for url, stamp in rows:
        if is_eligible(str(stamp or ""), cutoff):
            out.append(str(url))
        if len(out) >= limit:
            break
    return out


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return math.nan, math.nan
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def first_relevant_rank(urls: list[str], relevant: set[str]) -> int | None:
    return next((index for index, url in enumerate(urls, 1) if url in relevant), None)


def audit_one(row: Json, spec: Json, sources: dict[str, str], retained: dict[str, str],
              scoped: sqlite3.Connection, corpus: Path, top_k: int,
              secondary_k: int, fetch_readability: bool) -> Json:
    qid = str(row["qid"])
    as_of = spec_as_of(spec)
    if not as_of:
        raise ValueError(f"{qid}: set row has no as-of timestamp")
    cutoff = parse_cutoff(as_of)
    vault = TimeVault(cutoff, corpus_db=str(corpus))
    relevant = set(sources)

    production_retained = {url for url in relevant if url in retained}
    production_eligible = {
        url for url in production_retained if is_eligible(retained[url], cutoff)
    }
    qid_edge_eligible = {url for url, stamp in sources.items() if is_eligible(stamp, cutoff)}

    query_rows: list[Json] = []
    first_global_url: str | None = None
    for query in row["queries"]:
        global_hits = vault.search_corpus(query, limit=top_k)
        global_urls = [str(hit.get("url", "")) for hit in global_hits]
        scoped_urls = scoped_search(scoped, qid, query, cutoff, top_k)
        global_rank = first_relevant_rank(global_urls, relevant)
        scoped_rank = 1 if scoped_urls else None  # every scoped row is in the relevant set
        if global_rank is not None and first_global_url is None:
            first_global_url = global_urls[global_rank - 1]
        query_rows.append({
            "query": query,
            "global_relevant_rank_top25": global_rank,
            "global_relevant_top8": global_rank is not None and global_rank <= secondary_k,
            "global_hits_returned": len(global_urls),
            "scoped_relevant_rank_top25": scoped_rank,
            "scoped_hits_returned": len(scoped_urls),
            "scoped_relevant_top8": bool(scoped_urls[:secondary_k]),
        })

    global_ranks = [item["global_relevant_rank_top25"] for item in query_rows
                    if item["global_relevant_rank_top25"] is not None]
    scoped_found = any(item["scoped_hits_returned"] for item in query_rows)
    global_found = bool(global_ranks)
    if not production_retained:
        failure = "absent"
    elif not production_eligible:
        failure = "cutoff_gate"
    elif not scoped_found:
        failure = "lexical_miss"
    elif not global_found:
        failure = "ranking_miss"
    else:
        failure = None

    readable: bool | None = None
    readability_error: str | None = None
    if fetch_readability and first_global_url:
        try:
            fetched = vault.fetch_page(first_global_url, max_chars=2000)
            readable = bool(str(fetched.get("text", "")).strip())
        except Exception as exc:  # noqa: BLE001 - readability is explicitly non-fatal
            readable = False
            readability_error = type(exc).__name__

    return {
        "qid": qid,
        "proxy_kind": row["proxy_kind"],
        "load_bearing_claim": row["load_bearing_claim"],
        "as_of": as_of,
        "source_urls": len(relevant),
        "production_retained": len(production_retained),
        "production_retention_rate": len(production_retained) / max(1, len(relevant)),
        "production_cutoff_eligible": len(production_eligible),
        "production_cutoff_eligible_rate": len(production_eligible) / max(1, len(relevant)),
        "qid_edge_cutoff_eligible": len(qid_edge_eligible),
        "qid_edge_cutoff_eligible_rate": len(qid_edge_eligible) / max(1, len(relevant)),
        "global_discoverable_top25": global_found,
        "global_discoverable_top8": any(
            item["global_relevant_top8"] for item in query_rows
        ),
        "global_first_relevant_rank": min(global_ranks) if global_ranks else None,
        "qid_scoped_discoverable_top25": scoped_found,
        "qid_scoped_discoverable_top8": any(
            item["scoped_relevant_top8"] for item in query_rows
        ),
        "failure_reason": failure,
        "first_global_relevant_url": first_global_url,
        "wayback_readable": readable,
        "wayback_error": readability_error,
        "queries": query_rows,
    }


def percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def print_summary(details: list[Json]) -> None:
    n = len(details)
    print("BTF-2 substrate recall diagnostic (question-source-set proxy; NOT teacher-cited-page recall)")
    print(f"questions: {n}")
    for key, label in (
        ("global_discoverable_top25", "production-global discoverable @25"),
        ("global_discoverable_top8", "production-global discoverable @8"),
        ("qid_scoped_discoverable_top25", "question-scoped discoverable @25"),
        ("qid_scoped_discoverable_top8", "question-scoped discoverable @8"),
    ):
        successes = sum(bool(row[key]) for row in details)
        lo, hi = wilson(successes, n)
        print(f"{label:40s} {successes:2d}/{n} = {percent(successes / n)} "
              f"(Wilson95 {percent(lo)}..{percent(hi)})")
    for key, label in (
        ("production_retention_rate", "mean production URL retention"),
        ("production_cutoff_eligible_rate", "mean production cutoff eligibility"),
        ("qid_edge_cutoff_eligible_rate", "mean qid-edge cutoff eligibility"),
    ):
        mean = sum(float(row[key]) for row in details) / n
        print(f"{label:40s} {percent(mean)}")
    failures = Counter(row["failure_reason"] or "discovered" for row in details)
    print("failure classes: " + ", ".join(
        f"{name}={count}" for name, count in sorted(failures.items())
    ))
    readable = [row["wayback_readable"] for row in details
                if row["wayback_readable"] is not None]
    if readable:
        print(f"Wayback readable (discovered URL only): {sum(readable)}/{len(readable)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--set", dest="set_path", type=Path, default=DEFAULT_SET)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--top-k", type=int, default=25)
    parser.add_argument("--secondary-k", type=int, default=8)
    parser.add_argument("--max-queries", type=int, default=5)
    parser.add_argument("--expected-first", type=int, default=20)
    parser.add_argument("--fetch-readability", action="store_true")
    parser.add_argument("--details-out", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.secondary_k <= args.top_k <= 25:
        raise ValueError("require 1 <= secondary-k <= top-k <= 25")
    specs = load_jsonl(args.set_path)
    gold = load_jsonl(args.gold)
    validate_gold(gold, specs, args.max_queries, args.expected_first)
    spec_by_qid = {str(row["id"]): row for row in specs}
    source_sets = load_source_sets(args.manifest, (row["qid"] for row in gold))
    missing = [qid for qid, urls in source_sets.items() if not urls]
    if missing:
        raise ValueError(f"manifest has no edges for {missing}")
    retained = production_pages(args.corpus, source_sets)
    scoped = build_scoped_index(source_sets)
    try:
        details = [
            audit_one(
                row, spec_by_qid[str(row["qid"])], source_sets[str(row["qid"])],
                retained, scoped, args.corpus, args.top_k, args.secondary_k,
                args.fetch_readability,
            )
            for row in gold
        ]
    finally:
        scoped.close()
    print_summary(details)
    if args.details_out:
        args.details_out.parent.mkdir(parents=True, exist_ok=True)
        args.details_out.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in details),
            encoding="utf-8",
        )
        print(f"details: {args.details_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
