"""Buying-trigger extraction: article text -> candidate events, each field backed by a verbatim quote.

The extractor PROPOSES; the verifier DECIDES. Quotes that are not found word-for-word in the
fetched page are discarded by `verifier.py`, so a hallucinated field can never reach a report.
"""
from __future__ import annotations

import re

from .. import geo, llm
from ..runtime import RunContext, sha

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "company_quote": {"type": "string"},
                    "event_type": {"type": "string", "enum": list(geo.EVENT_TYPES)},
                    "event_summary": {"type": "string"},
                    "event_quote": {"type": "string"},
                    "status": {"type": "string", "enum": ["announced_planned", "completed_or_inaugurated", "unclear"]},
                    "location_city": {"type": "string"},
                    "location_state": {"type": "string"},
                    "location_quote": {"type": "string"},
                    "event_date": {"type": "string"},
                    "event_date_quote": {"type": "string"},
                    "investment": {"type": "string"},
                    "investment_quote": {"type": "string"},
                    "scale": {"type": "string"},
                    "scale_quote": {"type": "string"},
                    "industry": {"type": "string"},
                    "company_is_logistics_provider": {"type": "boolean"},
                },
                "required": ["company_name", "company_quote", "event_type", "event_summary", "event_quote", "status",
                             "location_city", "location_state", "location_quote", "event_date", "event_date_quote",
                             "investment", "investment_quote", "scale", "scale_quote", "industry",
                             "company_is_logistics_provider"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}

SYSTEM = """You extract business events from one news article for a logistics sales team.
Rules:
- Extract only events the article itself states about a named company: new or expanded warehouses, plants, factories,
  distribution centres, fulfilment centres, market entry, major investment or funding, supply-chain hiring.
- Every *_quote field must be copied EXACTLY, character for character, from the article text (one sentence or phrase,
  max 300 characters). If the article does not state a value, return "" for both the value and its quote. Never infer,
  never use outside knowledge, never paraphrase inside a quote.
- company_name: the company exactly as named in the article (legal suffix only if the article uses it).
- event_date: YYYY-MM-DD only if the article states the date of the event; else "".
- location_state: the Indian state where the facility/event is, only if stated or the city is stated.
- company_is_logistics_provider: true if the company itself sells logistics/warehousing/courier services.
- Return {"events": []} if the article has no such event."""

MAX_ARTICLE_CHARS = 14000


def llm_extract(ctx: RunContext, art: dict) -> list[dict]:
    text = art["text"]
    note = ""
    if len(text) > MAX_ARTICLE_CHARS:
        text = text[:MAX_ARTICLE_CHARS]
        note = "\n[Article text truncated for length]"
    user = (f"Title: {art.get('title', '')}\nPublisher: {art.get('publisher', '')}\n"
            f"Published: {art.get('published') or 'unknown'}\nURL: {art.get('final_url') or art['url']}\n\n"
            f"Article text:\n{text}{note}")
    out = llm.structured(ctx, "extract_events", SYSTEM, user, EVENT_SCHEMA, max_tokens=4000)
    return out.get("events", [])


# ---------------- deterministic extractor (default when no model key) ----------------
from .rule_extractor import FACILITY, HC, NEWNESS, NOISE, LOGISTICS_NAME, rule_extract, split_sentences  # noqa: E402,F401


def worth_model_call(art: dict, spec: dict) -> tuple[bool, str]:
    """Cheap deterministic pre-screen so the model only reads articles that could contain a qualifying event.
    Saves most of the model spend: fires, court cases, awards and other-state stories never reach Claude."""
    text = f"{art.get('title', '')}\n{art.get('text', '')}"
    if spec.get("states") and not (geo.states_in(text) & set(spec["states"])):
        return False, "article never mentions a requested state or one of its cities"
    for s in split_sentences(text):
        if FACILITY.search(s) and NEWNESS.search(s) and not NOISE.search(s) and not HC.search(s):
            return True, ""
    return False, "no sentence describes a new facility, expansion or investment"


def extract(ctx: RunContext, art: dict, spec: dict) -> dict:
    key = f"extract:{sha(art['url'], ctx.cfg.llm_enabled, ','.join(spec['triggers']), ','.join(spec.get('states', [])))}"

    def run():
        if ctx.cfg.llm_enabled:
            ok, why = worth_model_call(art, spec)
            if not ok:
                ctx.record("anthropic", "extract_events", "skipped", detail=f"pre-screen: {why}: {art['url'][:150]}")
                return {"method": "prescreen", "events": [], "prescreen_reason": why}
            try:
                return {"method": "llm", "events": llm_extract(ctx, art)}
            except llm.LLMUnavailable as e:
                ctx.log("WARN", "extract", f"LLM unavailable for {art['url'][:80]} ({e}); rule fallback")
        return {"method": "rules", "events": rule_extract(art, spec)}

    return ctx.step(key, run)
