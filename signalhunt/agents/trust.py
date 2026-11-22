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

# NCLT also approves mergers and capital reductions (restructuring, not distress): it counts only with an
# insolvency word. Live finding: "NCLT Approves Rs 1438 Crore Capital Reduction" refused a healthy buyer.
DISTRESS_HARD = re.compile(r"insolven|bankrupt|liquidat|winding[- ]up|\bcirp\b|\bibc\b|default(?:ed|s)? on|"
                           r"\bnclt\b[^.]{0,40}\b(?:admit\w*|petition|plea)|(?:admit\w*|petition|plea)[^.]{0,40}\bnclt\b", re.I)
# A headline word right after the name that is part of a LONGER company name ("Arvind Fashions" is not
# "Arvind"). Words that commonly follow a company name in a headline are allowed.
HEADLINE_WORDS = {"ltd", "limited", "pvt", "private", "inc", "corp", "group", "india", "admitted", "admits", "files",
                  "filed", "faces", "facing", "gets", "got", "moves", "seeks", "enters", "under", "to", "in", "shares",
                  "stock", "says", "announces", "lays", "cuts", "shuts", "defaults", "declared", "dragged", "hit", "hits",
                  "sued", "receives", "reports", "posts", "slips", "plunges", "slumps", "insolvency", "nclt", "bankruptcy",
                  "layoffs", "plans", "may", "will", "set", "is", "has", "on", "for", "after", "amid", "q1", "q2", "q3", "q4"}
DISTRESS_SOFT = re.compile(r"layoff|lay off|job cuts|retrench|shut(?:s|ting)? down|closure|plant shut", re.I)
GENERIC_WORDS = {"group", "india", "company", "industries", "enterprises", "limited", "holdings", "global",
                 "international", "the", "and", "of", "private", "ltd", "pvt"}
SHRINK_DONT = -20.0
SHRINK_CAUTION = -5.0


def names_company(title: str, company: str) -> bool:
    """True only if the headline names this company, not a longer name that starts with it."""
    target = norm_name(company)
    nt = norm_name(title)
    m = re.search(rf"\b{re.escape(target)}\b(?:\s+(\w+))?", nt)
    if not m:
        return False
    nxt = m.group(1)
    if nxt and nxt not in HEADLINE_WORDS:
        orig = re.search(rf"{re.escape(company.split()[-1])}\W+(\w+)", title, re.I)
        if orig and orig.group(1)[:1].isupper():
            return False  # "Arvind Fashions", "Tata Steel BSL": a different company
    return True


SUBJECT_VERBS = (r"(?:admitted|admits|files?|filed|enters?|entered|faces?|facing|under(?:goes)?|goes|went|declared|"
                 r"dragged|defaults?|defaulted|slips|moves?\s+to|taken\s+to|ordered\s+into|sent\s+(?:in)?to)")


def is_subject_of_insolvency(title: str, company: str) -> bool:
    """A refusal on news alone needs the headline to say THIS company is in insolvency: "<company> admitted to
    insolvency", "insolvency plea against <company>". Live findings that must NOT count: "NCLT ... condones NTPC's
    delay, directs liquidator" (NTPC is a creditor), "Ashok Leyland associate ALJD liquidated" (another entity),
    "court quashes case against Tata Steel". Those become a caution that a person reads."""
    if not DISTRESS_HARD.search(title):
        return False
    name = re.escape(company.strip())
    hard = r"(?:insolven\w*|bankrupt\w*|liquidat\w*|winding[- ]up|cirp|nclt|ibc)"
    direct = re.search(rf"\b{name}(?:'s)?\s+(?:\w+\s+){{0,1}}?{SUBJECT_VERBS}\b[^.]{{0,50}}\b{hard}", title, re.I)
    against = re.search(rf"\b{hard}\s+(?:\w+\s+){{0,2}}?(?:against|of|for)\s+{name}\b", title, re.I)
    return bool(direct or against)


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
            if not names_company(t, company):
                continue
            if DISTRESS_HARD.search(t) or DISTRESS_SOFT.search(t):
                hits.append({"title": t, "url": r.get("url"), "record_date": r.get("published"),
                             "publisher": r.get("publisher"), "hard": is_subject_of_insolvency(t, company)})
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
            if h.get("hard"):
                dont.append(f"distress news: \"{h['title'][:120]}\"")
            else:
                caution.append(f"possible distress in the news, a person reads it: \"{h['title'][:120]}\"")
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
