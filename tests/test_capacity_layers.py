"""Capacity-first layers: lane matching, reality ladder, worth-winning verdict, payment terms, the offer.
All inputs are fixtures (mock data only in tests)."""
from __future__ import annotations

import pytest

from signalhunt import approvals
from signalhunt.agents import ladder, lanes, reporter, terms, trust
from signalhunt.config import client_profile

TODAY = "2026-11-22"


def sig(regions=None, postings=None, growth=5.0, total=1200, available=True):
    return {"available": available, "retrieved_on": TODAY, "missing": [] if available else ["no records"],
            "job_window_days": 60,
            "profile": {"headcount_total": total, "growth_percent": {"six_months": growth},
                        "by_region_absolute": regions or {}} if available else None,
            "jobs": {"postings": postings or []} if available else None}


def opp(state="Tamil Nadu", cities=("Sriperumbudur",), name="Orbit Pharma", status="completed_or_inaugurated",
        quote="Orbit Pharma has inaugurated a plant in Sriperumbudur.", industry="pharmaceuticals", resolved=True):
    ident = ({"status": "RESOLVED", "company": {"crustdata_company_id": 1, "name": name, "primary_domain": "orbit.example",
                                                 "all_domains": ["orbit.example"], "employee_count_range": "501-1000"},
              "lookalikes": []} if resolved else {"status": "NOT_FOUND", "note": "no exact match", "lookalikes": []})
    return {"id": "o1", "company_name": name, "state": state, "cities": list(cities), "industry": industry,
            "event_status": status, "headline_quote": quote, "what_happened": quote, "identity": ident,
            "investment": "Rs 300 crore", "event_label": "Manufacturing expansion", "family": "facility",
            "decision_makers": {"people": [{"name": "Asha Rao", "title": "Head of Supply Chain"}]},
            "evidence": [{"field": "event", "value": quote, "quote": quote, "published": "2026-11-18",
                          "source_name": "Business Line", "source_url": "https://news.example/a", "verified": True}]}


CLIENT = client_profile()


# ---------------- lanes
def test_lane_both_ends_when_company_present_at_destination():
    m = lanes.match(opp(), CLIENT, sig(regions={"Maharashtra, India": 140}))
    lane = next(x for x in m["all"] if x["asset_id"] == "CHE-PUN")
    assert lane["strength"] == "BOTH_ENDS" and lane["direction"] == "outbound from new site"
    assert lane["evidence"][0]["field"].startswith("headcount.by_region_absolute")
    assert m["match_strength"] == "BOTH_ENDS"


def test_lane_origin_only_without_destination_evidence():
    m = lanes.match(opp(), CLIENT, sig(regions={"Tamil Nadu, India": 300}))
    assert m["match_strength"] == "ORIGIN_ONLY"


def test_lane_no_match_outside_capacity():
    m = lanes.match(opp(state="Gujarat", cities=("Sanand",)), CLIENT, sig())
    assert m["match_strength"] == "NONE" and m["best"] is None


def test_cluster_alias_maps_to_hub_and_cold_space_needs_cold_industry():
    m = lanes.match(opp(state="Maharashtra", cities=("Chakan",)), CLIENT, sig(regions={"Chennai": 40}))
    space = next(x for x in m["all"] if x["asset_id"] == "CHK-COLD")
    assert space["strength"] == "BOTH_ENDS" and "same cluster" in space["reasons"][0]
    steel = lanes.match(opp(state="Maharashtra", cities=("Chakan",), industry="steel", name="Nova Steel",
                            quote="Nova Steel has inaugurated a plant in Chakan."), CLIENT, sig())
    assert next(x for x in steel["all"] if x["asset_id"] == "CHK-COLD")["strength"] == "ORIGIN_ONLY"
    assert lanes.hub("Oragadam") == "Chennai" and lanes.hub("Talegaon") == "Pune"


def test_lookalike_place_posting_is_rejected():
    # 'Pune' fuzzy-matched a US posting in our live test; it must not count as presence in Maharashtra
    p = [{"title": "Warehouse Associate", "location_raw": "Pine Brook, NJ 07058", "country": "USA", "date_added": TODAY}]
    assert lanes.presence(sig(postings=p), "Maharashtra", ["Pune"])["status"] == "NOT_FOUND"


