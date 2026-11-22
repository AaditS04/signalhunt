"""HTTP API + dashboard host.  Run:  python -m uvicorn signalhunt.api:app --port 8000"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import approvals, db, workflow
from .agents.scoring import ASSUMPTIONS, WEIGHTS
from .config import ROOT, client_profile, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    with db.connect() as con:
        n = con.execute("SELECT COUNT(*) n FROM runs WHERE status='RUNNING'").fetchone()["n"]
    if n:
        db.log(None, "WARN", "recovery", f"process restarted: {n} interrupted run(s) will resume from the step cache")
    workflow.WORKER.start()
    yield
    workflow.WORKER.stop()


app = FastAPI(title="SignalHunt", version="1.0", lifespan=lifespan)


class RunIn(BaseModel):
    request: str


class DraftIn(BaseModel):
    subject: str
    body: str


class ApproveIn(BaseModel):
    approver: str
    to_email: str
    subject: str | None = None
    body: str | None = None


class RejectIn(BaseModel):
    approver: str
    reason: str = ""


@app.get("/api/health")
def health():
    cfg = settings()
    return {"ok": True, "llm": cfg.llm_enabled, "model": cfg.model if cfg.llm_enabled else None,
            "crustdata": cfg.crustdata_enabled, "outreach_mode": cfg.outreach_mode,
            "limits": {"max_cost_usd": cfg.max_run_cost_usd, "max_seconds": cfg.max_run_seconds,
                       "max_articles": cfg.max_articles, "max_llm_calls": cfg.max_llm_calls},
            "client": client_profile().get("client_name")}


@app.post("/api/runs")
def create_run(body: RunIn):
    if len(body.request.strip()) < 10:
        raise HTTPException(400, "request is too short")
    rid = workflow.create_run(body.request)
    workflow.WORKER.wake()
    return {"run_id": rid}


@app.get("/api/runs")
def list_runs(limit: int = 30):
    with db.connect() as con:
        rows = con.execute("SELECT id, request_text, status, current_step, progress_json, attempts, created_at, finished_at"
                           " FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["progress"] = db.loads(d.pop("progress_json"), {})
        out.append(d)
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    r = workflow.run_result(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return r


@app.post("/api/runs/{run_id}/retry")
def retry_run(run_id: str):
    r = workflow.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if r["status"] not in ("FAILED", "STOPPED_LIMIT"):
        raise HTTPException(409, f"run is {r['status']}")
    with db.connect() as con:
        con.execute("UPDATE runs SET status='RUNNING', error=NULL, finished_at=NULL WHERE id=?", (run_id,))
    workflow.WORKER.wake()
    return {"ok": True}


@app.get("/api/opportunities")
def opportunities():
    with db.connect() as con:
        rows = con.execute("SELECT data_json, first_run_id, last_run_id, updated_at FROM opportunities ORDER BY score DESC").fetchall()
    out = []
    for r in rows:
        d = db.loads(r["data_json"], {})
        d["first_run_id"], d["last_run_id"], d["updated_at"] = r["first_run_id"], r["last_run_id"], r["updated_at"]
        out.append(d)
    return out


@app.get("/api/opportunities/{opp_id}")
def opportunity(opp_id: str):
    with db.connect() as con:
        r = con.execute("SELECT data_json FROM opportunities WHERE id=?", (opp_id,)).fetchone()
        acts = con.execute("SELECT id FROM actions WHERE opp_id=?", (opp_id,)).fetchall()
    if not r:
        raise HTTPException(404, "not found")
    d = db.loads(r["data_json"], {})
    d["actions"] = [a for a in approvals.list_actions() if a["opp_id"] == opp_id]
    return d


@app.get("/api/actions")
def actions(status: str | None = None):
    return approvals.list_actions(status)


@app.put("/api/actions/{action_id}")
def edit_action(action_id: str, body: DraftIn):
    try:
        return approvals.update_draft(action_id, body.subject, body.body)
    except approvals.ApprovalError as e:
        raise HTTPException(409, str(e))


@app.post("/api/actions/{action_id}/approve")
def approve_action(action_id: str, body: ApproveIn):
    try:
        return approvals.approve(action_id, body.approver, body.to_email, body.subject, body.body)
    except approvals.ApprovalError as e:
        raise HTTPException(409, str(e))


@app.post("/api/actions/{action_id}/reject")
def reject_action(action_id: str, body: RejectIn):
    try:
        return approvals.reject(action_id, body.approver, body.reason)
    except approvals.ApprovalError as e:
        raise HTTPException(409, str(e))


@app.get("/api/stats")
def stats():
    cfg = settings()
    with db.connect() as con:
        q = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731
        s = {
            "runs": q("SELECT COUNT(*) FROM runs"),
            "runs_active": q("SELECT COUNT(*) FROM runs WHERE status IN ('RUNNING','QUEUED')"),
            "companies": q("SELECT COUNT(DISTINCT company_key) FROM opportunities"),
            "opportunities": q("SELECT COUNT(*) FROM opportunities"),
            "high_priority": q("SELECT COUNT(*) FROM opportunities WHERE priority='HIGH'"),
            "verified_triggers": q("SELECT COUNT(*) FROM run_candidates WHERE status IN ('VERIFIED','DUPLICATE')"),
            "cannot_verify": q("SELECT COUNT(*) FROM run_candidates WHERE status='CANNOT_VERIFY'"),
            "rejected": q("SELECT COUNT(*) FROM run_candidates WHERE status='REJECTED'"),
            "pending_approval": q("SELECT COUNT(*) FROM actions WHERE status='PENDING_APPROVAL'"),
            "sent": q("SELECT COUNT(*) FROM actions WHERE status='SENT'"),
            "total_cost_usd": round(q("SELECT COALESCE(SUM(cost_usd),0) FROM ledger"), 4),
        }
    s["total_cost_inr"] = round(s["total_cost_usd"] * cfg.usd_inr, 2)
    return s


@app.get("/api/ledger")
def ledger(run_id: str | None = None, limit: int = 300):
    with db.connect() as con:
        if run_id:
            rows = con.execute("SELECT * FROM ledger WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        summary = con.execute(
            "SELECT provider, operation, COUNT(*) calls, SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok,"
            " SUM(CASE WHEN status='retry' THEN 1 ELSE 0 END) retries, SUM(CASE WHEN status IN ('error','skipped') THEN 1 ELSE 0 END) errors,"
            " ROUND(AVG(latency_ms),0) avg_ms, COALESCE(SUM(cost_usd),0) cost_usd FROM ledger"
            + (" WHERE run_id=?" if run_id else "") + " GROUP BY provider, operation ORDER BY cost_usd DESC",
            (run_id,) if run_id else ()).fetchall()
    return {"entries": [dict(r) for r in rows], "summary": [dict(r) for r in summary]}


@app.get("/api/logs")
def logs(run_id: str | None = None, limit: int = 300):
    with db.connect() as con:
        if run_id:
            rows = con.execute("SELECT * FROM logs WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/config")
def config():
    return {"client": client_profile(), "scoring_weights": WEIGHTS, "scoring_assumptions": ASSUMPTIONS}


app.mount("/static", StaticFiles(directory=str(ROOT / "web")), name="static")


@app.get("/")
def index():
    return FileResponse(str(ROOT / "web" / "index.html"))
