"""Match an opportunity to the 3PL's perishable capacity: empty return lanes and idle warehouse space.

A lane match is strong only with evidence at BOTH ends:
  * outbound from a new site: the site is in the lane's origin state AND the company has people
    (or open postings) in the lane's destination state;
  * inbound to a new site: the site is in the lane's destination state AND the company already
    operates in the lane's origin state (supplies can ride the empty leg).
One-ended matches are labelled ORIGIN_ONLY (weaker). Everything is deterministic code; every
reason carries its source and record date.
"""
from __future__ import annotations

import re

from .. import geo

# industrial clusters -> hub city used on the 3PL's capacity sheet
CLUSTERS = {
    "chakan": "Pune", "talegaon": "Pune", "ranjangaon": "Pune", "shirwal": "Pune", "pimpri": "Pune",
    "chinchwad": "Pune", "hinjewadi": "Pune", "bhosari": "Pune",
    "sriperumbudur": "Chennai", "oragadam": "Chennai", "ambattur": "Chennai", "guindy": "Chennai",
    "gummidipoondi": "Chennai", "kanchipuram": "Chennai", "ranipet": "Chennai", "maraimalai nagar": "Chennai",
    "bhiwandi": "Mumbai", "thane": "Mumbai", "navi mumbai": "Mumbai", "panvel": "Mumbai", "jnpt": "Mumbai",
    "bangalore": "Bengaluru", "gurgaon": "Gurugram", "manesar": "Gurugram",
}


def hub(city: str | None) -> str | None:
    if not city:
        return None
    return CLUSTERS.get(city.strip().lower(), city.strip())


def _region_hits(profile: dict | None, state: str, cities: list[str]) -> list[dict]:
    """Employees by region from the company's headcount record that fall in a state or its cities."""
    if not profile:
        return []
    names = [state.lower()] + [c.lower() for c in cities]
    out = []
    for region, n in (profile.get("by_region_absolute") or {}).items():
        r = region.lower()
        if n and (any(re.search(rf"\b{re.escape(x)}\b", r) for x in names) or geo.resolve_state(region) == state):
            out.append({"region": region, "employees": n})
    return out


def _postings_in(jobs: dict | None, state: str, cities: list[str]) -> list[dict]:
    if not jobs:
        return []
    out = []
    for p in jobs.get("postings", []):
        loc = " ".join(str(x) for x in (p.get("location_raw"), p.get("city"), p.get("state")) if x)
        if not loc:
            continue
        # reject lookalike places: the posting must resolve to an Indian state we know
        st = geo.resolve_state(loc)
        if st == state or any(re.search(rf"\b{re.escape(c.lower())}\b", loc.lower()) for c in cities):
            if (p.get("country") or "IND").upper() in ("IND", "INDIA", ""):
                out.append(p)
    return out


def presence(signals: dict, state: str, cities: list[str]) -> dict:
    """Is this company present in a region? Evidence from headcount-by-region and job postings."""
    if not signals.get("available"):
        return {"status": "CANNOT_VERIFY", "missing": "; ".join(signals.get("missing") or ["no company records"]),
                "evidence": []}
    ev = []
    for h in _region_hits(signals.get("profile"), state, cities):
        ev.append({"kind": "employees_in_region", "source": "Crustdata POST /company/enrich",
                   "field": f"headcount.by_region_absolute['{h['region']}']", "value": h["employees"],
                   "record_date": signals.get("retrieved_on")})
    for p in _postings_in(signals.get("jobs"), state, cities)[:3]:
        ev.append({"kind": "job_posting", "source": "Crustdata POST /job/search", "field": "job_details.title",
                   "value": p.get("title"), "location": p.get("location_raw"), "url": p.get("url"),
                   "record_date": p.get("date_added")})
    return {"status": "FOUND" if ev else "NOT_FOUND", "evidence": ev,
            "missing": None if ev else f"no employees or postings found in {state} in the company's records"}


def _volume_band(opp: dict) -> dict:
    inv = opp.get("investment") or ""
    m = re.search(r"([\d,.]+)\s*(crore|cr|lakh)", inv, re.I)
    if not m:
        return {"band": "CANNOT_ESTIMATE", "basis": "no investment or scale figure in the sources",
                "label": "assessment, not a fact"}
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return {"band": "CANNOT_ESTIMATE", "basis": f"could not read '{inv}'", "label": "assessment, not a fact"}
    crore = val / 100 if m.group(2).lower() == "lakh" else val
    band = "HIGH" if crore >= 500 else "MEDIUM" if crore >= 100 else "LOW"
    return {"band": band, "basis": f"reported investment '{inv}' (bands: under 100 crore low, 100 to 500 medium, above high)",
            "label": "assessment, not a fact"}


