"""Sales report + outreach draft.

The model writes interpretation (why this may mean 3PL demand, likely requirements, approach, email)
from the VERIFIED facts only. A deterministic guard rejects any draft containing a number that is not
present in the verified facts (unsupported claim) and falls back to a template.
Everything here is labelled `model_interpretation` / `inference`, never as fact.
"""
from __future__ import annotations

import re

from .. import geo, llm
from ..runtime import RunContext, sha

SCHEMA = {
    "type": "object",
    "properties": {
        "why_3pl_demand": {"type": "string"},
        "logistics_requirements": {"type": "array", "items": {"type": "string"}},
        "sales_approach": {"type": "string"},
        "email_subject": {"type": "string"},
        "email_body": {"type": "string"},
    },
    "required": ["why_3pl_demand", "logistics_requirements", "sales_approach", "email_subject", "email_body"],
    "additionalProperties": False,
}

SYSTEM = """You write for the business-development team of an Indian 3PL company.
Input: verified facts about a prospect (each from a cited source) plus the 3PL's services.
Write:
- why_3pl_demand: 2-3 sentences. Phrase as inference ("may", "could"), never as fact.
- logistics_requirements: 3-5 short bullet phrases of plausible needs (inference).
- sales_approach: 2-3 sentences on how to approach, whom, and what to offer.
- email_subject and email_body: a concise, professional first email (under 150 words) asking for a 20-minute call.
  Mention the development exactly as the facts state it and cite where it was reported. Do not invent numbers, dates,
  names, locations or claims about the prospect that are not in the facts. Do not claim the prospect needs a 3PL.
  Address the recipient by the provided name if given, else "Hello". Sign off with the sender block given."""

TEMPLATES = {
    "facility": ["Inbound raw-material and outbound finished-goods transport", "Warehousing near the new site",
                 "Line-haul to regional distribution points", "Ramp-up support during commissioning"],
    "market": ["Regional warehousing in the new market", "Distribution and last-mile coverage", "Local carrier network"],
    "hiring": ["Outsourced warehouse operations", "Flexible capacity during scale-up"],
    "capital": ["Scalable warehousing as operations grow", "Multi-city distribution"],
}


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")}


def guard_numbers(text: str, allowed_text: str) -> list[str]:
    allowed = _numbers(allowed_text)
    return sorted(n for n in _numbers(text) if n not in allowed and len(n) > 1 and n not in {"20", "30"})


def template(opp: dict, client: dict, person: dict | None) -> dict:
    ev = opp["event_label"]
    comp = opp["company_name"]
    place = ", ".join(opp["cities"]) or opp["state"]
    src = opp["evidence"][0]["source_name"] if opp["evidence"] else "the press"
    date = opp["evidence"][0]["published"] if opp["evidence"] else ""
    greet = f"Dear {person['name'].split()[0]}," if person and person.get("name") else "Hello,"
    sender = client.get("sender", {})
    body = (f"{greet}\n\nI read in {src} ({date}) about {comp}'s {ev.lower()} in {place}: "
            f"\"{opp['headline_quote'][:220]}\"\n\n"
            f"{sender.get('company', 'We')} runs warehousing, transportation and distribution for manufacturers and "
            f"brands across {', '.join(client.get('serviceable_states', [])[:4])}. If useful, we would be glad to share "
            f"how we support companies during a new-site ramp-up.\n\nWould a 20-minute call next week work?\n\n"
            f"Regards,\n{sender.get('name', '')}\n{sender.get('company', '')}")
    return {
        "why_3pl_demand": (f"{comp}'s {ev.lower()} in {place} may create new inbound and outbound freight and storage "
                           f"requirements that could be outsourced to a 3PL. This is an inference from the event, not a stated need."),
        "logistics_requirements": TEMPLATES.get(opp["family"], TEMPLATES["facility"]),
        "sales_approach": ("Reference the reported development, lead with site ramp-up support, and ask for a short "
                           "discovery call with the operations or supply-chain lead."),
        "email_subject": f"{comp} - logistics support for {place}",
        "email_body": body,
        "method": "template",
    }


def write(ctx: RunContext, opp: dict, client: dict) -> dict:
    person = opp["decision_makers"]["people"][0] if opp["decision_makers"].get("people") else None
    facts_txt = "\n".join(
        f"- {f['field']}: {f['value']} (quote: \"{f['quote'][:300]}\"; source: {f['source_name']}, {f['published']})"
        for f in opp["evidence"] if f["verified"])
    sender = client.get("sender", {})
    user = (f"Prospect: {opp['company_name']}\nTrigger type: {opp['event_label']}\nLocation: {', '.join(opp['cities']) or opp['state']}\n"
            f"Verified facts:\n{facts_txt}\n\nRecipient: {person['name'] + ', ' + person['title'] if person else 'unknown'}\n"
            f"3PL services: {'; '.join(client.get('services', []))}\n"
            f"Sender block:\n{sender.get('name', '')}\n{sender.get('company', '')}")
    allowed = facts_txt + " " + user

    def run():
        if ctx.cfg.llm_enabled:
            try:
                out = llm.structured(ctx, "write_report", SYSTEM, user, SCHEMA, max_tokens=3000)
                bad = guard_numbers(out["email_body"] + " " + out["email_subject"], allowed)
                if bad:
                    ctx.log("WARN", "report", f"draft for {opp['company_name']} had unsupported numbers {bad}; template used")
                    t = template(opp, client, person)
                    t["guard"] = f"model draft rejected: unsupported numbers {bad}"
                    return t
                out["method"] = "llm"
                return out
            except llm.LLMUnavailable as e:
                ctx.log("WARN", "report", f"LLM unavailable ({e}); template used")
        return template(opp, client, person)

    return ctx.step(f"report:{sha(opp['opp_id'], user)}", run)


def label(event_type: str) -> str:
    return geo.EVENT_TYPES.get(event_type, event_type)