# ---------------- ladder
def test_mou_only_is_watching():
    o = opp(status="announced_planned", quote="Orbit Pharma signed an MoU to set up a plant in Sriperumbudur.")
    r = ladder.place(o, sig(), CLIENT)
    assert r["rung"] == "ANNOUNCED" and r["watching"] is True


def test_hiring_on_ground_proceeds():
    o = opp(status="announced_planned", quote="Orbit Pharma will set up a plant in Sriperumbudur.")
    p = [{"title": "Plant Manager", "location_raw": "Sriperumbudur, Tamil Nadu", "country": "IND",
          "date_added": "2026-11-10", "url": "https://jobs.example/1"}]
    r = ladder.place(o, sig(postings=p), CLIENT)
    assert r["rung"] == "HIRING_ON_GROUND" and r["watching"] is False
    assert any(e.get("kind") == "job_posting" and e["record_date"] for e in r["evidence"])


# ---------------- trust
NO_NEWS = {"checked": True, "hits": []}


def test_trust_pursue():
    assert trust.verdict(opp(), CLIENT, sig(), NO_NEWS)["verdict"] == "PURSUE"


def test_trust_captive_group_refused():
    v = trust.verdict(opp(name="Mahindra Electric"), CLIENT, sig(), NO_NEWS)
    assert v["verdict"] == "DONT_PURSUE" and "own logistics" in v["reasons"][0]


def test_trust_shrinking_team():
    assert trust.verdict(opp(), CLIENT, sig(growth=-30), NO_NEWS)["verdict"] == "DONT_PURSUE"
    assert trust.verdict(opp(), CLIENT, sig(growth=-8), NO_NEWS)["verdict"] == "PURSUE_WITH_CAUTION"


def test_trust_insolvency_news_refused():
    news = {"checked": True, "hits": [{"title": "Orbit Pharma admitted to NCLT insolvency", "url": "https://n.example",
                                       "record_date": "2026-10-01", "hard": True}]}
    assert trust.verdict(opp(), CLIENT, sig(), news)["verdict"] == "DONT_PURSUE"


def test_thin_evidence_is_cannot_verify_never_refused():
    v = trust.verdict(opp(resolved=False), CLIENT, sig(available=False), NO_NEWS)
    assert v["verdict"] == "CANNOT_VERIFY" and v["missing"]


# ---------------- terms
@pytest.mark.parametrize("name,verdict,growth,tier", [
    ("Orbit Pharma", "PURSUE", 5, "STANDARD_CREDIT"),
    ("Orbit Pharma", "PURSUE_WITH_CAUTION", -8, "PART_ADVANCE"),
    ("Orbit Pharma", "CANNOT_VERIFY", 5, "PART_ADVANCE"),
    ("Container Corporation of India", "PURSUE", 5, "PART_ADVANCE"),
])
def test_terms_table(name, verdict, growth, tier):
    t = terms.decide(opp(name=name), CLIENT, {"verdict": verdict, "reasons": []}, sig(growth=growth))
    assert t["tier"] == tier
    assert any("MSMED Act" in c for c in t["clauses"]) and t["credit_days"] <= 45


# ---------------- the offer
def test_offer_numbers_come_only_from_facts_and_profile():
    o = opp()
    o["lane_match"] = lanes.match(o, CLIENT, sig(regions={"Maharashtra, India": 140}))
    o["terms"] = terms.decide(o, CLIENT, {"verdict": "PURSUE", "reasons": []}, sig())
    rep = reporter.offer(o, CLIENT)
    allowed = " ".join([str(CLIENT), str(o["terms"]), *(f["quote"] + " " + f["published"] for f in o["evidence"])])
    assert reporter.guard_numbers(rep["email_body"] + " " + rep["email_subject"], allowed) == []
    lane = CLIENT["capacity"]["lanes"][0]
    assert rep["offer"]["price_inr"] >= lane["floor_inr_per_trip"]
    assert "52,000" in rep["email_body"] and "Thursday" in rep["email_body"] and "Dear Asha" in rep["email_body"]


# ---------------- contact rule
def test_recipient_allow_list(monkeypatch):
    monkeypatch.setenv("ALLOWED_RECIPIENTS", "buyer.teammate@example.org,@pilani.bits-pilani.ac.in")
    assert approvals.recipient_allowed("buyer.teammate@example.org")
    assert approvals.recipient_allowed("f2022@pilani.bits-pilani.ac.in")
    assert not approvals.recipient_allowed("ceo@realcompany.com")
    a = approvals.create_draft("opp_x", None, "Hi", "Body", None)
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(a["id"], "Deepanshu", "ceo@realcompany.com")


