"""Decision-maker discovery via Crustdata person search.

Personal-data policy (see README, Ethics): we keep only name, current title, company, the public
professional-profile URL and the source record. No personal email or phone is collected or inferred.
A person is kept only if Crustdata lists the role as CURRENT at the resolved company id.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .. import net
from ..runtime import RunContext
from ..sources import crustdata

PRIORITY = [  # function relevance to buying 3PL services
    ("supply chain", 50), ("logistics", 48), ("warehouse", 45), ("chief operating officer", 50), ("coo", 50),
    ("distribution", 42), ("procurement", 40), ("purchase", 36), ("sourcing", 36), ("operations", 34),
]
SENIORITY = [  # who can actually sign a 3PL contract (first match wins, so specific titles come first)
    (r"\bchief manager\b", 32), (r"\b(chief|cxo|cpo|coo)\b", 50), (r"\b(president|vp|vice president|avp|director)\b", 45),
    (r"\b(head|hod|general manager|gm)\b", 42), (r"\b(senior manager|sr\.? manager|deputy general manager|dgm|agm)\b", 32),
    (r"\b(chief manager|lead)\b", 30), (r"\bmanager\b", 22),
    (r"\b(foreman|supervisor|incharge|in-charge|executive|associate|assistant|analyst|officer|coordinator|engineer)\b", 5),
    (r"\b(intern|trainee|apprentice|student)\b", -60),
]


def title_rank(title: str) -> int:
    """function relevance (0-50) + seniority (0-50). 'Chief Procurement Officer' (90) outranks a
    'logistics foreman' (53): the contract decision sits with the senior buyer."""
    t = (title or "").lower()
    func = max((score for kw, score in PRIORITY if kw in t), default=0)
    if not func:
        return 0
    sen = 10
    for rx, score in SENIORITY:
        if re.search(rx, t):
            sen = score
            break
    return func + sen


def _current_title(profile: dict, company_id: int) -> str | None:
    cur = ((profile.get("experience") or {}).get("employment_details") or {}).get("current") or []
    for job in cur:
        cid = job.get("company_id") or job.get("crustdata_company_id")
        if cid is not None and str(cid) == str(company_id):
            return job.get("title")
    return None


def find_decision_makers(ctx: RunContext, identity: dict) -> dict:
    if identity.get("status") != "RESOLVED":
        return {"status": "CANNOT_VERIFY", "people": [],
                "missing": "company identity not resolved to a registry/API record, so its people cannot be looked up safely"}
    if not ctx.cfg.crustdata_enabled:
        return {"status": "CANNOT_VERIFY", "people": [], "missing": "no people data source configured (CRUSTDATA_API_KEY)"}
    cid = identity["company"]["crustdata_company_id"]

    def run():
        try:
            profiles = crustdata.search_decision_makers(ctx, cid)
        except (net.SourceError, crustdata.CrustdataUnavailable, ValueError) as e:
            return {"status": "CANNOT_VERIFY", "people": [], "_transient": True,
                    "missing": f"people lookup failed ({str(e)[:120]}); a resumed run retries it"}
        retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
        people = []
        for p in profiles:
            title = _current_title(p, cid)
            if not title:
                continue  # role at this company not confirmed as current -> not used
            rank = title_rank(title)
            if rank <= 0:
                continue
            bp = p.get("basic_profile") or {}
            sh = p.get("social_handles") or {}
            pn = sh.get("professional_network_identifier") or {}
            profile_url = pn.get("profile_url") if isinstance(pn, dict) else None
            people.append({
                "name": bp.get("name"),
                "title": title,
                "rank": rank,
                "crustdata_person_id": p.get("crustdata_person_id"),
                "profile_url": profile_url,
                "evidence": {
                    "source": "Crustdata POST /person/search",
                    "field": "experience.employment_details.current[].title (company_id match)",
                    "company_id": cid,
                    "retrieved_at": retrieved,
                },
            })
        people.sort(key=lambda x: x["rank"], reverse=True)
        if not people:
            return {"status": "CANNOT_VERIFY", "people": [],
                    "missing": "no current supply-chain/operations/procurement role found for this company"}
        return {"status": "VERIFIED", "people": people[:3]}

    return ctx.shared_step(f"people:{cid}", run)
