"""Request Understanding: free-text sales request -> TargetSpec.

The LLM interprets the request; deterministic code validates it (known states, known trigger
types, window clamped to 1..365 days). Without an LLM key a rule parser produces the same shape.
"""
from __future__ import annotations

import difflib
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

WORD_NUM = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "twelve": 12, "fourteen": 14, "fifteen": 15, "thirty": 30, "sixty": 60, "ninety": 90}
UNIT_DAYS = {"day": 1, "week": 7, "fortnight": 14, "month": 30, "quarter": 90, "year": 365}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# Case-sensitive: lower-case "up" is a verb ("scaling up"), "UP" is Uttar Pradesh.
STATE_ABBR = {"UP": "Uttar Pradesh", "MP": "Madhya Pradesh", "TN": "Tamil Nadu", "AP": "Andhra Pradesh",
              "WB": "West Bengal", "HP": "Himachal Pradesh", "J&K": "Jammu and Kashmir", "JK": "Jammu and Kashmir"}
REGIONS = {
    "central india": ["Madhya Pradesh", "Chhattisgarh"],
    "south india": ["Tamil Nadu", "Karnataka", "Kerala", "Andhra Pradesh", "Telangana"],
    "southern india": ["Tamil Nadu", "Karnataka", "Kerala", "Andhra Pradesh", "Telangana"],
    "north india": ["Delhi", "Haryana", "Punjab", "Uttar Pradesh", "Rajasthan", "Uttarakhand", "Himachal Pradesh"],
    "northern india": ["Delhi", "Haryana", "Punjab", "Uttar Pradesh", "Rajasthan", "Uttarakhand", "Himachal Pradesh"],
    "west india": ["Gujarat", "Maharashtra", "Rajasthan", "Goa"],
    "western india": ["Gujarat", "Maharashtra", "Rajasthan", "Goa"],
    "east india": ["West Bengal", "Odisha", "Bihar", "Jharkhand"],
    "eastern india": ["West Bengal", "Odisha", "Bihar", "Jharkhand"],
    "north east": ["Assam", "Meghalaya", "Tripura", "Manipur", "Nagaland", "Mizoram", "Arunachal Pradesh", "Sikkim"],
    "northeast": ["Assam", "Meghalaya", "Tripura", "Manipur", "Nagaland", "Mizoram", "Arunachal Pradesh", "Sikkim"],
    "ncr": ["Delhi", "Haryana", "Uttar Pradesh"],
}
CITY_ALIASES = {"bombay": "Mumbai", "madras": "Chennai", "calcutta": "Kolkata", "poona": "Pune", "baroda": "Vadodara"}


def _window(text: str) -> tuple[int, list[str]]:
    """Look-back window in days, plus notes on how the wording was read."""
    t = text.lower()
    notes = []
    if re.search(r"\b(?:next|upcoming|coming)\s+(?:\d+|[a-z]+)\s+(?:day|week|month|year)", t):
        notes.append("the request asks about a future period; news can only report the past, so the past window was searched")
    m = re.search(r"\bsince\s+(?:(\d{1,2})(?:st|nd|rd|th)?\s+)?([a-z]{3})[a-z]*\.?(?:\s+(\d{1,2})(?:st|nd|rd|th)?)?", t)
    if m and m.group(2) in MONTHS:
        today = date.today()
        day = int(m.group(1) or m.group(3) or 1)
        try:
            since = date(today.year, MONTHS[m.group(2)], day)
        except ValueError:
            since = date(today.year, MONTHS[m.group(2)], 1)
        if since > today:
            since = date(today.year - 1, since.month, since.day)
        notes.append(f"'since {m.group(0)[6:].strip()}' read as {since.isoformat()}")
        return max(1, (today - since).days), notes
    m = re.search(r"\b(?:last|past|previous|within|over)\s+(?:the\s+)?(?:(\d+|[a-z]+)\s+)?"
                  r"(day|week|fortnight|month|quarter|year)s?\b", t)
    if m:
        raw = m.group(1)
        n = int(raw) if raw and raw.isdigit() else WORD_NUM.get(raw or "a", 1)
        return n * UNIT_DAYS[m.group(2)], notes
    return 30, notes


def _window_days(text: str) -> int:
    return _window(text)[0]


def _states(text: str) -> tuple[list[str], list[str]]:
    t = text.lower()
    notes = []
    found = {s for s in geo.STATE_CITIES if re.search(rf"\b{re.escape(s.lower())}\b", t)}
    for ab, st in STATE_ABBR.items():
        if re.search(rf"(?<![A-Za-z&]){re.escape(ab)}(?![A-Za-z&])", text):
            found.add(st)
            notes.append(f"'{ab}' read as {st}")
    for reg, sts in REGIONS.items():
        if re.search(rf"\b{re.escape(reg)}\b", t):
            if not any(reg != r2 and reg in r2 and re.search(rf"\b{re.escape(r2)}\b", t) for r2 in REGIONS):
                found.update(sts)
                label = "central India" if reg == "central india" else reg
                notes.append(f"region '{label}' read as {', '.join(sts)}")
    for alias, city in CITY_ALIASES.items():
        if re.search(rf"\b{alias}\b", t):
            st = geo.resolve_state(city)
            if st:
                found.add(st)
                notes.append(f"'{alias.title()}' read as {city}, {st}")
    # misspelled state names ("maharastra", "karnatka"): close match on words of 6+ letters
    words = re.findall(r"[a-z]{6,}", t)
    names = {s.lower().replace(" ", ""): s for s in geo.STATE_CITIES}
    for w in words:
        if w in names or any(w in n.lower().split() for n in geo.STATE_CITIES):
            continue
        hit = difflib.get_close_matches(w, list(names), n=1, cutoff=0.82)
        if hit and names[hit[0]] not in found:
            found.add(names[hit[0]])
            notes.append(f"'{w}' read as {names[hit[0]]} (spelling)")
    return sorted(found), notes


