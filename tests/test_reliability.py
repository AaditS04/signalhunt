"""Crash recovery, approval enforcement, exactly-once delivery, API failures, cost/time limits,
identity rules and verifier rules."""
from __future__ import annotations

import httpx
import pytest
from conftest import GUJ_REQUEST

from signalhunt import approvals, db, net, workflow
from signalhunt.agents import identity, verifier
from signalhunt.runtime import LimitExceeded, RunContext


class HardKill(BaseException):
    """Escapes `except Exception` like a real kill -9 would stop the process mid-step."""


# ---------------------------------------------------------------- crash recovery
def test_kill_mid_run_then_resume_repeats_no_completed_work(fake_sources, monkeypatch):
    def crash(name):
        if name == "mid_articles":
            raise HardKill()
    monkeypatch.setattr(workflow, "_crash_point", crash)
    rid = workflow.create_run(GUJ_REQUEST)
    with pytest.raises(HardKill):
        workflow.execute_run(rid)
    assert workflow.get_run(rid)["status"] == "RUNNING"  # left as a real crash would leave it
    fetched_before = dict(fake_sources["fetch"])
    searches_before = fake_sources["search"]
    assert fetched_before, "some articles were processed before the kill"

    monkeypatch.setattr(workflow, "_crash_point", lambda name: None)
    workflow.execute_run(rid)  # restart
    run = workflow.get_run(rid)
    assert run["status"] == "COMPLETED" and run["attempts"] == 2 and run["progress"]["recovered"] == 1
    assert fake_sources["search"] == searches_before, "searches must not be repeated after restart"
    for url, n in fetched_before.items():
        assert fake_sources["fetch"][url] == n, f"{url} fetched again after restart"
    assert len(workflow.run_result(rid)["opportunities"]) >= 3


def test_kill_after_first_opportunity_no_duplicate_drafts(fake_sources, monkeypatch):
    monkeypatch.setattr(workflow, "_crash_point", lambda n: (_ for _ in ()).throw(HardKill()) if n == "after_first_opportunity" else None)
    rid = workflow.create_run(GUJ_REQUEST)
    with pytest.raises(HardKill):
        workflow.execute_run(rid)
    assert len(approvals.list_actions()) == 1
    monkeypatch.setattr(workflow, "_crash_point", lambda n: None)
    workflow.execute_run(rid)
    acts = approvals.list_actions()
    eligible = [o for o in workflow.run_result(rid)["opportunities"] if o["outreach"]["status"] in ("OFFER", "INTRO")]
    assert len(acts) == len({a["opp_id"] for a in acts}) == len(eligible)


# ---------------------------------------------------------------- approvals / exactly once
def _draft():
    return approvals.create_draft("opp_test", {"name": "A B", "title": "Head of Supply Chain"}, "Hi", "Body", None)


def test_send_requires_approval():
    a = _draft()
    assert approvals.send(a["id"])["status"] == "PENDING_APPROVAL"  # nothing sent
    assert not approvals.OUTBOX.exists() or not list(approvals.OUTBOX.iterdir())


def test_approval_requires_named_approver_and_typed_email():
    a = _draft()
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(a["id"], "", "buyer@acme.example")
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(a["id"], "Priya", "not-an-email")
    assert approvals.get(a["id"])["status"] == "PENDING_APPROVAL"


def test_approve_sends_exactly_once():
    a = _draft()
    out = approvals.approve(a["id"], "Priya", "buyer@acme.example")
    assert out["status"] == "SENT" and out["approved_by"] == "Priya"
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(a["id"], "Priya", "buyer@acme.example")  # second click
    approvals.send(a["id"])  # replay
    assert len(list(approvals.OUTBOX.iterdir())) == 1
    assert _draft()["id"] == a["id"], "same opportunity -> same action (idempotency key)"


