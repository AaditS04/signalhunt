"""Reality ladder: how real is an announced project on the ground?

  ANNOUNCED -> FUNDED_OR_APPROVED -> HIRING_ON_GROUND -> OPERATING

Indian investment MoUs convert poorly, so a headline alone is not demand. Only ground evidence
(hiring at the site, or operations started) earns outreach; anything lower is WATCHING.
"""
from __future__ import annotations

import re

from . import lanes

RUNGS = ["ANNOUNCED", "FUNDED_OR_APPROVED", "HIRING_ON_GROUND", "OPERATING"]
OPERATING_RE = re.compile(r"inaugurat|commissioned|began operations|commenced (?:operations|production)|"
                          r"started production|rolled out (?:its )?first|operational|opened", re.I)
FUNDED_RE = re.compile(r"approv|sanction|cleared|allot+ed land|land allot|allotment of [^.]{0,60}land|raised|secured (?:funding|financing)|"
                       r"bhoomi pujan|ground[- ]?breaking|foundation stone|construction (?:has )?(?:begun|started)", re.I)


def place(opp: dict, signals: dict, client: dict) -> dict:
    quote = " ".join(filter(None, [opp.get("headline_quote"), opp.get("what_happened")]))
    evidence, reasons = [], []
    rung = "ANNOUNCED"
    if opp.get("event_status") == "completed_or_inaugurated" or OPERATING_RE.search(quote):
        rung = "OPERATING"
        reasons.append("the source says the facility is open or operating")
    elif FUNDED_RE.search(quote):
        rung = "FUNDED_OR_APPROVED"
        reasons.append("the source reports approval, funding, land or construction")
    else:
        reasons.append("the source reports an announcement or plan only")
    src = (opp.get("evidence") or [{}])[0]
    evidence.append({"kind": "news", "value": (opp.get("headline_quote") or "")[:300], "url": src.get("source_url"),
                     "record_date": src.get("published")})
    jobs_ok = signals.get("jobs") is not None
    ground = lanes.presence({**signals, "profile": None, "available": jobs_ok,
                             "missing": signals.get("missing") or ["job postings not retrieved"]},
                            opp.get("state") or "", opp.get("cities") or [])
    if ground["status"] == "FOUND":
        evidence.extend(ground["evidence"])
        if RUNGS.index(rung) < RUNGS.index("HIRING_ON_GROUND"):
            rung = "HIRING_ON_GROUND"
        reasons.append(f"{len(ground['evidence'])} recent job posting(s) at or near the site "
                       f"(last {signals.get('job_window_days', 60)} days)")
    elif ground["status"] == "CANNOT_VERIFY":
        reasons.append(f"ground hiring not checked: {ground['missing']}")
    else:
        reasons.append("no job postings at the site in the company's records")
    min_rung = client.get("outreach_min_rung", "HIRING_ON_GROUND")
    watching = RUNGS.index(rung) < RUNGS.index(min_rung)
    return {"rung": rung, "rung_index": RUNGS.index(rung), "rungs": RUNGS, "watching": watching,
            "outreach_min_rung": min_rung, "reasons": reasons, "evidence": evidence,
            "label": "assessment from dated sources"}