def rule_parse(text: str) -> dict:
    t = text.lower()
    triggers = []
    if re.search(r"warehous|storage|godown|logistics park|cold[- ]chain|reefer|refrigerat", t):
        triggers.append("WAREHOUSE_EXPANSION")
    if re.search(r"manufactur|plant|factory|production|facilit|capacity|\bunits?\b|\bmills?\b|greenfield|brownfield", t):
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
    # A generic "expanding" (not "warehouse expansion") or a need for trucks means shippers with new sites:
    # keep plant expansions even when another word (e.g. "cold storage") already matched a trigger.
    generic_expansion = re.search(r"(?<!warehouse )(?<!manufacturing )(?<!plant )(?<!capacity )\bexpan\w*|"
                                  r"\bgrowing\b|\bscal\w* up\b", t)
    needs_freight = re.search(r"\btruck|\blorr|\bfreight|\btransport|\bhaul|\bshipment|\bdispatch|\bfleet", t)
    if (generic_expansion or needs_freight) and "MANUFACTURING_EXPANSION" not in triggers:
        triggers.append("MANUFACTURING_EXPANSION")
    if generic_expansion and "GEOGRAPHIC_EXPANSION" not in triggers:
        triggers.append("GEOGRAPHIC_EXPANSION")
    if not triggers:
        triggers = ["WAREHOUSE_EXPANSION", "MANUFACTURING_EXPANSION", "DISTRIBUTION_OPERATIONS"]
    states, notes = _states(text)
    window, wnotes = _window(text)
    cities = sorted({c for c in geo.CITY_TO_STATE if re.search(rf"\b{re.escape(c)}\b", t)})
    # "manufacturing expansion/plant" names a trigger, not an industry restriction
    t_ind = re.sub(r"manufactur\w*\s+(expansion|plant|facilit\w*|unit|capacity)", " ", t)
    t_ind = re.sub(r"\b(exclud\w*|except|other than|apart from|but not|skip)\b.*", " ", t_ind)  # excluded names aren't industries
    industries = [k for k in geo.INDUSTRIES if re.search(rf"\b{re.escape(k)}\b", t_ind)]
    for k, words in geo.INDUSTRY_REQUEST_WORDS.items():
        if k not in industries and any(re.search(rf"\b{re.escape(w)}\b", t_ind) for w in words):
            industries.append(k)
    return {"industries": industries, "states": states, "cities": [c.title() for c in cities],
            "window_days": window, "triggers": triggers, "company_characteristics": [], "constraints": notes + wnotes}


EXCL_RE = re.compile(r"\b(?:exclud(?:e|ing)|except(?: for)?|other than|apart from|but not|not including|skip)\s+"
                     r"([^;.\n]+)", re.I)


def request_exclusions(text: str) -> list[str]:
    """Company names the request itself rules out, e.g. "...; exclude Ritco Logistics and TCI Express"."""
    return _exclusions(text)[0]


def excluded_places(text: str) -> list[str]:
    """States the request rules out, e.g. "...in UP and HP but not in Delhi"."""
    return _exclusions(text)[1]


def _exclusions(text: str) -> tuple[list[str], list[str]]:
    companies, places = [], []
    for m in EXCL_RE.finditer(text):
        for name in re.split(r",|\band\b|\bor\b|/", m.group(1)):
            name = name.strip(" '\"")
            bare = re.sub(r"^(?:in|from|at|near)\s+", "", name, flags=re.I).strip()
            state = geo.resolve_state(bare) if bare else None
            if state and (bare.lower() == state.lower() or bare != name or bare.lower() in geo.CITY_TO_STATE
                          or bare in geo.STATE_ALIASES):
                places.append(state)
            elif len(name) >= 2 and name.lower() not in {"logistics companies", "competitors", "rivals"}:
                companies.append(name)
    return list(dict.fromkeys(companies)), list(dict.fromkeys(places))


def foreign_places(text: str) -> list[str]:
    t = text.lower()
    return [p.title() if len(p) > 3 else p.upper() for p in geo.FOREIGN_PLACES if re.search(rf"\b{re.escape(p)}\b", t)]


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
    # request-level exclusions are parsed deterministically on both paths, so a model can never drop one
    spec["excluded_companies"] = request_exclusions(text)
    for x in spec["excluded_companies"]:
        spec["constraints"].append(f"Exclude {x} (from the request)")
    for s in excluded_places(text):
        if s in spec["states"]:
            spec["states"].remove(s)
        spec["constraints"].append(f"Exclude {s} (from the request)")
    if not spec["states"]:
        spec["states"] = []
        foreign = foreign_places(text)
        if foreign:
            spec["out_of_scope"] = (f"The request names a location outside India ({', '.join(foreign)}). "
                                    "The client serves India only, so no search was run.")
        else:
            spec["constraints"].append("No Indian state recognised in the request; searching India-wide.")
    spec["parse_method"] = method
    return spec
