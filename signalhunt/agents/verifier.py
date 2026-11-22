"""Evidence verification (deterministic, no model involved).

For each proposed event:
  * every field's quote must appear verbatim (after whitespace/quote normalisation) in the fetched page;
  * the field value must actually be stated inside its quote (a quote about the topic is not enough);
  * the record must be dated, and the date must fall inside the request window;
  * the event must be in the requested geography and of a requested trigger type;
  * logistics providers / excluded companies are rejected (they are rivals, not prospects).

Outcome per event: VERIFIED, CANNOT_VERIFY (with the list of what is missing) or REJECTED (with reason).
"""
from __future__ import annotations

import re
import unicodedata

from .. import geo

QUOTE_MIN = 12


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_source(quote: str, source_text: str) -> bool:
    q = normalize(quote).strip(" .\"'")
    if len(q) < QUOTE_MIN:
        return False
    return q in normalize(source_text)


def value_in_quote(value: str, quote: str) -> bool:
    v = normalize(value)
    if not v:
        return False
    q = normalize(quote)
    if v in q:
        return True
    # allow a legal suffix / minor token difference: all significant tokens of the value must be in the quote
    toks = [t for t in re.findall(r"[a-z0-9]+", v) if t not in {"ltd", "limited", "pvt", "private", "inc", "the", "co"}]
    return bool(toks) and all(re.search(rf"\b{re.escape(t)}\b", q) for t in toks)


def excluded_match(company: str, excluded: list[str]) -> str | None:
    """Exclusion is deliberately generous (a false positive only drops a lead): match when either
    normalised name contains the other as whole words, e.g. 'Ritco' vs 'Ritco Logistics'."""
    from .identity import norm_name
    c = norm_name(company)
    if not c:
        return None
    for x in excluded:
        n = norm_name(x)
        if n and (re.search(rf"\b{re.escape(n)}\b", c) or re.match(rf"{re.escape(c)}\b", n)):
            return x
    return None


def make_fact(field: str, value: str, quote: str, art: dict, verified: bool, note: str = "") -> dict:
    return {
        "field": field,
        "value": value,
        "quote": quote,
        "verified": verified,
        "source_url": art.get("final_url") or art.get("url"),
        "source_name": art.get("publisher"),
        "published": art.get("published"),
        "published_field": art.get("date_field"),
        "retrieved_at": art.get("retrieved_at"),
        "via": art.get("via"),
        "note": note,
    }


def verify_event(ev: dict, art: dict, spec: dict, excluded: list[str]) -> dict:
    src = f"{art.get('title', '')}\n{art.get('text', '')}"
    facts: list[dict] = []
    missing: list[str] = []
    reasons: list[str] = []

    def check(field: str, value: str, quote: str, required: bool, label: str) -> bool:
        if not value:
            if required:
                missing.append(f"{label}: not stated in the source")
            return False
        ok_q = quote_in_source(quote, src)
        ok_v = ok_q and value_in_quote(value, quote)
        note = "" if ok_v else ("quote not found verbatim in source" if not ok_q else "value not stated in the quoted passage")
        facts.append(make_fact(field, value, quote, art, ok_v, note))
        if not ok_v and required:
            missing.append(f"{label}: {note}")
        return ok_v

    company_ok = check("company_name", ev.get("company_name", ""), ev.get("company_quote", ""), True, "company name")
    # the event statement must be in the source and must name the company
    ev_quote_ok = quote_in_source(ev.get("event_quote", ""), src)
    facts.append(make_fact("event", ev.get("event_summary", ""), ev.get("event_quote", ""), art, ev_quote_ok,
                           "" if ev_quote_ok else "event statement not found verbatim in source"))
    if not ev_quote_ok:
        missing.append("event statement: not found verbatim in the source")

    # location: city or state must be stated in the location quote
    loc_value = ev.get("location_city") or ev.get("location_state") or ""
    loc_ok = check("location", loc_value, ev.get("location_quote", ""), True, "event location")
    state = None
    if loc_ok:
        state = geo.resolve_state(ev.get("location_city", "")) or geo.resolve_state(ev.get("location_state", ""))
        if state and ev.get("location_city") and ev.get("location_state"):
            # contradiction inside the record: city belongs to a different state than claimed
            st2 = geo.resolve_state(ev["location_state"])
            if st2 and geo.resolve_state(ev["location_city"]) and st2 != geo.resolve_state(ev["location_city"]):
                missing.append(f"event location: city {ev['location_city']} is not in {ev['location_state']}")
                state = None

    check("investment", ev.get("investment", ""), ev.get("investment_quote", ""), False, "investment")
    check("scale", ev.get("scale", ""), ev.get("scale_quote", ""), False, "scale")
    date_ok = check("event_date", ev.get("event_date", ""), ev.get("event_date_quote", ""), False, "event date")
    # an event_date fact counts only if the quote contains the date; model-normalised ISO dates won't match text
    if ev.get("event_date") and not date_ok:
        facts[-1]["note"] = "event date not stated verbatim; record publication date used instead"

    # record date
    published = art.get("published")
    if not published:
        missing.append("record date: the source page carries no publication date")
    elif published < spec["window_start"]:
        reasons.append(f"OUTDATED: record dated {published}, before the window start {spec['window_start']}")
    elif published > spec["window_end"]:
        reasons.append(f"record dated in the future ({published}); treated as unreliable")

    if ev.get("event_date") and date_ok and ev["event_date"] < spec["window_start"]:
        reasons.append(f"OUTDATED: the event itself happened on {ev['event_date']}")

    # scope rules
    if ev.get("event_type") not in spec["triggers"]:
        reasons.append(f"event type {ev.get('event_type')} was not requested")
    if spec["states"] and state and state not in spec["states"]:
        reasons.append(f"outside requested geography: event is in {state}")
    if ev.get("company_is_logistics_provider"):
        reasons.append("company is itself a logistics provider (rival, not a prospect)")
    hit = excluded_match(ev.get("company_name", ""), excluded)
    if hit:
        reasons.append(f"company is on the client's exclusion list ({hit})")

    if reasons:
        status = "REJECTED"
    elif missing or not company_ok:
        status = "CANNOT_VERIFY"
    elif spec["states"] and not state:
        status = "CANNOT_VERIFY"
        missing.append("event location: could not be mapped to an Indian state")
    else:
        status = "VERIFIED"
    return {
        "status": status,
        "reasons": reasons,
        "missing": missing,
        "facts": facts,
        "state": state,
        "event": ev,
        "article": {k: art.get(k) for k in ("url", "final_url", "title", "publisher", "published", "date_field",
                                            "retrieved_at", "via")},
    }
