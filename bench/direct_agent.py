"""Direct OpenRouter chat transport for the bench's tool-less, single-completion calls.

Some bench calls are one-shot completions with NO tools: contamination_probe.py's recall
probes, and run_bench.py's "zero" tier under --leakfree none. Shelling those out to the
`claude` CLI has two measured problems:

1. The CLI prepends ~22,000 tokens of Claude Code system scaffolding to EVERY call — a
   bare "say hello" bills 22,020 input tokens ($0.066). For a one-shot completion that
   scaffolding is pure overhead, paid thousands of times over a set.
2. Through OpenRouter's Anthropic-compatible endpoint the CLI returns an EMPTY result for
   non-Anthropic models (google/gemini-2.5-pro -> result:"", output_tokens:16): the
   compat translation drops the content, so a cross-model bench cell silently scores noise.

A direct HTTPS POST to OpenRouter's NATIVE chat API fixes both — ~5-10x cheaper (no
scaffolding) and model-agnostic (native schema, no Anthropic-skin translation). Stdlib
urllib only, the same as bench/timevault.py; no third-party HTTP dependency (repo rule).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "forecast-scaffold-direct/0.1 (bench tool-less completions)"


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    """POST json -> parsed json dict. The single transport seam; tests replace this
    (or urllib.request.urlopen underneath it). HTTPError propagates so the caller can
    read the error body and decide whether the status is retryable."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _error_body(exc: urllib.error.HTTPError) -> str:
    """The HTTP error body, best-effort — OpenRouter states the actual fault there
    (model not found, no credit, bad request), so its head goes into the raised message."""
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - diagnostics only; never mask the original error
        return ""


def run_direct(
    prompt: str, system: str | None, model: str, timeout: int
) -> tuple[str, float, str]:
    """One tool-less completion via OpenRouter's native chat API.

    Returns (output_text, cost_usd, model_id) — the SAME 3-tuple contract as
    bot/run_bot.py's run_agent, so a bench call site swaps transports by branching only
    on the provider, not on the shape of what it unpacks.

    cost_usd is OpenRouter's own usage.cost (returned because usage.include=true); when the
    field is absent it is stamped 0.0 rather than guessed. On HTTP error the response body's
    head is included in the raised message. ONE retry on transient failures (HTTP 429/5xx,
    timeout), 5s apart, matching the repo's minimal-retry style (timevault's GDELT backoff);
    no retry on any other 4xx.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set — the openrouter-direct transport needs it "
            "(create one at https://openrouter.ai/keys)"
        )
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    payload = {"model": model, "messages": messages, "usage": {"include": True}}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    data: dict | None = None
    for attempt in range(2):  # one original try + one retry on transient failures
        try:
            data = _post(OPENROUTER_CHAT_URL, payload, headers, timeout)
            break
        except urllib.error.HTTPError as exc:
            body = _error_body(exc)
            transient = exc.code == 429 or 500 <= exc.code < 600
            if transient and attempt == 0:
                time.sleep(5)
                continue
            raise RuntimeError(
                f"OpenRouter HTTP {exc.code} for model {model!r}: {body[:300]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            # Connection failure or timeout: retry once, then surface it.
            if attempt == 0:
                time.sleep(5)
                continue
            raise RuntimeError(
                f"OpenRouter request failed for model {model!r}: {exc}"
            ) from exc

    choices = (data or {}).get("choices") or []
    if not choices:
        raise RuntimeError(
            f"OpenRouter returned no choices for model {model!r}: "
            f"{json.dumps(data)[:300]}"
        )
    text = str((choices[0].get("message") or {}).get("content") or "")
    raw_cost = (data.get("usage") or {}).get("cost")
    try:
        cost = float(raw_cost) if raw_cost is not None else 0.0
    except (TypeError, ValueError):
        cost = 0.0
    model_id = str(data.get("model") or model)
    return text, cost, model_id