def test_generic_company_name_is_never_refused_on_news(monkeypatch):
    # live finding: the extractor produced a company called "Group" and unrelated "Group" headlines matched
    monkeypatch.setenv("TRUST_NEWS_CHECK", "1")
    d = trust.distress_news(None, "Group")
    assert d["checked"] is False and "generic" in d["note"]
    v = trust.verdict(opp(name="Group", resolved=False), CLIENT, sig(available=False), d)
    assert v["verdict"] == "CANNOT_VERIFY"


def test_cold_chain_fit_reads_company_name_when_industry_is_generic():
    # live finding: "Anuh Pharma" arrived with industry "manufacturing"
    o = opp(state="Maharashtra", cities=("Palghar",), name="Anuh Pharma", industry="manufacturing",
            quote="Anuh Pharma has inaugurated a new unit in Palghar.")
    space = next(x for x in lanes.match(o, CLIENT, sig())["all"] if x["asset_id"] == "CHK-COLD")
    assert "'pharma'" in space["reasons"][1]


def test_land_allotment_counts_as_approved():
    o = opp(status="unclear", quote="Sugs Lloyd Limited has secured the allotment of 3,342 square metres of industrial land in Noida.")
    assert ladder.place(o, sig(), CLIENT)["rung"] == "FUNDED_OR_APPROVED"


def test_distress_headline_must_name_this_company_not_a_longer_name():
    # live finding: "Arvind" (Varanasi plant) was refused on a headline about Arvind Fashions
    assert not trust.names_company("Arvind Fashions ARVINDFASN: NCLT Approves Rs 1438 Crore Capital Reduction", "Arvind")
    assert trust.names_company("Arvind admitted to NCLT insolvency", "Arvind")
    assert trust.names_company("Orbit Pharma Ltd files for bankruptcy", "Orbit Pharma")


def test_nclt_restructuring_is_not_distress():
    assert not trust.DISTRESS_HARD.search("NCLT Approves Rs 1438 Crore Capital Reduction")
    assert not trust.DISTRESS_HARD.search("NCLT approves merger of two subsidiaries")
    assert trust.DISTRESS_HARD.search("NCLT admits insolvency plea against Orbit Pharma")
    assert trust.DISTRESS_HARD.search("Orbit Pharma admitted to CIRP")


@pytest.mark.parametrize("title,company,subject", [
    # live wrong refusals (22 Nov 2026): the company is a creditor, an associate, or cleared
    ("NCLT Hyderabad Condones NTPC's 408-Day Delay, Directs Liquidator", "NTPC Limited", False),
    ("Ashok Leyland associate ALJD liquidated after NCLT order", "Ashok Leyland", False),
    ("Orissa High Court quashes pollution case against Tata Steel over liquidation claim", "Tata Steel", False),
    # real distress about the company itself
    ("Orbit Pharma admitted to insolvency by NCLT", "Orbit Pharma", True),
    ("NCLT admits insolvency plea against Orbit Pharma", "Orbit Pharma", True),
    ("Orbit Pharma files for bankruptcy", "Orbit Pharma", True),
])
def test_news_refusal_needs_the_company_as_subject(title, company, subject):
    assert trust.is_subject_of_insolvency(title, company) is subject


def test_unclear_distress_headline_is_a_caution_not_a_refusal():
    news = {"checked": True, "hits": [{"title": "Ashok Leyland associate ALJD liquidated after NCLT order",
                                       "url": "https://n.example", "record_date": "2026-11-01", "hard": False}]}
    v = trust.verdict(opp(name="Ashok Leyland"), CLIENT, sig(), news)
    assert v["verdict"] == "PURSUE_WITH_CAUTION" and "a person reads it" in v["reasons"][0]


def test_fragments_and_divisions_are_not_companies():
    from signalhunt.agents import rule_extractor as rx
    assert rx.clean_company("and Mysuru") == "" and rx.clean_company("Aluminium Division") == ""
    assert rx.clean_company("Continuous Sandwich PUF Panel Manufacturing Line The") == ""
    assert rx.clean_company("Reliance Group") == "Reliance Group"
