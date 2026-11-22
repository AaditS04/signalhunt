"""Reply impostor check and exactly-once meeting booking."""
from __future__ import annotations

from datetime import date

import pytest

from signalhunt import approvals, db, replies, workflow

MON = date(2026, 11, 23)  # a Monday


def sent_offer(opp_id="opp_r1", asset="CHE-PUN", domains=("orbit.example",)):
    opp = {"id": opp_id, "entity_key": f"cd:{opp_id}", "company_name": "Orbit Pharma", "score": 70, "priority": "HIGH",
           "verification": "VERIFIED",
           "identity": {"status": "RESOLVED", "company": {"primary_domain": domains[0] if domains else None,
                                                          "all_domains": list(domains)}}}
    workflow._save_opportunity("run_x", opp)
    a = approvals.create_draft(opp_id, None, "Offer", "Body", None, {"kind": "OFFER", "asset_id": asset})
    return approvals.approve(a["id"], "Deepanshu", "buyer.teammate@example.org")


def test_parse_meeting_time():
    assert replies.parse_meeting_time("Thursday 3 PM works", MON) == "2026-11-26T15:00"
    assert replies.parse_meeting_time("tomorrow at 11:30", MON) == "2026-11-24T11:30"
    assert replies.parse_meeting_time("sounds good, call me", MON) is None


def test_genuine_reply_books_once_even_after_crash(monkeypatch):
    a = sent_offer()
    def boom(name):
        if name == "after_ics":
            raise RuntimeError("simulated kill")
    monkeypatch.setattr(replies, "_crash_point", boom)
    with pytest.raises(RuntimeError):
        replies.record_reply(a["id"], "asha@orbit.example", "Thursday 3 PM works", MON)
    monkeypatch.setattr(replies, "_crash_point", lambda n: None)
    r1 = replies.record_reply(a["id"], "asha@orbit.example", "Thursday 3 PM works", MON)
    r2 = replies.record_reply(a["id"], "asha@orbit.example", "Thursday 3 PM works", MON)
    assert r1["verdict"] == "BOOKED" and r2.get("replayed")
    assert len(list(approvals.OUTBOX.glob("meeting_*.ics"))) == 1
    assert len(replies.list_replies()) == 1
    board = {b["asset_id"]: b for b in workflow.capacity_board()}
    assert board["CHE-PUN"]["status"] == "OFFERED"


def test_free_mail_reply_held():
    a = sent_offer()
    r = replies.record_reply(a["id"], "orbit.planthead@gmail.com", "Thursday 3 PM works", MON)
    assert r["verdict"] == "HELD_IMPOSTOR_RISK" and not list(approvals.OUTBOX.glob("meeting_*.ics"))


def test_mismatched_domain_held():
    a = sent_offer()
    assert replies.record_reply(a["id"], "x@orbit-pharma.co", "Thursday 3 PM", MON)["verdict"] == "HELD_DOMAIN_MISMATCH"


def test_roleplay_sender_accepted_and_labelled(monkeypatch):
    monkeypatch.setenv("ROLEPLAY_SENDERS", "buyer.teammate@example.org")
    a = sent_offer()
    r = replies.record_reply(a["id"], "buyer.teammate@example.org", "Friday 11 am", MON)
    assert r["verdict"] == "BOOKED" and r["detail"]["sender_check"]["status"] == "ROLE_PLAY"


def test_capacity_not_promised_twice(monkeypatch):
    monkeypatch.setenv("ROLEPLAY_SENDERS", "buyer.teammate@example.org")
    a1 = sent_offer("opp_r1")
    a2 = sent_offer("opp_r2")
    assert replies.record_reply(a1["id"], "buyer.teammate@example.org", "Thursday 3 PM", MON)["verdict"] == "BOOKED"
    assert replies.record_reply(a2["id"], "buyer.teammate@example.org", "Friday 4 PM", MON)["verdict"] == "HELD_CAPACITY"


def test_reply_needs_a_sent_offer():
    workflow._save_opportunity("run_x", {"id": "opp_p", "entity_key": "k", "company_name": "X", "score": 1,
                                          "priority": "LOW", "verification": "VERIFIED"})
    a = approvals.create_draft("opp_p", None, "S", "B", None)
    with pytest.raises(replies.ReplyError):
        replies.record_reply(a["id"], "a@b.com", "Thursday 3 PM", MON)