def test_crash_while_sending_reconciles_once():
    a = _draft()
    with db.connect() as con:  # simulate: approved, claimed, process died before marking SENT
        con.execute("UPDATE actions SET status='SENDING', approved_by='Priya', payload_json=? WHERE id=?",
                    (db.dumps({"subject": "Hi", "body": "Body", "to_email": "b@acme.example"}), a["id"]))
    approvals.reconcile()
    approvals.reconcile()
    assert approvals.get(a["id"])["status"] == "SENT"
    assert len(list(approvals.OUTBOX.iterdir())) == 1


def test_rejected_action_cannot_be_sent():
    a = _draft()
    approvals.reject(a["id"], "Priya", "wrong contact")
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(a["id"], "Priya", "buyer@acme.example")
    assert approvals.get(a["id"])["status"] == "REJECTED"


# ---------------------------------------------------------------- source failures
def test_http_retries_then_fails_and_ledger_records(monkeypatch):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(503, text="down")

    monkeypatch.setattr(net, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    rid = workflow.create_run("test run for retries in Gujarat")
    ctx = RunContext(rid)
    with pytest.raises(net.SourceError):
        net.request(ctx, "flaky", "op", "GET", "https://flaky.example/x", retries=2)
    assert attempts["n"] == 3
    with db.connect() as con:
        st = [r["status"] for r in con.execute("SELECT status FROM ledger WHERE run_id=? ORDER BY id", (rid,))]
    assert st == ["retry", "retry", "error"]
    monkeypatch.setattr(net, "_client", None)


def test_circuit_breaker_skips_dead_provider(monkeypatch):
    monkeypatch.setattr(net, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    ctx = RunContext(None)
    for _ in range(3):
        with pytest.raises(net.SourceError):
            net.request(ctx, "dead", "op", "GET", "https://dead.example", retries=0)
    with pytest.raises(net.SourceError, match="circuit open"):
        net.request(ctx, "dead", "op", "GET", "https://dead.example", retries=0)
    monkeypatch.setattr(net, "_client", None)


def test_discovery_falls_back_to_next_provider(monkeypatch):
    from signalhunt.agents import discovery
    from signalhunt.sources import news_rss

    def bing_down(ctx, q):
        raise net.SourceError("bing_news_rss", "HTTP 503")

    monkeypatch.setattr(news_rss, "bing_news", bing_down)
    monkeypatch.setattr(news_rss, "google_news", lambda ctx, q, d: [{"title": "t", "url": "https://x.example/a", "snippet": "",
                                                                    "published": None, "provider": "google_news_rss"}])
    spec = {"window_start": "2026-10-23", "window_days": 30}
    r = discovery._search_one(RunContext(None), "q", spec)
    assert r["provider"].startswith("google_news_rss") and "503" in r["failures"][0]


# ---------------------------------------------------------------- limits
def test_cost_limit_stops_run_cleanly(fake_sources, monkeypatch):
    monkeypatch.setenv("MAX_RUN_COST_USD", "0.0001")
    rid = workflow.create_run(GUJ_REQUEST)
    RunContext(rid).record("anthropic", "seed", "ok", cost_usd=0.01)  # spend already above the limit
    workflow.execute_run(rid)
    run = workflow.get_run(rid)
    assert run["status"] == "STOPPED_LIMIT" and "cost limit" in (run["error"] or run["progress"].get("limit_reason", ""))


def test_llm_call_limit(monkeypatch):
    monkeypatch.setenv("MAX_LLM_CALLS_PER_RUN", "1")
    rid = workflow.create_run("limit test in Gujarat warehouses")
    ctx = RunContext(rid)
    ctx.record("anthropic", "x", "ok", cost_usd=0.0)
    with pytest.raises(LimitExceeded):
        ctx.check_llm_budget()


def test_time_limit(monkeypatch):
    monkeypatch.setenv("MAX_RUN_SECONDS", "5")
    ctx = RunContext(None, prior_active_seconds=10)
    with pytest.raises(LimitExceeded, match="time limit"):
        ctx.check_limits("x")


# ---------------------------------------------------------------- identity & verifier units
SERVE = [
    {"crustdata_company_id": 628895, "name": "Serve Robotics", "primary_domain": "serverobotics.com"},
    {"crustdata_company_id": 5825197, "name": "Site Serve Robotics", "primary_domain": "siteserverobotics.co.uk"},
    {"crustdata_company_id": 25592234, "name": "iServe Robotics", "primary_domain": "iserve.ai"},
]


def test_identity_exact_match_resolves_and_lists_lookalikes():
    d = identity.decide("Serve Robotics Inc", SERVE, "", "")
    assert d["status"] == "RESOLVED" and d["entity_key"] == "cd:628895"
    assert {l["name"] for l in d["lookalikes"]} == {"Site Serve Robotics", "iServe Robotics"}


def test_identity_similar_name_never_merged():
    d = identity.decide("Serve Robotic Systems", SERVE, "", "")
    assert d["status"] == "NOT_FOUND" and d["entity_key"].startswith("name:")
    assert identity.norm_name("Tata Steel Limited") == identity.norm_name("Tata Steel")
    assert identity.norm_name("Tata Steel BSL") != identity.norm_name("Tata Steel")


def test_identity_same_name_two_companies_ambiguous_unless_domain_in_source():
    twins = [{"crustdata_company_id": 1, "name": "Apex Foods", "primary_domain": "apexfoods.com"},
             {"crustdata_company_id": 2, "name": "Apex Foods", "primary_domain": "apexfoods.co.uk"}]
    assert identity.decide("Apex Foods", twins, "no domain here", "")["status"] == "AMBIGUOUS"
    d = identity.decide("Apex Foods", twins, "Visit apexfoods.co.uk for details", "")
    assert d["status"] == "RESOLVED" and d["entity_key"] == "cd:2"


def test_verifier_rejects_paraphrased_quote():
    art = {"url": "https://x.example", "title": "T", "text": "Acme Foods has opened a warehouse in Sanand, Gujarat.",
           "published": "2026-11-20"}
    spec = {"window_start": "2026-10-23", "window_end": "2026-11-22", "states": ["Gujarat"], "triggers": ["WAREHOUSE_EXPANSION"]}
    ev = {"company_name": "Acme Foods", "company_quote": "Acme Foods has opened a warehouse in Sanand, Gujarat.",
          "event_type": "WAREHOUSE_EXPANSION", "event_summary": "x", "event_quote": "Acme Foods opened a big new warehouse in Sanand",
          "location_city": "Sanand", "location_state": "Gujarat", "location_quote": "a warehouse in Sanand, Gujarat"}
    v = verifier.verify_event(ev, art, spec, [])
    assert v["status"] == "CANNOT_VERIFY" and any("event statement" in m for m in v["missing"])
    ev["event_quote"] = "Acme Foods has opened a warehouse in Sanand"
    assert verifier.verify_event(ev, art, spec, [])["status"] == "VERIFIED"


def test_verifier_value_must_be_in_its_quote():
    art = {"url": "https://x.example", "title": "T", "text": "Acme Foods has opened a warehouse in Sanand, Gujarat.",
           "published": "2026-11-20"}
    spec = {"window_start": "2026-10-23", "window_end": "2026-11-22", "states": ["Gujarat"], "triggers": ["WAREHOUSE_EXPANSION"]}
    ev = {"company_name": "Acme Foods", "company_quote": "Acme Foods has opened a warehouse in Sanand, Gujarat.",
          "event_type": "WAREHOUSE_EXPANSION", "event_summary": "x", "event_quote": "Acme Foods has opened a warehouse in Sanand",
          "location_city": "Rajkot", "location_state": "Gujarat", "location_quote": "a warehouse in Sanand, Gujarat"}
    v = verifier.verify_event(ev, art, spec, [])
    assert v["status"] == "CANNOT_VERIFY"  # Rajkot is not what the quote says


def test_verifier_undated_record_cannot_verify():
    art = {"url": "https://x.example", "title": "T", "text": "Acme Foods has opened a warehouse in Sanand, Gujarat.", "published": None}
    spec = {"window_start": "2026-10-23", "window_end": "2026-11-22", "states": ["Gujarat"], "triggers": ["WAREHOUSE_EXPANSION"]}
    ev = {"company_name": "Acme Foods", "company_quote": "Acme Foods has opened a warehouse in Sanand, Gujarat.",
          "event_type": "WAREHOUSE_EXPANSION", "event_summary": "x", "event_quote": "Acme Foods has opened a warehouse in Sanand",
          "location_city": "Sanand", "location_state": "Gujarat", "location_quote": "a warehouse in Sanand, Gujarat"}
    v = verifier.verify_event(ev, art, spec, [])
    assert v["status"] == "CANNOT_VERIFY" and any("record date" in m for m in v["missing"])


def test_report_guard_blocks_unsupported_numbers():
    from signalhunt.agents.reporter import guard_numbers
    assert guard_numbers("Your Rs 900 crore plant", "invest Rs 500 crore") == ["900"]
    assert guard_numbers("Your Rs 500 crore plant", "invest Rs 500 crore") == []


def test_identity_real_crustdata_shape_heller():
    """Shape of the live Crustdata identify response for 'Heller Industries' (22 Nov 2026)."""
    cands = [
        {"crustdata_company_id": 2091220, "name": "Heller Industries", "primary_domain": "hellerindustries.com", "confidence_score": 1.0},
        {"crustdata_company_id": 8152359, "name": "Heller Industries, Inc.", "primary_domain": None, "confidence_score": 0.8},
        {"crustdata_company_id": 4024, "name": "Reliance Industries Limited", "primary_domain": "ril.com", "confidence_score": 0.6},
        {"crustdata_company_id": 1554291, "name": "Heller", "primary_domain": "hellersearch.com", "confidence_score": 0.6},
    ]
    d = identity.decide("Heller Industries", cands, "", "")
    assert d["status"] == "RESOLVED" and d["entity_key"] == "cd:2091220"
    assert "Heller" in {l["name"] for l in d["lookalikes"]}, "'Heller' (search firm) must be a lookalike, not a match"


def test_discovery_drops_unusable_urls_and_non_english():
    from signalhunt.agents.discovery import usable
    assert not usable({"url": "https://www.google.com/goto?url=CAES", "title": "Plant opens"})
    assert not usable({"url": "https://www.msn.com/en-in/money/x", "title": "Plant opens"})
    assert not usable({"url": "https://aajtak.in/x", "title": "जेवर में CNH के नए मैन्युफैक्चरिंग प्लांट"})
    assert usable({"url": "https://www.thehindubusinessline.com/x", "title": "Acme opens plant in Sanand"})


def test_rate_limit_429_waits_and_does_not_open_breaker(monkeypatch):
    seq = iter([429, 429, 200])
    monkeypatch.setattr(net, "_client", httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(next(seq), headers={"retry-after": "1"}, json={}))))
    slept = []
    monkeypatch.setattr(net.time, "sleep", lambda s: slept.append(s))
    ctx = RunContext(None)
    assert net.request(ctx, "crustdata", "company_identify", "POST", "https://api.example/x", retries=0).status_code == 200
    assert slept == [1.0, 1.0]
    assert net.BREAKER.allow("crustdata:company_identify")
    monkeypatch.setattr(net, "_client", None)