def match(opp: dict, client: dict, signals: dict) -> dict:
    cap = client.get("capacity") or {}
    site_state = opp.get("state")
    site_cities = opp.get("cities") or []
    site_hubs = {hub(c) for c in site_cities if c}
    # industry as stated, plus the company name and headline (an extractor may label "Anuh Pharma" just "manufacturing")
    industry = " ".join(filter(None, [opp.get("industry"), opp.get("company_name"), opp.get("headline_quote")])).lower()
    matches = []
    for lane in cap.get("lanes", []):
        o_state, d_state = lane["origin_state"], lane["dest_state"]
        o_city, d_cities = lane["origin_city"], lane.get("dest_cities", [])
        value = lane.get("rate_inr_per_trip", 0) * lane.get("empty_trucks_per_week", 0)
        base = {"asset_type": "lane", "asset_id": lane["id"], "asset": lane,
                "value_at_stake_inr": value, "value_basis": "client rate card x empty trucks per week (weekly)"}
        if site_state == o_state:
            pres = presence(signals, d_state, d_cities)
            reasons = [f"new site in {', '.join(site_cities) or site_state} is at the origin end of lane {lane['id']}"
                       + (f" (cluster of {o_city})" if o_city in site_hubs else f" ({o_state})")]
            if pres["status"] == "FOUND":
                strength = "BOTH_ENDS"
                reasons.append(f"company already present in {d_state}, the lane's destination (see evidence)")
            else:
                strength = "ORIGIN_ONLY"
                reasons.append(f"destination end not shown: {pres['missing']}")
            matches.append({**base, "direction": "outbound from new site", "strength": strength, "reasons": reasons,
                            "evidence": pres["evidence"]})
        elif site_state == d_state:
            pres = presence(signals, o_state, [o_city])
            reasons = [f"new site in {', '.join(site_cities) or site_state} is at the destination end of lane {lane['id']}"]
            if pres["status"] == "FOUND":
                strength = "BOTH_ENDS"
                reasons.append(f"company already operates in {o_state}; supplies to the new site can ride the {o_city} leg")
            else:
                strength = "ORIGIN_ONLY"
                reasons.append(f"no operations shown in {o_state}: {pres['missing']}")
            matches.append({**base, "direction": "inbound to new site", "strength": strength, "reasons": reasons,
                            "evidence": pres["evidence"]})
    for sp in cap.get("spaces", []):
        if site_state != sp["state"]:
            continue
        value = sp.get("rate_inr_per_pallet_month", 0) * sp.get("pallets_free", 0)
        needs_cold = "cold" in sp.get("type", "").lower()
        fits = (not needs_cold) or any(k in industry for k in client.get("cold_chain_industries", []))
        near = sp["city"] in site_hubs or hub(sp["city"]) in site_hubs
        reasons = [f"new site in {', '.join(site_cities) or site_state}" + (f", same cluster as {sp['city']}" if near
                   else f", same state as {sp['city']}")]
        if needs_cold:
            hit = next((k for k in client.get("cold_chain_industries", []) if k in industry), None)
            reasons.append(f"'{hit}' in the company's industry, name or source typically needs cold chain" if fits
                           else f"nothing in industry '{opp.get('industry')}', name or source points to cold chain")
        matches.append({"asset_type": "space", "asset_id": sp["id"], "asset": sp, "direction": "storage near new site",
                        "strength": "BOTH_ENDS" if (fits and near) else "ORIGIN_ONLY", "reasons": reasons,
                        "evidence": [], "value_at_stake_inr": value,
                        "value_basis": "client rate card x free pallets (monthly)"})
    order = {"BOTH_ENDS": 0, "ORIGIN_ONLY": 1}
    matches.sort(key=lambda m: (order.get(m["strength"], 2), -m["value_at_stake_inr"]))
    best = matches[0] if matches else None
    return {"match_strength": best["strength"] if best else "NONE", "best": best, "all": matches,
            "volume_band": _volume_band(opp)}
