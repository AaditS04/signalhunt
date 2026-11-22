"""Workflow orchestrator: request -> evidence-backed, scored opportunities + approval-gated outreach.

Crash safety: run status and every external result are in SQLite (see runtime.RunContext.step). On
start-up the worker picks up RUNNING runs and replays them; finished steps come from the cache, so
no search, fetch, model call or email happens twice.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import traceback
import uuid
from datetime import datetime, timezone

from . import approvals, db, geo
from .agents import (dedup, discovery, extractor, ground, identity, ladder, lanes, people, reporter, request_parser,
                     scoring, terms, trust, verifier)
from .config import client_profile, settings
from .runtime import LimitExceeded, RunContext, sha
from .sources import fetcher

STATUS_DONE = {"COMPLETED", "FAILED", "STOPPED_LIMIT"}


def _crash_point(name: str) -> None:
    """Test hook: SIGNALHUNT_CRASH_AT=<point> hard-kills the process there (simulates kill -9)."""
    if os.getenv("SIGNALHUNT_CRASH_AT") == name:
        db.log(None, "WARN", "crash-test", f"simulated hard kill at {name}")
        os._exit(137)


# ---------------------------------------------------------------- run records
def create_run(request_text: str) -> str:
    run_id = "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    with db.connect() as con:
        con.execute(
            "INSERT INTO runs(id, request_text, request_hash, status, progress_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (run_id, request_text.strip(), sha(request_text.strip().lower()), "QUEUED", "{}", db.now(), db.now()))
    db.log(run_id, "INFO", "run", f"queued: {request_text[:200]}")
    return run_id


def get_run(run_id: str) -> dict | None:
    with db.connect() as con:
        r = db.row_to_dict(con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
    if r:
        r["progress"] = db.loads(r.pop("progress_json"), {})
        r["spec"] = db.loads(r.pop("spec_json"), None)
    return r


def _update(run_id: str, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.connect() as con:
        con.execute(f"UPDATE runs SET {sets}, updated_at=? WHERE id=?", (*fields.values(), db.now(), run_id))


def _progress(ctx: RunContext, prog: dict, **kw) -> None:
    prog.update(kw)
    prog["active_seconds"] = round(ctx.elapsed(), 1)
    prog["cost_usd"] = round(ctx.cost_so_far(), 5)
    prog["cache_hits"] = ctx.cache_hits
    _update(ctx.run_id, progress_json=db.dumps(prog), current_step=prog.get("phase"))


# ---------------------------------------------------------------- the pipeline
def execute_run(run_id: str) -> dict:
    run = get_run(run_id)
    if run is None:
        raise ValueError(f"unknown run {run_id}")
    if run["status"] in STATUS_DONE:
        return run
    resumed = run["status"] == "RUNNING"
    prog = run.get("progress") or {}
    ctx = RunContext(run_id, settings(), prior_active_seconds=float(prog.get("active_seconds", 0)))
    _update(run_id, status="RUNNING", attempts=(run["attempts"] or 0) + 1,
            started_at=run["started_at"] or db.now())
    if resumed:
        ctx.log("WARN", "recovery", f"resuming interrupted run (attempt {(run['attempts'] or 0) + 1}); "
                                    "completed steps will be replayed from the step cache")
        prog["recovered"] = prog.get("recovered", 0) + 1
    client = client_profile()
    limited_reason = None
    try:
        # 1. understand the request
        _progress(ctx, prog, phase="understanding request")
        spec = ctx.step("spec", lambda: request_parser.parse_request(ctx, run["request_text"]))
        _update(run_id, spec_json=db.dumps(spec))
        ctx.log("INFO", "parse", f"spec: states={spec['states']} triggers={spec['triggers']} window={spec['window_days']}d "
                                 f"industries={spec['industries']} via {spec['parse_method']}")
        _crash_point("after_parse")
        if spec.get("out_of_scope"):
            _progress(ctx, prog, phase="out of scope", opportunities=0)
            _update(run_id, status="COMPLETED", finished_at=db.now(), error=f"OUT_OF_SCOPE: {spec['out_of_scope']}")
            ctx.log("WARN", "parse", spec["out_of_scope"])
            return get_run(run_id)
        client = {**client, "excluded_companies": list(client.get("excluded_companies", []))
                  + spec.get("excluded_companies", [])}

        # 2. discover
        _progress(ctx, prog, phase="discovering signals")
        disc = ctx.step("discovery", lambda: discovery.discover(ctx, spec))
        cands = disc["candidates"]
        for o in disc["outdated"] + disc.get("screened", []):
            _save_candidate(run_id, o.get("cand_id") or sha(o["url"]), "REJECTED", o["reason"], {"listing": o})
        _progress(ctx, prog, phase="verifying evidence", articles_total=len(cands), articles_done=0,
                  queries=len(disc["queries"]), duplicate_listings_removed=disc["duplicate_listings_removed"],
                  outdated_listings=len(disc["outdated"]), screened_headlines=len(disc.get("screened", [])))
        _crash_point("after_discovery")

        # 3a. fetch every source page in parallel (reads only; each fetch memoized, so a restart refetches nothing)
        _progress(ctx, prog, phase="fetching sources")
        fetched = _prefetch(ctx, cands)

        # 3b. extract, verify, resolve identity, one article at a time. Decision-maker lookups run on a second
        #     thread as soon as a company resolves (separate Crustdata rate limit), so the two queues overlap.
        _progress(ctx, prog, phase="verifying evidence")
        verified_items: list[dict] = []
        counts = {"VERIFIED": 0, "CANNOT_VERIFY": 0, "REJECTED": 0}
        people_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="people")
        people_futures: dict = {}
        for i, c in enumerate(cands):
            try:
                ctx.check_limits("next article")
            except LimitExceeded as e:
                limited_reason = str(e)
                ctx.log("WARN", "limits", f"{e}; finishing with the {i} articles processed")
                break
            try:
                items = _process_article(ctx, c, spec, client, fetched.get(c["cand_id"]))
            except LimitExceeded as e:
                limited_reason = str(e)
                ctx.log("WARN", "limits", f"{e}; finishing with the {i} articles processed")
                break
            if not items:  # whole article rejected or unreadable: count it from the outcome already saved
                st = _candidate_status(ctx.run_id, c["cand_id"])
                if st in counts:
                    counts[st] += 1
            for it in items:
                counts[it["status"]] += 1
                if it["status"] == "VERIFIED":
                    verified_items.append(it)
                    ident = it.get("identity") or {}
                    if ident.get("status") == "RESOLVED":
                        cid = ident["company"]["crustdata_company_id"]
                        if cid not in people_futures:
                            people_futures[cid] = people_pool.submit(people.find_decision_makers, ctx, ident)
            _progress(ctx, prog, articles_done=i + 1, verified=counts["VERIFIED"],
                      cannot_verify=counts["CANNOT_VERIFY"], rejected=counts["REJECTED"])
            if i == 1:
                _crash_point("mid_articles")

        # 4. cluster duplicates into opportunities
        _progress(ctx, prog, phase="deduplicating & qualifying")
        clusters = dedup.cluster(verified_items)
        for cl in clusters:
            for dup in cl["items"][1:]:
                _save_candidate(run_id, dup["cand_key"], "DUPLICATE",
                                f"same event as opportunity {cl['opp_id']} (corroborating source)", _slim(dup))

        # 5. decision-makers, scoring, report, outreach draft
        opp_ids = []
        for cl in clusters:
            ident = cl["items"][0]["identity"]
            if limited_reason:
                ppl = {"status": "CANNOT_VERIFY", "people": [], "missing": f"skipped: {limited_reason}"}
            else:
                try:
                    cid = (ident.get("company") or {}).get("crustdata_company_id")
                    fut = people_futures.get(cid)
                    ppl = fut.result() if fut is not None else people.find_decision_makers(ctx, ident)
                except LimitExceeded as e:
                    limited_reason = str(e)
                    ppl = {"status": "CANNOT_VERIFY", "people": [], "missing": f"skipped: {e}"}
            opp = _assemble(cl, ident, ppl, spec, client)
            if not limited_reason:
                try:
                    _qualify(ctx, opp, client)
                except LimitExceeded as e:
                    limited_reason = str(e)
            if limited_reason and "outreach" not in opp:
                _qualify(ctx, opp, client, offline=True)
            out = opp["outreach"]
            if out["status"] in ("REFUSED", "WATCHING"):
                opp["report"] = reporter.template(opp, client, None)
                opp["report"]["email_body"] = ""
                _save_opportunity(run_id, opp)
                opp_ids.append(opp["id"])
                ctx.log("INFO", "qualify", f"{opp['company_name']}: {out['status']} ({out['reason'][:160]})")
                _progress(ctx, prog, opportunities=len(opp_ids), phase="writing reports")
                continue
            if out["status"] == "OFFER":
                opp["report"] = reporter.offer(opp, client)
            elif limited_reason:
                opp["report"] = reporter.template(opp, client, ppl["people"][0] if ppl.get("people") else None)
            else:
                try:
                    opp["report"] = reporter.write(ctx, opp, client)
                except LimitExceeded as e:
                    limited_reason = str(e)
                    opp["report"] = reporter.template(opp, client, ppl["people"][0] if ppl.get("people") else None)
            _save_opportunity(run_id, opp)
            person = ppl["people"][0] if ppl.get("people") else None
            act = approvals.create_draft(opp["id"], person and {"name": person["name"], "title": person["title"]},
                                         opp["report"]["email_subject"], opp["report"]["email_body"], run_id,
                                         {"kind": out["status"], "hold_reason": out.get("hold_reason"),
                                          "asset_id": out.get("asset_id")})
            opp_ids.append(opp["id"])
            _progress(ctx, prog, opportunities=len(opp_ids), phase="writing reports")
            _crash_point("after_first_opportunity")

        people_pool.shutdown(wait=True)
        final_status = "STOPPED_LIMIT" if limited_reason else "COMPLETED"
        _progress(ctx, prog, phase="done" if not limited_reason else "stopped at limit",
                  limit_reason=limited_reason, opportunities=len(opp_ids))
        _update(run_id, status=final_status, finished_at=db.now())
        ctx.log("INFO", "run", f"{final_status}: {len(opp_ids)} opportunities, {counts['VERIFIED']} verified events, "
                               f"cost ${ctx.cost_so_far():.4f}, {ctx.elapsed():.0f}s active, cache hits {ctx.cache_hits}")
    except LimitExceeded as e:
        _progress(ctx, prog, phase="stopped at limit", limit_reason=str(e))
        _update(run_id, status="STOPPED_LIMIT", finished_at=db.now(), error=str(e))
        ctx.log("WARN", "limits", str(e))
    except discovery.SourcesUnavailable as e:
        _progress(ctx, prog, phase="sources unavailable")
        _update(run_id, status="FAILED", finished_at=db.now(), error=f"SOURCES_UNAVAILABLE: {e}")
        ctx.log("ERROR", "discover", str(e))
    except Exception as e:  # noqa: BLE001
        _update(run_id, status="FAILED", finished_at=db.now(), error=f"{type(e).__name__}: {e}")
        ctx.log("ERROR", "run", traceback.format_exc()[-1500:])
    return get_run(run_id)


FETCH_WORKERS = 6


def _prefetch(ctx: RunContext, cands: list[dict]) -> dict:
    """Fetch all candidate pages concurrently. Page reads are side-effect free, and every result goes through
    the step cache, so parallelism changes nothing about crash safety or exactly-once behaviour."""
    def one(c):
        try:
            return c["cand_id"], ctx.step(f"fetch:{c['cand_id']}", lambda: fetcher.fetch_article(ctx, c["url"]))
        except LimitExceeded:
            return c["cand_id"], None
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="fetch") as ex:
        return {cid: art for cid, art in ex.map(one, cands) if art is not None}


def _process_article(ctx: RunContext, c: dict, spec: dict, client: dict, art: dict | None = None) -> list[dict]:
    if art is None:
        art = ctx.step(f"fetch:{c['cand_id']}", lambda: fetcher.fetch_article(ctx, c["url"]))
    if not art.get("ok"):
        reason = "source unavailable: " + "; ".join(art.get("errors", []))[:300]
        _save_candidate(ctx.run_id, c["cand_id"], "CANNOT_VERIFY", reason, {"listing": c})
        ctx.log("WARN", "fetch", f"{c['url'][:90]}: {reason}")
        return []
    # feed date is a dated record too (RSS pubDate); page metadata wins when present
    if not art.get("published") and c.get("published"):
        art["published"] = c["published"]
        art["date_field"] = f"{c['provider']} pubDate"
    if not art.get("publisher") and c.get("publisher"):
        art["publisher"] = c["publisher"]
    ex = extractor.extract(ctx, art, spec)
    if not ex["events"]:
        reason = (f"not sent to the model ({ex['prescreen_reason']})" if ex.get("prescreen_reason")
                  else "no qualifying business event stated in the article")
        _save_candidate(ctx.run_id, c["cand_id"], "REJECTED", reason,
                        {"listing": c, "article": {k: art.get(k) for k in ("title", "publisher", "published")}})
        return []
    out = []
    for j, ev in enumerate(ex["events"]):
        v = verifier.verify_event(ev, art, spec, client.get("excluded_companies", []))
        v["extraction_method"] = ex["method"]
        v["cand_key"] = f"{c['cand_id']}:{j}"
        if v["status"] == "VERIFIED":
            v["identity"] = identity.resolve(ctx, ev["company_name"], art.get("text", "")[:20000],
                                             art.get("final_url") or art["url"])
        reason = "; ".join(v["reasons"] or v["missing"]) or "all required facts verified"
        _save_candidate(ctx.run_id, v["cand_key"], v["status"], reason, _slim(v))
        out.append(v)
    return out


def _slim(v: dict) -> dict:
    return {k: v.get(k) for k in ("status", "reasons", "missing", "facts", "state", "event", "article",
                                  "extraction_method", "identity")}


def _assemble(cl: dict, ident: dict, ppl: dict, spec: dict, client: dict) -> dict:
    lead = cl["items"][0]
    ev = lead["event"]
    company_name = (ident.get("company") or {}).get("name") or ev["company_name"]
    evidence = []
    seen = set()
    for it in cl["items"]:
        for f in it["facts"]:
            if not f["verified"]:
                continue
            k = (f["field"], f["source_url"], f["quote"])
            if k in seen:
                continue
            seen.add(k)
            evidence.append(f)
    conflict_fields = {c["field"] for c in cl["conflicts"]}
    if conflict_fields:
        evidence = [dict(f, conflicting=f["field"] in conflict_fields) for f in evidence]
    sc = scoring.score(cl, ident, ppl, spec, client)
    timeline = sorted(
        [{"date": it["article"].get("published"), "source": it["article"].get("publisher"),
          "title": it["article"].get("title"), "url": it["article"].get("final_url") or it["article"].get("url"),
          "summary": it["event"].get("event_summary")} for it in cl["items"]],
        key=lambda x: x["date"] or "")
    inv = next((f["value"] for f in evidence if f["field"] == "investment" and not f.get("conflicting")), None)
    scale = next((f["value"] for f in evidence if f["field"] == "scale"), None)
    industry = next((it["event"].get("industry") for it in cl["items"] if it["event"].get("industry")), None)
    unverified = []
    if not industry:
        unverified.append("industry: CANNOT_VERIFY (not stated in sources)")
    for c in cl["conflicts"]:
        unverified.append(f"{c['field']}: {c['resolution']}")
    if ppl.get("status") != "VERIFIED":
        unverified.append(f"decision-maker: CANNOT_VERIFY ({ppl.get('missing')})")
    if ident.get("status") != "RESOLVED":
        unverified.append(f"company identity: {ident.get('status')} ({ident.get('note')})")
    return {
        "id": cl["opp_id"],
        "opp_id": cl["opp_id"],
        "company_name": company_name,
        "company_name_in_source": ev["company_name"],
        "entity_key": cl["entity_key"],
        "identity": ident,
        "industry": industry or "CANNOT_VERIFY",
        "state": cl["state"],
        "cities": cl["cities"],
        "family": cl["family"],
        "event_type": cl["event_type"],
        "event_label": reporter.label(cl["event_type"]),
        "event_status": ev.get("status"),
        "headline_quote": ev.get("event_quote", ""),
        "what_happened": ev.get("event_summary"),
        "when": {"latest_record_date": timeline[-1]["date"] if timeline else None,
                 "first_record_date": timeline[0]["date"] if timeline else None,
                 "event_date": ev.get("event_date") or None},
        "investment": inv,
        "scale": scale,
        "evidence": evidence,
        "independent_sources": cl["independent_sources"],
        "duplicate_reports_merged": len(cl["items"]) - 1,
        "conflicts": cl["conflicts"],
        "timeline": timeline,
        "decision_makers": ppl,
        "scoring": sc,
        "score": sc["score"],
        "priority": sc["priority"],
        "confidence": sc["confidence"],
        "verification": "VERIFIED",
        "unverified": unverified,
        "extraction_method": lead.get("extraction_method"),
    }


def _qualify(ctx: RunContext, opp: dict, client: dict, offline: bool = False) -> None:
    """Layers 1 to 3 on one opportunity: lane match, reality ladder, worth-winning verdict, payment terms.
    Then the outreach decision: REFUSED and WATCHING get no draft; a capacity match gets a priced OFFER;
    no capacity match gets a general INTRO. A thin-evidence buyer is held for a person, never refused."""
    ident = opp["identity"]
    if offline:
        sig = {"available": False, "profile": None, "jobs": None, "missing": ["run stopped at its limit"]}
        dis = {"checked": False, "hits": [], "note": "run stopped at its limit"}
    else:
        sig = ctx.step(f"signals:{opp['id']}", lambda: ground.fetch(ctx, ident))
        dis = trust.distress_news(ctx, opp["company_name"])
    opp["ground"] = {"missing": sig.get("missing"), "retrieved_on": sig.get("retrieved_on"),
                     "headcount_total": (sig.get("profile") or {}).get("headcount_total"),
                     "postings_60d": len((sig.get("jobs") or {}).get("postings", []))}
    opp["lane_match"] = lanes.match(opp, client, sig)
    opp["ladder"] = ladder.place(opp, sig, client)
    opp["trust"] = trust.verdict(opp, client, sig, dis)
    opp["terms"] = terms.decide(opp, client, opp["trust"], sig)
    v = opp["trust"]["verdict"]
    best = opp["lane_match"]["best"]
    if v == "DONT_PURSUE":
        out = {"status": "REFUSED", "reason": "; ".join(opp["trust"]["reasons"])}
    elif opp["ladder"]["watching"]:
        out = {"status": "WATCHING", "reason": f"reality ladder at {opp['ladder']['rung']}, below "
                                               f"{opp['ladder']['outreach_min_rung']}: " + "; ".join(opp["ladder"]["reasons"])}
    elif best:
        out = {"status": "OFFER", "reason": f"matched {best['asset_id']} ({best['strength']})", "asset_id": best["asset_id"]}
    else:
        out = {"status": "INTRO", "reason": "no capacity match: general introduction only"}
    if out["status"] in ("OFFER", "INTRO") and v == "CANNOT_VERIFY":
        out["hold_reason"] = "buyer not fully verified: " + "; ".join(opp["trust"]["missing"])[:300]
    opp["outreach"] = out


def _save_candidate(run_id: str, cand_id: str, status: str, reason: str, data: dict) -> None:
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO run_candidates(run_id, cand_id, status, reason, data_json) VALUES (?,?,?,?,?)",
                    (run_id, cand_id, status, reason[:1000], db.dumps(data)))


def _candidate_status(run_id: str, cand_id: str) -> str | None:
    with db.connect() as con:
        row = con.execute("SELECT status FROM run_candidates WHERE run_id=? AND cand_id=?", (run_id, cand_id)).fetchone()
    return row[0] if row else None


def _save_opportunity(run_id: str, opp: dict) -> None:
    """Upsert by deterministic id: re-running the same request never duplicates an opportunity."""
    with db.connect() as con:
        con.execute(
            "INSERT INTO opportunities(id, company_key, company_name, score, priority, verification, data_json, first_run_id,"
            " last_run_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET score=excluded.score, priority=excluded.priority, data_json=excluded.data_json,"
            " company_name=excluded.company_name, last_run_id=excluded.last_run_id, updated_at=excluded.updated_at",
            (opp["id"], opp["entity_key"], opp["company_name"], opp["score"], opp["priority"], opp["verification"],
             db.dumps(opp), run_id, run_id, db.now(), db.now()))
        con.execute("INSERT OR IGNORE INTO run_opportunities(run_id, opp_id) VALUES (?,?)", (run_id, opp["id"]))


# ---------------------------------------------------------------- results
def run_result(run_id: str) -> dict:
    run = get_run(run_id)
    if not run:
        return {}
    with db.connect() as con:
        opps = [db.loads(r["data_json"]) for r in con.execute(
            "SELECT o.data_json FROM opportunities o JOIN run_opportunities ro ON ro.opp_id=o.id WHERE ro.run_id=?"
            " ORDER BY o.score DESC", (run_id,)).fetchall()]
        cands = [dict(r) for r in con.execute(
            "SELECT cand_id, status, reason, data_json FROM run_candidates WHERE run_id=?", (run_id,)).fetchall()]
        led = con.execute(
            "SELECT provider, COUNT(*) calls, SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok,"
            " SUM(CASE WHEN status IN ('error','retry','skipped') THEN 1 ELSE 0 END) failed,"
            " COALESCE(SUM(cost_usd),0) cost FROM ledger WHERE run_id=? GROUP BY provider", (run_id,)).fetchall()
    cfg = settings()
    total = sum(r["cost"] for r in led)
    def brief(c):
        d = db.loads(c["data_json"], {})
        ev = d.get("event") or {}
        art = d.get("article") or (d.get("listing") or {})
        return {"status": c["status"], "reason": c["reason"], "company": ev.get("company_name"),
                "event": ev.get("event_summary"), "source_url": art.get("final_url") or art.get("url"),
                "source_title": art.get("title"), "published": art.get("published")}
    return {
        "run_id": run_id,
        "status": run["status"],
        "request": run["request_text"],
        "spec": run["spec"],
        "opportunities": opps,
        "cannot_verify": [brief(c) for c in cands if c["status"] == "CANNOT_VERIFY"],
        "rejected": [brief(c) for c in cands if c["status"] == "REJECTED"],
        "refused": [{"company": o["company_name"], "reason": o["outreach"]["reason"], "opp_id": o["id"]}
                    for o in opps if (o.get("outreach") or {}).get("status") == "REFUSED"],
        "watching": [{"company": o["company_name"], "rung": o["ladder"]["rung"], "reason": o["outreach"]["reason"],
                      "opp_id": o["id"]} for o in opps if (o.get("outreach") or {}).get("status") == "WATCHING"],
        "capacity_board": capacity_board(),
        "duplicates": [brief(c) for c in cands if c["status"] == "DUPLICATE"],
        "cost": {"total_usd": round(total, 5), "total_inr": round(total * cfg.usd_inr, 2),
                 "by_provider": [dict(r) for r in led],
                 "limits": {"max_cost_usd": cfg.max_run_cost_usd, "max_seconds": cfg.max_run_seconds,
                            "max_llm_calls": cfg.max_llm_calls, "max_articles": cfg.max_articles}},
        "progress": run["progress"],
        "error": run.get("error"),
    }


def capacity_board() -> list[dict]:
    """Each empty lane and free space, with what has been offered on it (a booked meeting marks it OFFERED)."""
    cap = client_profile().get("capacity") or {}
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM asset_offers").fetchall()]
    board = []
    for a in cap.get("lanes", []) + cap.get("spaces", []):
        offered = [r for r in rows if r["asset_id"] == a["id"]]
        board.append({"asset_id": a["id"], "asset": a, "status": "OFFERED" if offered else "OPEN",
                      "offers": offered})
    return board


# ---------------------------------------------------------------- background worker
class Worker:
    """Single background worker. Processes QUEUED runs and resumes RUNNING ones left by a crash."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        recovered = approvals.reconcile()
        if recovered:
            db.log(None, "WARN", "recovery", f"reconciled interrupted sends: {recovered}")
        self._thread = threading.Thread(target=self._loop, daemon=True, name="signalhunt-worker")
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _next(self) -> str | None:
        with db.connect() as con:
            r = con.execute("SELECT id FROM runs WHERE status IN ('RUNNING','QUEUED')"
                            " ORDER BY CASE status WHEN 'RUNNING' THEN 0 ELSE 1 END, created_at LIMIT 1").fetchone()
        return r["id"] if r else None

    def _loop(self) -> None:
        while not self._stop.is_set():
            rid = self._next()
            if rid:
                try:
                    execute_run(rid)
                except Exception as e:  # noqa: BLE001
                    db.log(rid, "ERROR", "worker", f"{type(e).__name__}: {e}")
                    _update(rid, status="FAILED", error=str(e))
                continue
            self._wake.wait(timeout=2.0)
            self._wake.clear()


WORKER = Worker()
