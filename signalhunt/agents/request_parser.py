"""Request Understanding: free-text sales request -> TargetSpec.

The LLM interprets the request; deterministic code validates it (known states, known trigger
types, window clamped to 1..365 days). Without an LLM key a rule parser produces the same shape.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .. import geo, llm
from ..runtime import RunContext

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "industries": {"type": "array", "items": {"type": "string"}},
        "states": {"type": "array", "items": {"type": "string"}},
        "cities": {"type": "array", "items": {"type": "string"}},
        "window_days": {"type": "integer"},
        "triggers": {"type": "array", "items": {"type": "string", "enum": list(geo.EVENT_TYPES)}},
        "company_characteristics": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["industries", "states", "cities", "window_days", "triggers", "company_characteristics", "constraints"],
    "additionalProperties": False,
}

SYSTEM = (
    "You convert a B2B sales-targeting request from an Indian 3PL (third-party logistics) company into a search spec. "
    "Use only what the request says. states: Indian state names in full. cities: only cities the request names. "
    "window_days: the look-back period in days (default 30 if not stated). triggers: the business events the request asks "
    "for, mapped to the allowed enum values. industries: lower-case industry names from the request (empty if any industry). "
    "constraints: other explicit restrictions, quoted briefly."
)

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "six": 6, "seven": 7, "fourteen": 14, "thirty": 30, "sixty": 60, "ninety": 90}


def _window_days(text: str) -> int:
    t = text.lower()
    m = re.search(r"(?:last|past|previous)\s+(\d+|\w+)\s+(day|week|month)s?", t)
    if m:
        n_raw, unit = m.group(1), m.group(2)
        n = int(n_raw) if n_raw.isdigit() else WORD_NUM.get(n_raw, 1)
        return n * {"day": 1, "week": 7, "month": 30}[unit]
    if re.search(r"(?:last|past)\s+(week)", t):
        return 7
    if re.search(r"(?:last|past)\s+(month)", t):
        return 30
    if re.search(r"(?:last|past)\s+(quarter)", t):
        return 90
    return 30


def rule_parse(text: str) -> dict:
    t = text.lower()
    triggers = []
    if re.search(r"warehous|storage|godown|logistics park", t):
        triggers.append("WAREHOUSE_EXPANSION")
    if re.search(r"manufactur|plant|factory|production|facilit", t):
        triggers.append("MANUFACTURING_EXPANSION")
    if re.search(r"geograph|new market|new region|enter", t):
        triggers.append("GEOGRAPHIC_EXPANSION")
    if re.search(r"e-?commerce|d2c|fulfil|quick commerce", t):
        triggers.append("ECOMMERCE_GROWTH")
    if re.search(r"hiring|recruit|job", t):
        triggers.append("SUPPLY_CHAIN_HIRING")
    if re.search(r"distribution|depot|hub", t):
        triggers.append("DISTRIBUTION_OPERATIONS")
    if re.search(r"fund|invest|raise", t):
        triggers.append("FUNDING_OR_INVESTMENT")
    if re.search(r"\bexpan", t) and not triggers:
        triggers += ["WAREHOUSE_EXPANSION", "MANUFACTURING_EXPANSION", "GEOGRAPHIC_EXPANSION"]
    if not triggers:
        triggers = ["WAREHOUSE_EXPANSION", "MANUFACTURING_EXPANSION", "DISTRIBUTION_OPERATIONS"]
    states = sorted({s for s in geo.STATE_CITIES if re.search(rf"\b{re.escape(s.lower())}\b", t)})
    cities = sorted({c for c in geo.CITY_TO_STATE if re.search(rf"\b{re.escape(c)}\b", t)})
    # "manufacturing expansion/plant" names a trigger, not an industry restriction
    t_ind = re.sub(r"manufactur\w*\s+(expansion|plant|facilit\w*|unit|capacity)", " ", t)
    industries = [k for k in geo.INDUSTRIES if re.search(rf"\b{re.escape(k)}\b", t_ind)]
    return {"industries": industries, "states": states, "cities": [c.title() for c in cities],
            "window_days": _window_days(text), "triggers": triggers, "company_characteristics": [], "constraints": []}


def validate(spec: dict, text: str) -> dict:
    states = []
    for s in spec.get("states", []):
        r = geo.resolve_state(s)
        if r and r not in states:
            states.append(r)
    for c in spec.get("cities", []):
        r = geo.resolve_state(c)
        if r and r not in states:
            states.append(r)
    triggers = [t for t in spec.get("triggers", []) if t in geo.EVENT_TYPES] or rule_parse(text)["triggers"]
    wd = spec.get("window_days") or 30
    wd = max(1, min(int(wd), 365))
    today = date.today()
    return {
        "industries": [i.lower().strip() for i in spec.get("industries", []) if i.strip()],
        "states": states,
        "cities": spec.get("cities", []),
        "window_days": wd,
        "window_start": (today - timedelta(days=wd)).isoformat(),
        "window_end": today.isoformat(),
        "triggers": list(dict.fromkeys(triggers)),
        "company_characteristics": spec.get("company_characteristics", []),
        "constraints": spec.get("constraints", []),
    }


def parse_request(ctx: RunContext, text: str) -> dict:
    method = "rules"
    raw = None
    if ctx.cfg.llm_enabled:
        try:
            raw = llm.structured(ctx, "parse_request", SYSTEM, f"Request:\n{text}", SPEC_SCHEMA, max_tokens=2000)
            method = "llm"
        except llm.LLMUnavailable as e:
            ctx.log("WARN", "parse_request", f"LLM unavailable ({e}); using rule parser")
    if raw is None:
        raw = rule_parse(text)
    spec = validate(raw, text)
    if not spec["states"]:
        spec["states"] = []
        spec["constraints"].append("No Indian state recognised in the request; searching India-wide.")
    spec["parse_method"] = method
    return spec
