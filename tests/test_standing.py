"""bench/standing.py: the pure reductions (no network)."""

from datetime import UTC, datetime

from bench.standing import own_split, recent_bot_tournaments, summarize


def _entry(name, rank, score, n, prize=0):
    return {"user": {"username": name}, "rank": rank, "score": score,
            "contribution_count": n, "coverage": float(n), "prize": prize}


def test_summarize_finds_us_and_the_prize_line():
    board = {"finalized": False, "max_coverage": 40.0, "entries": [
        _entry("us-bot", 3, 90.0, 30),
        _entry("leader", 1, 400.0, 40, prize=500),
        {"user": None, "rank": 2, "score": 300.0},  # aggregate row: dropped
        _entry("metac-opus", 2, 200.0, 40, prize=100),
        _entry("idle", 4, 0.0, 0),
    ]}
    result = summarize(board, "us-bot")
    assert result["participants"] == 4
    assert result["me"]["rank"] == 3 and result["me"]["per_q"] == 3.0
    assert [r["username"] for r in result["top"]][:2] == ["leader", "metac-opus"]
    assert result["prize_line"] == 200.0
    assert [r["username"] for r in result["baselines"]] == ["metac-opus"]
    assert result["top"][-1]["per_q"] is None  # zero contributions never divides


def test_recent_bot_tournaments_filters_by_name_and_window():
    now = datetime(2026, 11, 19, tzinfo=UTC)
    def project(slug, name, start):
        return {"slug": slug, "name": name, "start_date": f"{start}T00:00:00Z"}

    projects = [
        project("minibench-2026-11-02", "MiniBench", "2026-11-02"),
        project("fall-futureeval-2026", "Fall 2026 FutureEval", "2026-09-28"),
        project("minibench-2026-07-13", "MiniBench", "2026-07-13"),  # before the window
        project("metaculus-cup", "Metaculus Cup", "2026-11-01"),  # not a bot tournament
        project("market-pulse-27q1", "Market Pulse 27Q1", "2026-12-15"),  # not started
    ]
    slugs = [p["slug"] for p in recent_bot_tournaments(projects, now, 75)]
    assert slugs == ["minibench-2026-11-02", "fall-futureeval-2026"]


def test_own_split_counts_missed_and_groups_by_type():
    questions = [
        {"id": 1, "type": "binary", "status": "resolved"},
        {"id": 2, "type": "binary", "status": "resolved"},
        {"id": 3, "type": "numeric", "status": "closed"},
        {"id": 4, "type": "numeric", "status": "closed",
         "my_forecasts": {"latest": {"start_time": 1}}},
        {"id": 5, "type": "numeric", "status": "closed"},
        {"id": 6, "type": "numeric", "status": "upcoming"},  # window not open yet
    ]
    overlay = {1: {"spot_peer_score": 10.0}, 2: {"spot_peer_score": -4.0},
               3: {"spot_peer_score": None}}
    split = own_split(questions, overlay)
    assert split["questions"] == 6 and split["missed"] == 1  # q5; q4 is ours, just unsynced
    assert split["by_type"] == {"binary": {"n": 2, "mean": 3.0, "sum": 6.0}}
