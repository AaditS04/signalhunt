"""Claude wrapper: JSON-schema constrained output, cost ledger, limit checks, memoized by prompt hash.

The model is only used for interpretation (reading an article, wording a report). It never
supplies facts: every fact it extracts must carry a verbatim quote that the deterministic
verifier finds in the fetched page, otherwise the fact is dropped.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .runtime import RunContext, sha


class LLMUnavailable(Exception):
    pass


_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=2, timeout=120.0)
    return _client


def structured(ctx: RunContext, purpose: str, system: str, user: str, schema: dict,
               max_tokens: int = 4000) -> dict:
    if not ctx.cfg.llm_enabled:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    key = f"llm:{purpose}:{sha(ctx.cfg.model, system, user, json.dumps(schema, sort_keys=True), n=24)}"
    return ctx.step(key, lambda: _call(ctx, purpose, system, user, schema, max_tokens))


def _call(ctx: RunContext, purpose: str, system: str, user: str, schema: dict, max_tokens: int) -> dict:
    import anthropic

    ctx.check_llm_budget()
    client = _get_client()
    params: dict[str, Any] = dict(
        model=ctx.cfg.model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": ctx.cfg.effort, "format": {"type": "json_schema", "schema": schema}},
    )
    t0 = time.monotonic()
    try:
        try:
            # Server-side fallback: if a safety classifier declines, the API retries on a fallback model.
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
        except (TypeError, anthropic.BadRequestError):
            resp = client.messages.create(**params)
    except anthropic.APIStatusError as e:
        ctx.record("anthropic", purpose, "error", (time.monotonic() - t0) * 1000, detail=f"HTTP {e.status_code}: {str(e)[:300]}")
        raise LLMUnavailable(f"Claude API error {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        ctx.record("anthropic", purpose, "error", (time.monotonic() - t0) * 1000, detail=str(e)[:300])
        raise LLMUnavailable("Claude API connection error") from e
    ms = (time.monotonic() - t0) * 1000
    u = resp.usage
    cr = getattr(u, "cache_read_input_tokens", 0) or 0
    cw = getattr(u, "cache_creation_input_tokens", 0) or 0
    cost = ctx.llm_cost(ctx.cfg.model, u.input_tokens, u.output_tokens, cr, cw)
    ctx.record("anthropic", purpose, "ok", ms,
               {"model": getattr(resp, "model", ctx.cfg.model), "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens, "cache_read": cr}, cost)
    if resp.stop_reason == "refusal":
        raise LLMUnavailable("model declined the request")
    if resp.stop_reason == "max_tokens":
        raise LLMUnavailable("model output truncated (max_tokens)")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMUnavailable("model returned invalid JSON") from e
