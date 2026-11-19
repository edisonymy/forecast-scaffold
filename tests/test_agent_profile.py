"""The agent command: model and effort in one place, the candidate slot, research tools."""

import json
import shlex
from pathlib import Path

from bot import run_bot


def test_agent_command_always_names_model_and_effort():
    tokens = shlex.split(run_bot.agent_command())
    assert tokens[tokens.index("--model") + 1] == run_bot.AGENT_MODEL
    assert tokens[tokens.index("--effort") + 1] == run_bot.AGENT_EFFORT
    assert "--output-format" in tokens and "--allowed-tools" in tokens
    custom = shlex.split(run_bot.agent_command("some-model", "max"))
    assert custom[custom.index("--model") + 1] == "some-model"
    assert custom[custom.index("--effort") + 1] == "max"


def test_with_candidate_swaps_the_model_and_optionally_the_effort():
    base = run_bot.agent_command("incumbent", "high")
    same_effort = shlex.split(run_bot.with_candidate(base, "candidate"))
    assert same_effort[same_effort.index("--model") + 1] == "candidate"
    assert same_effort[same_effort.index("--effort") + 1] == "high"
    own_effort = shlex.split(run_bot.with_candidate(base, "candidate@xhigh"))
    assert own_effort[own_effort.index("--model") + 1] == "candidate"
    assert own_effort[own_effort.index("--effort") + 1] == "xhigh"
    assert own_effort.count("--effort") == 1
    bare = shlex.split(run_bot.with_candidate("claude -p --model a", "b@low"))
    assert bare[-2:] == ["--effort", "low"]


def test_with_research_tools_writes_a_config_the_agent_cannot_loosen(tmp_path: Path):
    log = tmp_path / "calls.jsonl"
    cmd = shlex.split(run_bot.with_research_tools(
        run_bot.agent_command(), "metaculus.com,polymarket.com", log))
    allowed = cmd[cmd.index("--allowed-tools") + 1].split(",")
    assert {"WebSearch", "mcp__research__fetch_raw", "mcp__research__news_search"} <= set(allowed)
    assert "--strict-mcp-config" in cmd
    config = json.loads(Path(cmd[cmd.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    args = config["mcpServers"]["research"]["args"]
    assert args[0].endswith("research_tools.py") and Path(args[0]).is_file()
    assert args[args.index("--deny-domains") + 1] == "metaculus.com,polymarket.com"
    assert args[args.index("--log") + 1] == str(log)


def test_take_tool_calls_counts_by_outcome_and_clears(tmp_path: Path):
    log = tmp_path / "calls.jsonl"
    assert run_bot.take_tool_calls(log) == {} and run_bot.take_tool_calls(None) == {}
    log.write_text("\n".join([
        json.dumps({"tool": "fetch_raw", "ok": True, "chars": 10, "host": "a.org"}),
        json.dumps({"tool": "fetch_raw", "ok": False, "chars": 0, "host": "b.org"}),
        json.dumps({"tool": "news_search", "ok": True, "chars": 5, "query": "q"}),
        "not json",
        json.dumps({"tool": "fetch_raw", "ok": True, "chars": 7, "host": "c.org"}),
    ]) + "\n", encoding="utf-8")
    assert run_bot.take_tool_calls(log) == {
        "fetch_raw": 2, "fetch_raw_failed": 1, "news_search": 1}
    assert run_bot.take_tool_calls(log) == {}  # the next take starts from zero


def test_blind_runs_carry_the_forecasting_sites_into_the_tool_deny_list():
    import re
    domains = re.findall(r"domain:([^)]+)", run_bot.ALWAYS_DISALLOWED + ","
                         + run_bot.BLIND_DISALLOWED)
    assert {"metaculus.com", "polymarket.com", "manifold.markets", "kalshi.com"} <= set(domains)
    assert re.findall(r"domain:([^)]+)", run_bot.ALWAYS_DISALLOWED) == []
