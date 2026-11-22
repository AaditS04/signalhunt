"""Ground evidence about one resolved company, fetched once and shared by the lane, ladder and trust checks.

Two Crustdata calls per company (both through the step cache, so a restart repeats neither):
  * POST /company/enrich (basic_info, headcount): headcount trend and headcount by region
  * POST /job/search: postings by this company id in the last 60 days
Only a RESOLVED company id is ever looked up, so a namesake's data is never used.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import net
from ..runtime import LimitExceeded, RunContext, sha
from ..sources import crustdata

JOB_WINDOW_DAYS = 60


def fetch(ctx: RunContext, ident: dict) -> dict:
    """Returns {"available": bool, "profile": {...}|None, "jobs": {...}|None, "missing": [...], "retrieved_on": date}."""
    today = date.today().isoformat()
    out = {"available": False, "profile": None, "jobs": None, "missing": [], "retrieved_on": today,
           "job_window_days": JOB_WINDOW_DAYS}
    if ident.get("status") != "RESOLVED":
        out["missing"].append(f"company identity is {ident.get('status')}, so no company records were looked up")
        return out
    if not ctx.cfg.crustdata_enabled:
        out["missing"].append("Crustdata not configured (CRUSTDATA_API_KEY)")
        return out
    cid = ident["company"]["crustdata_company_id"]

    def prof():
        try:
            return {"profile": crustdata.company_profile(ctx, cid)}
        except LimitExceeded:
            raise
        except (net.SourceError, crustdata.CrustdataUnavailable, ValueError) as e:
            return {"error": str(e)[:200], "_transient": True}

    since = (date.today() - timedelta(days=JOB_WINDOW_DAYS)).isoformat()

    def jobs():
        try:
            return crustdata.job_postings(ctx, cid, since)
        except LimitExceeded:
            raise
        except (net.SourceError, crustdata.CrustdataUnavailable, ValueError) as e:
            return {"error": str(e)[:200], "_transient": True}

    p = ctx.shared_step(f"ground:profile:{cid}", prof)
    j = ctx.shared_step(f"ground:jobs:{cid}:{sha(since)}", jobs)
    if p.get("error"):
        out["missing"].append(f"headcount record unavailable ({p['error'][:100]}); held for a person, not treated as 'no data'")
    else:
        out["profile"] = p.get("profile")
        if out["profile"] is None:
            out["missing"].append("Crustdata has no headcount record for this company")
    if j.get("error"):
        out["missing"].append(f"job postings unavailable ({j['error'][:100]}); held for a person, not treated as 'no data'")
    else:
        out["jobs"] = j
    out["available"] = out["profile"] is not None or out["jobs"] is not None
    out["company_id"] = cid
    return out
