"""End-to-end workflow on fixture sources: normal discovery, duplicates, lookalikes, outdated,
missing evidence, contradictions, source failure, geography, rivals, repeated execution."""
from __future__ import annotations

from conftest import GUJ_REQUEST

from signalhunt import approvals, workflow


def run(request=GUJ_REQUEST):
    rid = workflow.create_run(request)
    workflow.execute_run(rid)
    return workflow.run_result(rid)


def by_company(res):
    return {o["company_name"]: o for o in res["opportunities"]}


def reasons(res, bucket):
    return " | ".join(f"{c['company']}: {c['reason']}" for c in res[bucket])


def test_normal_discovery_produces_sourced_opportunity(fake_sources):
    res = run()
    assert res["status"] == "COMPLETED"
    opp = by_company(res)["Acme Foods"]
    assert opp["state"] == "Gujarat" and opp["cities"] == ["Sanand"]
    assert opp["event_type"] == "WAREHOUSE_EXPANSION"
    # every fact carries url, exact quote, date and retrieval time
    for f in opp["evidence"]:
        assert f["verified"] and f["source_url"].startswith("https://") and f["quote"] and f["published"] and f["retrieved_at"]
    assert opp["scale"] == "2 lakh sq ft"
    assert 0 < opp["score"] <= 100 and len(opp["scoring"]["breakdown"]) == 7
    # inference is labelled as such, never as fact
    assert "inference" in opp["report"]["why_3pl_demand"].lower() or "may" in opp["report"]["why_3pl_demand"].lower()
    # an outreach draft exists and waits for approval
    acts = [a for a in approvals.list_actions() if a["opp_id"] == opp["id"]]
    assert len(acts) == 1 and acts[0]["status"] == "PENDING_APPROVAL"


def test_duplicate_event_and_listing_merged(fake_sources):
    res = run()
    acme = [o for o in res["opportunities"] if o["company_name"] == "Acme Foods"]
    assert len(acme) == 1, "two outlets reporting one event must give ONE opportunity"
    assert sorted(acme[0]["independent_sources"]) == ["am.example", "gbt.example"]
    assert res["progress"]["duplicate_listings_removed"] >= 1  # same URL with utm params
    assert any(d["company"] == "Acme Foods" for d in res["duplicates"])


def test_lookalike_company_not_merged(fake_sources):
    res = run()
    names = by_company(res)
    india = [n for n in names if n.startswith("Acme Foods India")]
    assert "Acme Foods" in names and len(india) == 1
    assert names["Acme Foods"]["entity_key"] != names[india[0]]["entity_key"]
    assert names["Acme Foods"]["cities"] == ["Sanand"] and names[india[0]]["cities"] == ["Surat"]


def test_outdated_record_rejected(fake_sources):
    res = run()
    assert "Zenith" not in str([o["company_name"] for o in res["opportunities"]])
    assert any("before window start" in c["reason"] or "OUTDATED" in c["reason"] for c in res["rejected"])


def test_missing_location_is_cannot_verify(fake_sources):
    res = run()
    cv = [c for c in res["cannot_verify"] if c["company"] == "Orbit Pharma"]
    assert cv and "location" in cv[0]["reason"]
    assert "Orbit Pharma" not in by_company(res)


def test_contradictory_amounts_flagged(fake_sources):
    res = run()
    nova = by_company(res)["Nova Steel"]
    assert nova["conflicts"] and nova["conflicts"][0]["field"] == "investment"
    assert nova["investment"] is None, "no single amount may be reported when sources disagree"
    assert any("investment" in u and "CANNOT_VERIFY" in u for u in nova["unverified"])
    assert nova["confidence"] != "HIGH"


def test_out_of_geography_and_rival_rejected(fake_sources):
    res = run()
    names = by_company(res)
    assert "Delta Motors" not in names and not any("Ritco" in n for n in names)
    txt = reasons(res, "rejected")
    assert "outside requested geography" in txt
    assert "exclusion list" in txt or "logistics provider" in txt


def test_source_failure_logged_not_invented(fake_sources):
    res = run()
    assert any("source unavailable" in c["reason"] for c in res["cannot_verify"])
    assert res["status"] == "COMPLETED"


def test_repeated_execution_is_idempotent(fake_sources):
    r1 = run()
    n_actions = len(approvals.list_actions())
    r2 = run()
    assert sorted(o["id"] for o in r1["opportunities"]) == sorted(o["id"] for o in r2["opportunities"])
    from signalhunt import db
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == len(r1["opportunities"])
    assert len(approvals.list_actions()) == n_actions, "a re-run must not create new outreach for the same opportunity"


def test_requirement_change_window_and_geography(fake_sources):
    # narrower window: 4 days -> Nova (5-6 days old) and Acme India (5 days) drop out
    res = run("Find companies in Gujarat with warehouse or manufacturing expansion in the last 4 days")
    assert res["spec"]["window_days"] == 4
    names = by_company(res)
    assert "Acme Foods" in names and "Nova Steel" not in names
    # different geography: Maharashtra -> Delta Motors qualifies, Gujarat companies do not
    res2 = run("Find companies in Maharashtra that opened a new plant in the last 30 days")
    names2 = by_company(res2)
    assert "Delta Motors" in names2 and "Acme Foods" not in names2


def test_requirement_change_client_exclusion(fake_sources, monkeypatch):
    import signalhunt.workflow as wf
    base = wf.client_profile()
    monkeypatch.setattr(wf, "client_profile", lambda: {**base, "excluded_companies": base["excluded_companies"] + ["Nova Steel"]})
    res = run()
    assert "Nova Steel" not in by_company(res)
    assert "exclusion list (Nova Steel)" in reasons(res, "rejected")


def test_progress_counts_every_article(fake_sources):
    res = run()
    p = res["progress"]
    # article-level rejections (no event, unreadable source) are counted too, not only verifier outcomes
    assert p["verified"] + p["cannot_verify"] + p["rejected"] >= p["articles_done"] > 0
