"""Worth winning? A counterparty check before any offer is made.

Verdicts: PURSUE, PURSUE_WITH_CAUTION, DONT_PURSUE, CANNOT_VERIFY.
Rule that protects small honest firms: thin evidence gives CANNOT_VERIFY (a person decides),
never DONT_PURSUE. DONT_PURSUE needs positive evidence (captive logistics arm, insolvency news,
a team shrinking fast), and every reason carries its source and record date.
"""
from __future__ import annotations

import os
import re

from .. import net
from ..runtime import LimitExceeded, RunContext, sha
from ..sources import news_rss
from .identity import norm_name

DISTRESS_HARD = re.compile(r"insolven|\bnclt\b|bankrupt|liquidat|winding up|ibc\b|default(?:ed|s)? on", re.I)
DISTRESS_SOFT = re.compile(r"layoff|lay off|job cuts|retrench|shut(?:s|ting)? down|closure|plant shut", re.I)
GENERIC_WORDS = {"group", "india", "company", "industries", "enterprises", "limited", "holdings", "global",
                 "international", "the", "and", "of", "private", "ltd", "pvt"}
SHRINK_DONT = -20.0
SHRINK_CAUTION = -5.0


def distress_news(ctx: RunContext, company: str) -> dict:
    """Free Google News RSS search for distress about this exact company (last 365 days)."""
    if os.getenv("TRUST_NEWS_CHECK", "1") == "0":
        return {"checked": False, "hits": [], "note": "news check switched off (TRUST_NEWS_CHECK=0)"}

    target = norm_name(company)
    if not target or set(target.split()) <= GENERIC_WORDS:
        return {"checked": False, "hits": [],
                "note": f"name '{company}' is too generic to search news reliably; a person checks"}

    def run():
        q = f'"{company}" insolvency OR NCLT OR layoffs OR bankruptcy'
        try:
            res = news_rss.google_news(ctx, q, days=365)
        except LimitExceeded:
            raise
        except (net.SourceError, ValueError, Exception) as e:  # noqa: BLE001 - a failed check is reported, not hidden
            return {"checked": False, "hits": [], "note": f"news search failed ({str(e)[:100]})", "_transient": True}
        hits = []
        for r in res[:40]:
            t = r.get("title") or ""
            named = re.search(rf"\b{re.escape(target)}\b", norm_name(t))
            if named and (DISTRESS_HARD.search(t) or DISTRESS_SOFT.search(t)):
                hits.append({"title": t, "url": r.get("url"), "record_date": r.get("published"),
                             "publisher": r.get("publisher"), "hard": bool(DISTRESS_HARD.search(t))})
        return {"checked": True, "hits": hits[:5], "query": q}

    return ctx.step(f"distress:{sha(company.lower())}", run)


def verdict(opp: dict, client: dict, signals: dict, distress: dict) -> dict:
    name = opp.get("company_name") or ""
    ident = opp.get("identity") or {}
    checks, evidence, missing = [], [], []
    dont, caution = [], []

    for g in client.get("captive_logistics_groups", []):
        if re.search(rf"\b{re.escape(g.lower())}\b", name.lower()):
            dont.append(f"part of the {g} group, which runs its own logistics arm (client exclusion list)")
            evidence.append({"kind": "config", "source": "client_profile.json captive_logistics_groups", "value": g})
    checks.append("captive logistics group")

    if ident.get("status") != "RESOLVED":
        missing.append(f"company identity {ident.get('status')}: {ident.get('note')}")
    else:
        lk = ident.get("lookalikes") or []
        if lk:
            evidence.append({"kind": "identity", "source": "Crustdata POST /company/identify",
                             "value": f"{len(lk)} similarly named companies kept separate"})
    checks.append("identity")

    prof = signals.get("profile")
    if prof:
        gp = prof.get("growth_percent") or {}
        six = gp.get("six_months")
        if six is not None:
            ev = {"kind": "headcount_trend", "source": "Crustdata POST /company/enrich",
                  "field": "headcount.growth_percent.six_months", "value": round(float(six), 1),
                  "total": prof.get("headcount_total"), "record_date": signals.get("retrieved_on")}
            evidence.append(ev)
            if six <= SHRINK_DONT:
                dont.append(f"team shrank {abs(six):.1f} percent in six months")
            elif six <= SHRINK_CAUTION:
                caution.append(f"team shrank {abs(six):.1f} percent in six months")
        else:
            missing.append("no six-month headcount trend in the company record")
    else:
        missing.append("headcount trend: " + ("; ".join(signals.get("missing") or []) or "no record"))
    checks.append("team growing or shrinking")

    if distress.get("checked"):
        for h in distress.get("hits", []):
            evidence.append({"kind": "distress_news", "source": h.get("publisher") or "news", "value": h["title"],
                             "url": h.get("url"), "record_date": h.get("record_date")})
            (dont if h.get("hard") else caution).append(f"distress news: \"{h['title'][:120]}\"")
    else:
        missing.append(f"distress news: {distress.get('note')}")
    checks.append("distress news (insolvency, NCLT, layoffs)")

    if dont:
        v = "DONT_PURSUE"
    elif ident.get("status") != "RESOLVED" or not prof:
        v = "CANNOT_VERIFY"
    elif caution:
        v = "PURSUE_WITH_CAUTION"
    else:
        v = "PURSUE"
    reasons = dont + caution
    if v == "CANNOT_VERIFY":
        reasons = ["not enough evidence to judge; a person decides"] + reasons
    if v == "PURSUE":
        reasons = ["no red flags in the checks that ran"]
    return {"verdict": v, "reasons": reasons, "evidence": evidence, "missing": missing, "checks": checks,
            "not_checked_today": ["senior departures (roadmap)", "GST and MCA registry status (portal access not confirmed)"]}