def test_breaker_is_scoped_per_domain(monkeypatch):
    def handler(r):
        return httpx.Response(500) if "bad.example" in str(r.url) else httpx.Response(200, text="ok")
    monkeypatch.setattr(net, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    ctx = RunContext(None)
    for _ in range(3):
        with pytest.raises(net.SourceError):
            net.request(ctx, "web", "fetch_page", "GET", "https://bad.example/a", retries=0, breaker_key="bad.example")
    assert net.request(ctx, "web", "fetch_page", "GET", "https://good.example/a", retries=0,
                       breaker_key="good.example").status_code == 200
    monkeypatch.setattr(net, "_client", None)


def test_transient_failures_are_not_memoized():
    rid = workflow.create_run("transient cache test in Gujarat")
    ctx = RunContext(rid)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return {"error": "429", "_transient": True} if calls["n"] == 1 else {"ok": True}

    assert ctx.step("k", flaky)["_transient"]
    assert ctx.step("k", flaky) == {"ok": True}   # retried, not served from cache
    assert ctx.step("k", flaky) == {"ok": True} and calls["n"] == 2  # now cached


def test_decision_maker_seniority_ranking():
    from signalhunt.agents.people import title_rank
    assert title_rank("Chief Procurement Officer") > title_rank("logistics foreman shift incharge")
    assert title_rank("Head - Supply Chain") > title_rank("Logistics Manager")
    assert title_rank("Supply chain intern") <= 0 or title_rank("Supply chain intern") < title_rank("Logistics Manager")
    assert title_rank("Sales Manager") == 0


def test_all_sources_down_fails_loudly_and_resumes(monkeypatch, articles):
    """Network outage: the run must FAIL (resumable), never report 'no opportunities'."""
    from signalhunt.agents import discovery
    from signalhunt.sources import fetcher
    down = {"on": True}

    def search(ctx, q, spec, **kw):
        if down["on"]:
            return {"provider": None, "results": [], "failures": ["bing_news_rss: ConnectError: getaddrinfo failed"],
                    "_transient": True}
        return {"provider": "fixture", "results": [{"title": a["title"], "url": a["url"], "snippet": "", "published": a["published"],
                                                    "provider": "fixture"} for a in articles[:2]], "failures": []}

    monkeypatch.setattr(discovery, "_search_one", search)
    monkeypatch.setattr(fetcher, "fetch_article", lambda ctx, url: {
        "ok": True, "url": url, "final_url": url, "retrieved_at": "x", "via": "fixture", "date_field": "f",
        **{k: a[k] for a in articles if a["url"] == url for k in ("title", "published", "publisher", "text")}})
    rid = workflow.create_run(GUJ_REQUEST)
    workflow.execute_run(rid)
    run = workflow.get_run(rid)
    assert run["status"] == "FAILED" and "SOURCES_UNAVAILABLE" in run["error"]
    down["on"] = False  # network back: resume the same run
    with db.connect() as con:
        con.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (rid,))
    workflow.execute_run(rid)
    assert workflow.get_run(rid)["status"] == "COMPLETED"
    assert workflow.run_result(rid)["opportunities"], "resumed run must search again (failures were not cached)"


def test_model_prescreen_skips_irrelevant_articles():
    from signalhunt.agents.extractor import worth_model_call
    spec = {"states": ["Gujarat"], "triggers": ["WAREHOUSE_EXPANSION"]}
    assert not worth_model_call({"title": "Fire", "text": "A fire broke out at a warehouse in Surat, Gujarat."}, spec)[0]
    assert not worth_model_call({"title": "Pune plant", "text": "Delta Motors opened a new plant in Pune."}, spec)[0]
    assert worth_model_call({"title": "Sanand", "text": "Acme has opened a new warehouse in Sanand."}, spec)[0]


def test_model_path_falls_back_to_rules_when_api_fails(fake_sources, monkeypatch):
    """Key set but the API is down / refuses: the run still completes with rule extraction."""
    from signalhunt import llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def boom(*a, **k):
        raise llm.LLMUnavailable("Claude API error 529")
    monkeypatch.setattr(llm, "structured", boom)
    rid = workflow.create_run(GUJ_REQUEST)
    workflow.execute_run(rid)
    res = workflow.run_result(rid)
    assert res["status"] == "COMPLETED" and any(o["company_name"] == "Acme Foods" for o in res["opportunities"])
    assert res["spec"]["parse_method"] == "rules"
