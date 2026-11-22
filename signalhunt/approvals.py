"""Human approval + exactly-once delivery for irreversible actions (outbound email).

State machine (enforced with conditional UPDATEs, so two clicks / two processes cannot race):

    PENDING_APPROVAL --approve--> APPROVED --claim--> SENDING --deliver--> SENT
           |                                              |
           +--reject--> REJECTED                          +--(crash)--> reconcile() on restart

* One action per opportunity: `idempotency_key = sha("email", opp_id)` is UNIQUE, so re-running a
  workflow never creates a second email for the same opportunity.
* Nothing is sent without a named approver; the recipient address is typed by the human approver
  (SignalHunt never guesses or scrapes email addresses).
* Delivery is idempotent on the key: the sandbox outbox creates `data/outbox/<key>.json` with
  O_EXCL, so a replay after a crash finds the file and does not send twice.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

from . import db
from .config import ROOT, client_profile, settings
from .runtime import sha

OUTBOX = ROOT / "data" / "outbox"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


class ApprovalError(Exception):
    pass


def idem_key(opp_id: str) -> str:
    return sha("email", opp_id, n=24)


def create_draft(opp_id: str, recipient: dict | None, subject: str, body: str, run_id: str | None) -> dict:
    key = idem_key(opp_id)
    payload = {"recipient": recipient, "subject": subject, "body": body, "drafted_in_run": run_id}
    with db.connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO actions(id, idempotency_key, opp_id, kind, payload_json, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("act_" + key[:12], key, opp_id, "email", db.dumps(payload), "PENDING_APPROVAL", db.now(), db.now()),
        )
        row = con.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
    return dict(row)


def get(action_id: str) -> dict | None:
    with db.connect() as con:
        return db.row_to_dict(con.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())


def update_draft(action_id: str, subject: str, body: str) -> dict:
    a = get(action_id)
    if not a:
        raise ApprovalError("action not found")
    if a["status"] != "PENDING_APPROVAL":
        raise ApprovalError(f"cannot edit an action in status {a['status']}")
    p = db.loads(a["payload_json"], {})
    p.update({"subject": subject, "body": body, "edited": True})
    with db.connect() as con:
        con.execute("UPDATE actions SET payload_json=?, updated_at=? WHERE id=? AND status='PENDING_APPROVAL'",
                    (db.dumps(p), db.now(), action_id))
    return get(action_id)


def approve(action_id: str, approver: str, to_email: str, subject: str | None = None, body: str | None = None) -> dict:
    if not approver or not approver.strip():
        raise ApprovalError("approver name is required")
    if not EMAIL_RE.match((to_email or "").strip()):
        raise ApprovalError("a valid recipient email typed by the approver is required (SignalHunt never guesses addresses)")
    a = get(action_id)
    if not a:
        raise ApprovalError("action not found")
    p = db.loads(a["payload_json"], {})
    if subject is not None:
        p["subject"] = subject
    if body is not None:
        p["body"] = body
    p["to_email"] = to_email.strip()
    with db.connect() as con:
        cur = con.execute(
            "UPDATE actions SET status='APPROVED', approved_by=?, approved_at=?, payload_json=?, updated_at=?"
            " WHERE id=? AND status='PENDING_APPROVAL'",
            (approver.strip(), db.now(), db.dumps(p), db.now(), action_id))
        if cur.rowcount != 1:
            raise ApprovalError(f"action is {a['status']}, not PENDING_APPROVAL")
    db.log(None, "INFO", "approval", f"{action_id} approved by {approver.strip()} for {to_email.strip()}")
    return send(action_id)


def reject(action_id: str, approver: str, reason: str = "") -> dict:
    with db.connect() as con:
        cur = con.execute(
            "UPDATE actions SET status='REJECTED', approved_by=?, error=?, updated_at=? WHERE id=? AND status='PENDING_APPROVAL'",
            (approver or "unknown", reason[:500], db.now(), action_id))
        if cur.rowcount != 1:
            raise ApprovalError("only a PENDING_APPROVAL action can be rejected")
    db.log(None, "INFO", "approval", f"{action_id} rejected by {approver}: {reason}")
    return get(action_id)


def send(action_id: str) -> dict:
    """Claim APPROVED -> SENDING atomically, deliver idempotently, then mark SENT."""
    with db.connect() as con:
        cur = con.execute("UPDATE actions SET status='SENDING', updated_at=? WHERE id=? AND status='APPROVED'",
                          (db.now(), action_id))
        claimed = cur.rowcount == 1
    a = get(action_id)
    if not claimed and a["status"] != "SENDING":
        return a  # already SENT / REJECTED / not approved: nothing to do (exactly-once)
    return _deliver(a)


def _deliver(a: dict) -> dict:
    p = db.loads(a["payload_json"], {})
    key = a["idempotency_key"]
    try:
        if settings().outreach_mode == "smtp":
            ref = _smtp_send(p, key)
        else:
            ref = _sandbox_send(a, p, key)
        with db.connect() as con:
            con.execute("UPDATE actions SET status='SENT', sent_at=?, provider_ref=?, updated_at=? WHERE id=? AND status='SENDING'",
                        (db.now(), ref, db.now(), a["id"]))
        db.log(None, "INFO", "outbox", f"{a['id']} delivered ({ref})")
    except Exception as e:  # noqa: BLE001 - delivery errors are recorded, never swallowed
        with db.connect() as con:
            con.execute("UPDATE actions SET status='FAILED', error=?, updated_at=? WHERE id=?", (str(e)[:500], db.now(), a["id"]))
        db.log(None, "ERROR", "outbox", f"{a['id']} delivery failed: {e}")
    return get(a["id"])


def _sandbox_send(a: dict, p: dict, key: str) -> str:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    path = OUTBOX / f"{key}.json"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return f"sandbox:{path.name} (already delivered; replay ignored)"
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"idempotency_key": key, "action_id": a["id"], "opp_id": a["opp_id"], "to": p.get("to_email"),
                   "from": client_profile().get("sender", {}).get("email"), "subject": p.get("subject"),
                   "body": p.get("body"), "approved_by": a.get("approved_by"), "delivered_at": db.now()}, fh, indent=2)
    return f"sandbox:{path.name}"


def _smtp_send(p: dict, key: str) -> str:
    marker = OUTBOX / f"{key}.smtp-sent"
    if marker.exists():
        return "smtp (already delivered; replay ignored)"
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM") or client_profile().get("sender", {}).get("email")
    msg["To"] = p["to_email"]
    msg["Subject"] = p["subject"]
    msg["Message-ID"] = f"<{key}@signalhunt>"  # receivers de-duplicate on Message-ID
    msg.set_content(p["body"])
    with smtplib.SMTP(os.getenv("SMTP_HOST", ""), int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    marker.write_text(key)
    return f"smtp:{key}"


def reconcile() -> list[str]:
    """On startup: finish any action interrupted between claim and SENT. Delivery is idempotent, so this
    either finds the delivered file (mark SENT) or delivers once."""
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM actions WHERE status='SENDING'").fetchall()]
    done = []
    for a in rows:
        db.log(None, "WARN", "recovery", f"action {a['id']} was interrupted while SENDING; reconciling")
        _deliver(a)
        done.append(a["id"])
    return done


def list_actions(status: str | None = None) -> list[dict]:
    with db.connect() as con:
        if status:
            rows = con.execute("SELECT * FROM actions WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM actions ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = db.loads(d.pop("payload_json"), {})
        out.append(d)
    return out
