"""Company identity resolution. Never merges on name similarity.

Rules
  1. Names are compared only after deterministic normalisation (case, punctuation, legal suffixes).
     "Tata Steel" == "Tata Steel Limited", but "Tata Steel" != "Tata Steel BSL".
  2. With Crustdata: `company/identify` returns candidates. Exactly one candidate with an equal
     normalised name -> RESOLVED (entity key = Crustdata company id). Several equal names -> the one
     whose domain appears in the article wins; otherwise AMBIGUOUS (cannot verify identity).
     Candidates whose names are merely similar are reported as lookalikes and NOT used.
  3. Without Crustdata: UNRESOLVED_NAME_ONLY, entity key = normalised name.
"""
from __future__ import annotations

import re

from .. import net
from ..runtime import RunContext, sha
from ..sources import crustdata

LEGAL = {"ltd", "limited", "pvt", "private", "inc", "incorporated", "llp", "llc", "corp", "corporation", "co",
         "plc", "the"}
# Deliberately NOT stripped: "india", "industries", "group", "company": "Heller Industries" and "Heller"
# (a search firm) are different companies, as are "Foo India" and "Foo".


def norm_name(name: str) -> str:
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [t for t in s.split() if t not in LEGAL]
    return " ".join(toks)


def entity_key_for_name(name: str) -> str:
    return "name:" + norm_name(name)


def _domain_in(dom: str | None, text: str) -> bool:
    if not dom:
        return False
    return dom.lower() in text.lower()


def resolve(ctx: RunContext, company_name: str, article_text: str = "", article_url: str = "") -> dict:
    base = {"query_name": company_name, "normalized": norm_name(company_name), "lookalikes": [], "candidates": []}
    if not ctx.cfg.crustdata_enabled:
        return {**base, "status": "UNRESOLVED_NAME_ONLY", "entity_key": entity_key_for_name(company_name),
                "note": "No company registry/API configured; identity rests on the name as printed in the source."}

    def lookup():
        try:
            return {"candidates": crustdata.identify_company(ctx, name=company_name)}
        except (net.SourceError, crustdata.CrustdataUnavailable, ValueError) as e:
            return {"error": str(e), "_transient": True}

    # the registry lookup is shared across runs (24 h); the decision is re-made against each article
    res = ctx.shared_step(f"identify:{sha(company_name.strip().lower())}", lookup)
    if res.get("error"):
        return {**base, "status": "UNRESOLVED_NAME_ONLY", "entity_key": entity_key_for_name(company_name),
                "note": f"Company lookup failed ({res['error'][:120]}); identity rests on the printed name. "
                        "A resumed run retries the lookup."}
    return decide(company_name, res["candidates"], article_text, article_url, base)


def decide(company_name: str, cands: list[dict], article_text: str, article_url: str, base: dict | None = None) -> dict:
    """Pure decision function (unit-tested with lookalike fixtures)."""
    base = base or {"query_name": company_name, "normalized": norm_name(company_name), "lookalikes": [], "candidates": []}
    target = norm_name(company_name)
    exact = [c for c in cands if norm_name(c.get("name") or "") == target]
    looks = [c for c in cands if c not in exact]
    base["lookalikes"] = [{"name": c.get("name"), "domain": c.get("primary_domain"),
                           "crustdata_company_id": c.get("crustdata_company_id"),
                           "why_not_merged": "name differs after normalisation"} for c in looks[:5]]
    base["candidates"] = [{"name": c.get("name"), "domain": c.get("primary_domain"),
                           "crustdata_company_id": c.get("crustdata_company_id")} for c in exact[:5]]
    chosen = None
    how = ""
    if len(exact) == 1:
        chosen, how = exact[0], "single exact normalised-name match in Crustdata company/identify"
    elif len(exact) > 1:
        hay = f"{article_text} {article_url}"
        by_dom = [c for c in exact if any(_domain_in(d, hay) for d in ([c.get("primary_domain")] + (c.get("all_domains") or [])))]
        if len(by_dom) == 1:
            chosen, how = by_dom[0], "several companies share the name; the article mentions this one's domain"
        else:
            indian = [c for c in exact if (c.get("primary_domain") or "").endswith(".in")]
            if len(indian) == 1:
                chosen, how = indian[0], "several companies share the name; only one has an Indian (.in) domain"
            else:
                # one record clearly stronger: strictly highest Crustdata match confidence AND it has a website
                # (the weaker ones are typically stale duplicate records without a domain)
                ranked = sorted(exact, key=lambda c: -(c.get("confidence_score") or 0))
                top, second = ranked[0], ranked[1]
                if ((top.get("confidence_score") or 0) > (second.get("confidence_score") or 0)
                        and top.get("primary_domain")):
                    chosen, how = top, (f"several records share the name; chose the one with the highest Crustdata match "
                                        f"confidence ({top.get('confidence_score')} vs {second.get('confidence_score')}) "
                                        f"and a verified website")
    if chosen is None:
        status = "AMBIGUOUS" if exact else "NOT_FOUND"
        note = (f"{len(exact)} companies share this exact name and the source does not say which one"
                if exact else "No company with this exact name in Crustdata; similar names were not merged")
        return {**base, "status": status, "entity_key": entity_key_for_name(company_name), "note": note}
    return {
        **base,
        "status": "RESOLVED",
        "entity_key": f"cd:{chosen.get('crustdata_company_id')}",
        "note": how,
        "company": chosen,
        "evidence": {
            "source": "Crustdata POST /company/identify",
            "field": "company_data.basic_info.name",
            "value": chosen.get("name"),
            "crustdata_company_id": chosen.get("crustdata_company_id"),
            "domain": chosen.get("primary_domain"),
        },
    }
