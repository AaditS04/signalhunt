"""The whole service on fixtures, in one run: find the freight, trust the party, set terms, close the deal.

Three new sites in the client's capacity states:
  * Orbit Pharma plant in Sriperumbudur, operating, hiring there, staff in Maharashtra -> priced OFFER on CHE-PUN
  * Nova Steel MoU for Chakan, no ground evidence                                   -> WATCHING, no draft
  * Kia Components warehouse in Oragadam (captive logistics group)                   -> REFUSED, no draft
Then: an outside address is blocked, the offer goes to a teammate, the reply is verified, the meeting is
booked once, and the lane shows OFFERED on the capacity board. Mock data only here (event rule).
"""
from __future__ import annotations

import pytest
from conftest import days_ago

from signalhunt import approvals, replies, workflow
from signalhunt.agents import ladder
from signalhunt.agents.identity import norm_name
from signalhunt.sources import crustdata

ARTS = [
    {"id": "t1", "url": "https://bl.example/orbit", "publisher": "Business Line", "published": days_ago(3),
     "title": "Orbit Pharma inaugurates plant in Sriperumbudur",
     "text": "Orbit Pharma Limited has inaugurated a new manufacturing plant in Sriperumbudur, Tamil Nadu, with an "
             "investment of Rs 300 crore.\nThe plant will supply hospitals across western India."},
    {"id": "t2", "url": "https://et.example/nova", "publisher": "Economic Times", "published": days_ago(4),
     "title": "Nova Steel signs MoU for Chakan plant",
     "text": "Nova Steel Limited has signed an MoU with the Maharashtra government to set up a manufacturing plant in "
             "Chakan, Maharashtra.\nThe company plans to invest Rs 900 crore."},
    {"id": "t3", "url": "https://ht.example/kia", "publisher": "Hindu Times", "published": days_ago(2),
     "title": "Kia Components opens warehouse in Oragadam",
     "text": "Kia Components India Limited has inaugurated a new warehouse in Oragadam, Tamil Nadu, with 1 lakh sq ft "
             "of space.\nIt will serve dealers across India."},
]
IDS = {"orbit pharma": 11, "nova steel": 22, "kia components india": 33}
REQUEST = ("Find companies in Tamil Nadu and Maharashtra that have opened or announced new plants, warehouses or "
           "distribution centres in the last 30 days and could need truck capacity or warehouse space.")


@pytest.fixture
def articles():
    return ARTS


@pytest.fixture
def live_like(fake_sources, monkeypatch):
    monkeypatch.setenv("CRUSTDATA_API_KEY", "test-only")
    monkeypatch.setenv("ALLOWED_RECIPIENTS", "@team.example")
    monkeypatch.setenv("ROLEPLAY_SENDERS", "buyer@team.example")

    def identify(ctx, name=None, domain=None):
        for k, cid in IDS.items():
            if norm_name(name).startswith(k):
                return [{"crustdata_company_id": cid, "name": name, "primary_domain": f"c{cid}.example",
                         "all_domains": [], "employee_count_range": "501-1000", "confidence_score": 1}]
        return []

    monkeypatch.setattr(crustdata, "identify_company", identify)
    monkeypatch.setattr(crustdata, "search_decision_makers", lambda ctx, cid, limit=10: [])
    monkeypatch.setattr(crustdata, "company_profile", lambda ctx, cid: {
        "headcount_total": 900, "growth_percent": {"six_months": 4.0},
        "by_region_absolute": {"Maharashtra, India": 60, "Tamil Nadu, India": 300}})
    monkeypatch.setattr(crustdata, "job_postings", lambda ctx, cid, since, limit=50: {"postings": [
        {"title": "Plant Supervisor", "location_raw": "Sriperumbudur, Tamil Nadu, India", "country": "IND",
         "url": "https://jobs.example/1", "date_added": days_ago(5)}] if cid == 11 else []})
    return fake_sources


def _run():
    rid = workflow.create_run(REQUEST)
    workflow.execute_run(rid)
    return workflow.run_result(rid)


def test_each_lead_gets_the_right_decision(live_like):
    res = _run()
    assert res["status"] == "COMPLETED"
    by = {o["company_name"]: o for o in res["opportunities"]}
    orbit, nova, kia = by["Orbit Pharma"], by["Nova Steel"], by["Kia Components"]
    assert orbit["outreach"]["status"] == "OFFER" and orbit["outreach"]["asset_id"] == "CHE-PUN"
    assert orbit["lane_match"]["match_strength"] == "BOTH_ENDS" and orbit["trust"]["verdict"] == "PURSUE"
    assert nova["outreach"]["status"] == "WATCHING" and nova["ladder"]["rung"] == "ANNOUNCED"
    assert kia["outreach"]["status"] == "REFUSED" and kia["trust"]["verdict"] == "DONT_PURSUE"
    drafted = {a["opp_id"] for a in approvals.list_actions()}
    assert orbit["id"] in drafted and nova["id"] not in drafted and kia["id"] not in drafted


def test_offer_to_booking_happens_once_and_locks_the_lane(live_like):
    res = _run()
    orbit = next(o for o in res["opportunities"] if o["company_name"] == "Orbit Pharma")
    act = next(a for a in approvals.list_actions() if a["opp_id"] == orbit["id"])
    body = act["payload"]["body"]
    assert "INR 52,000 per trip" in body and "45 days" in body and "MSMED Act" in body
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(act["id"], "Deepanshu", "someone@gmail.com")
    assert approvals.approve(act["id"], "Deepanshu", "buyer@team.example")["status"] == "SENT"
    r = replies.record_reply(act["id"], "buyer@team.example", "Thursday 3 PM works for us")
    assert r["verdict"] == "BOOKED" and r["detail"]["sender_check"]["status"] == "ROLE_PLAY"
    assert replies.record_reply(act["id"], "buyer@team.example", "Thursday 3 PM works for us")["replayed"]
    assert len(list(approvals.OUTBOX.glob("meeting_*.ics"))) == 1
    board = {b["asset_id"]: b["status"] for b in workflow.capacity_board()}
    assert board == {"CHE-PUN": "OFFERED", "CHK-COLD": "OPEN"}


@pytest.mark.parametrize("headline", ["Kia Components opens warehouse in Oragadam",
                                      "Acme opened its plant in Sanand"])
def test_present_tense_opening_counts_as_operating(headline):
    opp = {"headline_quote": headline, "what_happened": "", "event_status": "unclear", "state": "Tamil Nadu",
           "cities": [], "evidence": [{}]}
    assert ladder.place(opp, {"jobs": None, "missing": ["n/a"]}, {})["rung"] == "OPERATING"


def test_future_opening_is_not_operating():
    opp = {"headline_quote": "Acme plans to open a plant in Sanand next year", "what_happened": "",
           "event_status": "announced_planned", "state": "Gujarat", "cities": [], "evidence": [{}]}
    assert ladder.place(opp, {"jobs": None, "missing": ["n/a"]}, {})["rung"] == "ANNOUNCED"
