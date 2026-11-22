"""Duplicate-event clustering and contradiction detection (deterministic).

Many outlets syndicate one press release. Verified events are grouped by
(resolved company identity, state, trigger family); each group becomes ONE opportunity whose
evidence list holds every independent source. The opportunity id is a hash of that key, so a
re-run upserts the same row instead of creating a duplicate.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import Counter

from ..runtime import sha

FAMILY = {
    "WAREHOUSE_EXPANSION": "facility",
    "DISTRIBUTION_OPERATIONS": "facility",
    "ECOMMERCE_GROWTH": "facility",
    "MANUFACTURING_EXPANSION": "facility",
    "GEOGRAPHIC_EXPANSION": "market",
    "SUPPLY_CHAIN_HIRING": "hiring",
    "FUNDING_OR_INVESTMENT": "capital",
}


def amount_crore(s: str) -> float | None:
    """'Rs 1,200 crore' -> 1200.0 ; '₹5 billion' -> 500.0 ; None when not parseable."""
    if not s:
        return None
    t = s.lower().replace(",", "")
    m = re.search(r"([\d.]+)\s*(crore|cr\b|lakh|billion|bn|million|mn)", t)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    if unit in ("crore", "cr"):
        return n
    if unit == "lakh":
        return n / 100
    if unit in ("billion", "bn"):
        return n * 100  # INR billion -> crore (1 bn = 100 crore)
    if unit in ("million", "mn"):
        return n / 10
    return None


def domain(url: str | None) -> str:
    return urllib.parse.urlparse(url or "").netloc.lower().replace("www.", "")


def cluster(verified: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for v in verified:
        fam = FAMILY.get(v["event"]["event_type"], "other")
        k = (v["identity"]["entity_key"], v.get("state") or "", fam)
        groups.setdefault(k, []).append(v)
    out = []
    for (ekey, state, fam), items in groups.items():
        # independent sources = distinct publisher domains
        doms = []
        for it in items:
            d = domain(it["article"].get("final_url") or it["article"].get("url"))
            if d not in doms:
                doms.append(d)
        types = Counter(it["event"]["event_type"] for it in items)
        conflicts = []
        amounts = {}
        for it in items:
            for f in it["facts"]:
                if f["field"] == "investment" and f["verified"]:
                    a = amount_crore(f["value"])
                    if a is not None:
                        amounts.setdefault(round(a, 1), []).append(f["source_url"])
        if len(amounts) > 1:
            conflicts.append({
                "field": "investment",
                "values": [{"crore": k, "sources": v} for k, v in amounts.items()],
                "resolution": "CANNOT_VERIFY: sources disagree on the amount; no single figure is reported",
            })
        cities = sorted({it["event"].get("location_city") for it in items if it["event"].get("location_city")})
        out.append({
            "opp_id": sha("opp", ekey, state, fam),
            "entity_key": ekey,
            "state": state,
            "family": fam,
            "event_type": types.most_common(1)[0][0],
            "items": sorted(items, key=lambda x: x["article"].get("published") or "", reverse=True),
            "independent_sources": doms,
            "duplicate_reports": len(items) - len(doms),
            "cities": cities,
            "conflicts": conflicts,
        })
    return out
