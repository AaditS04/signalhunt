"""Payment terms chosen by rule before the deal, from evidence, so the 3PL is not left chasing money later.

Tiers: STANDARD_CREDIT, PART_ADVANCE, ADVANCE_ONLY. Every tier carries its reasons. This is an
assessment of payment risk, not a prediction.
"""
from __future__ import annotations

import re

PSU_RE = re.compile(r"corporation of india|\bbharat\b.*limited|\bhindustan\b.*limited|\bindian oil\b|\bnhpc\b|\bntpc\b|"
                    r"\bongc\b|\bsail\b|\bbhel\b|\bgail\b|government of|\bstate\b.*corporation", re.I)
LARGE_RANGES = ("5001-10000", "10001+", "10,001+", "5,001-10,000")


def decide(opp: dict, client: dict, trust: dict, signals: dict) -> dict:
    pol = client.get("payment_policy") or {}
    credit = int(pol.get("default_credit_days", 45))
    adv = 0
    tier = "STANDARD_CREDIT"
    reasons, clauses = [], []
    name = opp.get("company_name") or ""
    v = trust.get("verdict")
    if name in client.get("psu_buyers", []) or PSU_RE.search(name):
        reasons.append(f"government or PSU buyer: expect a payment cycle near {pol.get('psu_expected_cycle_days', 90)} days "
                       "(PSUs and government owe about 40 percent of delayed MSME payments)")
        tier = "PART_ADVANCE"
        adv = int(pol.get("advance_pct_on_caution", 30))
    if v == "PURSUE_WITH_CAUTION":
        tier, adv = "PART_ADVANCE", int(pol.get("advance_pct_on_caution", 30))
        reasons.append("trust check raised a caution: " + "; ".join(trust.get("reasons", []))[:200])
    if v == "CANNOT_VERIFY":
        tier, adv = "PART_ADVANCE", int(pol.get("advance_pct_on_caution", 30))
        reasons.append("buyer could not be fully verified; part advance until a person confirms")
    if any("distress" in r for r in trust.get("reasons", [])):
        tier, adv = "ADVANCE_ONLY", 100
        reasons.append("distress news about the buyer")
    rng = ((opp.get("identity") or {}).get("company") or {}).get("employee_count_range") or ""
    total = ((signals or {}).get("profile") or {}).get("headcount_total") or 0
    if rng in LARGE_RANGES or total >= 5000:
        clauses.append(f"Credit period is fixed at {credit} days; no extension without a written amendment.")
        reasons.append("much larger buyer: fixed terms so the credit period cannot drift")
    if pol.get("msme_registered"):
        clauses.append(f"As an MSME-registered supplier, payment is due within {min(credit, 45)} days of acceptance "
                       "under the MSMED Act, 2006.")
        credit = min(credit, 45)
    if tier == "STANDARD_CREDIT" and not reasons:
        reasons.append("no risk signal: the client's default terms apply")
    return {"tier": tier, "credit_days": credit, "advance_pct": adv, "clauses": clauses, "reasons": reasons,
            "label": "assessment of payment risk, not a prediction"}
