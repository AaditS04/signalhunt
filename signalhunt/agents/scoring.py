"""Transparent opportunity scoring (deterministic, 0-100).

score = sum(weight_i * component_i), every component in [0, 1] with a written reason.
Weights are configuration (WEIGHTS) and are printed in every report, so a judge or a sales rep can
recompute any score by hand.
"""
from __future__ import annotations

from datetime import date

WEIGHTS = {
    "recency": 20,
    "evidence_strength": 20,
    "logistics_relevance": 20,
    "geographic_fit": 10,
    "industry_fit": 10,
    "decision_maker": 10,
    "identity_confidence": 10,
}

RELEVANCE = {
    "WAREHOUSE_EXPANSION": (1.0, "a new/expanded warehouse directly creates storage, handling and transport demand"),
    "DISTRIBUTION_OPERATIONS": (1.0, "a new distribution operation needs line-haul and last-mile capacity"),
    "MANUFACTURING_EXPANSION": (0.9, "a new plant needs inbound raw-material and outbound finished-goods logistics"),
    "ECOMMERCE_GROWTH": (0.9, "e-commerce growth needs fulfilment and last-mile capacity"),
    "GEOGRAPHIC_EXPANSION": (0.7, "entering a new region often needs a local logistics partner"),
    "SUPPLY_CHAIN_HIRING": (0.6, "supply-chain hiring signals growing logistics operations"),
    "FUNDING_OR_INVESTMENT": (0.5, "new capital may fund expansion; the logistics need is indirect"),
}

ASSUMPTIONS = [
    "Recency decays linearly from 1.0 (today) to 0.0 (window start).",
    "Evidence: 1 verified independent source = 0.6, 2 = 0.85, 3+ = 1.0; minus 0.25 when sources conflict.",
    "Logistics relevance is a fixed weight per trigger type (see RELEVANCE table in scoring.py).",
    "Identity: registry/API-resolved = 1.0, printed name only = 0.5, ambiguous/not found = 0.2.",
    "Priority: HIGH >= 70, MEDIUM >= 50, else LOW.",
    "Scores rank leads; they are not probabilities that the company will buy.",
]


def score(cluster: dict, identity: dict, people: dict, spec: dict, client: dict) -> dict:
    comps = {}
    # recency
    latest = max((it["article"].get("published") or "" for it in cluster["items"]), default="")
    try:
        age = (date.today() - date.fromisoformat(latest)).days
        r = max(0.0, min(1.0, 1 - age / max(spec["window_days"], 1)))
        comps["recency"] = (r, f"latest source dated {latest} ({age} days ago, window {spec['window_days']} days)")
    except ValueError:
        comps["recency"] = (0.0, "no dated source")
    # evidence
    n = len(cluster["independent_sources"])
    e = 0.6 if n == 1 else 0.85 if n == 2 else 1.0 if n >= 3 else 0.0
    why = f"{n} independent verified source(s)"
    if cluster["conflicts"]:
        e = max(0.0, e - 0.25)
        why += "; sources conflict on " + ", ".join(c["field"] for c in cluster["conflicts"])
    comps["evidence_strength"] = (e, why)
    # relevance
    rel, rwhy = RELEVANCE.get(cluster["event_type"], (0.5, "generic expansion signal"))
    comps["logistics_relevance"] = (rel, rwhy)
    # geography
    serviceable = set(client.get("serviceable_states", []))
    st = cluster["state"]
    if not spec["states"]:
        g = 1.0 if st in serviceable else 0.6
        gwhy = f"event in {st or 'unknown state'}; {'inside' if st in serviceable else 'outside'} client's serviceable states"
    else:
        g = 1.0 if st in spec["states"] else 0.0
        if st in spec["states"] and serviceable and st not in serviceable:
            g = 0.6
        gwhy = f"event in {st}; requested {', '.join(spec['states'])}"
    comps["geographic_fit"] = (g, gwhy)
    # industry
    inds = [i.lower() for i in spec.get("industries", [])]
    ev_inds = {(it["event"].get("industry") or "").lower() for it in cluster["items"]} - {""}
    if not inds:
        comps["industry_fit"] = (1.0, "request did not restrict industry")
    elif not ev_inds:
        comps["industry_fit"] = (0.5, "industry not stated in sources")
    elif any(any(i in x or x in i for x in ev_inds) for i in inds):
        comps["industry_fit"] = (1.0, f"industry {', '.join(sorted(ev_inds))} matches request")
    else:
        comps["industry_fit"] = (0.2, f"industry {', '.join(sorted(ev_inds))} not in requested {', '.join(inds)}")
    # decision maker
    if people.get("status") == "VERIFIED":
        top = people["people"][0]
        comps["decision_maker"] = (1.0, f"{top['title']} identified (sourced)")
    else:
        comps["decision_maker"] = (0.0, people.get("missing", "no decision-maker found"))
    # identity
    ist = identity.get("status")
    iv = {"RESOLVED": 1.0, "UNRESOLVED_NAME_ONLY": 0.5}.get(ist, 0.2)
    comps["identity_confidence"] = (iv, f"{ist}: {identity.get('note', '')}")

    total = round(sum(WEIGHTS[k] * comps[k][0] for k in WEIGHTS), 1)
    priority = "HIGH" if total >= 70 else "MEDIUM" if total >= 50 else "LOW"
    if n >= 2 and ist == "RESOLVED" and not cluster["conflicts"]:
        confidence = "HIGH"
    elif n >= 1 and ist in ("RESOLVED", "UNRESOLVED_NAME_ONLY"):
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    return {
        "score": total,
        "priority": priority,
        "confidence": confidence,
        "breakdown": [{"component": k, "weight": WEIGHTS[k], "value": round(comps[k][0], 3),
                       "points": round(WEIGHTS[k] * comps[k][0], 1), "reason": comps[k][1]} for k in WEIGHTS],
        "assumptions": ASSUMPTIONS,
    }
