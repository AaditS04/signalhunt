"""Layer 4, last steps: verify a buyer's reply, then book the meeting exactly once.

1. The reply must answer an offer that was actually SENT.
2. Impostor check: the sender's domain must be one of the resolved company's domains
   (Crustdata company/identify primary_domain / all_domains). A free-mail address claiming to be the
   buyer is HELD; so is any other domain. During the event a teammate plays the buyer: addresses in
   ROLEPLAY_SENDERS are accepted and labelled ROLE_PLAY on screen, never presented as the real company.
3. A day and time are read from the text by plain code; no time found means a person reads it.
4. Capacity: a lane or space already OFFERED to another buyer is held for a person (never promised twice).
5. Booking: the .ics invite is created with O_EXCL under a key derived from the action, and the reply row
   is keyed on (action, sender, text), so a crash and replay books once and records once.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

from . import approvals, db
from .config import client_profile
from .runtime import sha

FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "live.com", "rediffmail.com",
             "icloud.com", "aol.com", "proton.me", "protonmail.com", "zoho.com", "gmx.com", "mail.com", "ymail.com"}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_RE = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|nesday|sday|urday|rsday)?\b|\btomorrow\b", re.I)
TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)|\b([01]?\d|2[0-3]):([0-5]\d)\b", re.I)


class ReplyError(Exception):
    pass


def _crash_point(name: str) -> None:
    if os.getenv("SIGNALHUNT_CRASH_AT") == name:
        db.log(None, "WARN", "crash-test", f"simulated hard kill at {name}")
        os._exit(137)


def parse_meeting_time(text: str, today: date | None = None) -> str | None:
    """'Thursday 3 PM works' -> next Thursday 15:00 (ISO, IST). None if day or time is missing."""
    today = today or date.today()
    d = DAY_RE.search(text or "")
    t = TIME_RE.search(text or "")
    if not d or not t:
        return None
    if d.group(0).lower() == "tomorrow":
        day = today + timedelta(days=1)
    else:
        idx = next(i for i, n in enumerate(DAYS) if n.startswith(d.group(1).lower()[:3]))
        ahead = (idx - today.weekday()) % 7 or 7
        day = today + timedelta(days=ahead)
    if t.group(1):
        h, mnt, ap = int(t.group(1)), int(t.group(2) or 0), t.group(3).lower().replace(".", "")
        if h > 12:
            return None
        h = (h % 12) + (12 if ap == "pm" else 0)
    else:
        h, mnt = int(t.group(4)), int(t.group(5))
    return datetime(day.year, day.month, day.day, h, mnt).isoformat(timespec="minutes")


def _opp(opp_id: str) -> dict:
    with db.connect() as con:
        r = con.execute("SELECT data_json FROM opportunities WHERE id=?", (opp_id,)).fetchone()
    return db.loads(r["data_json"], {}) if r else {}


def check_sender(from_email: str, opp: dict) -> dict:
    email = from_email.strip().lower()
    dom = email.split("@")[-1]
    roleplay = [x.strip().lower() for x in os.getenv("ROLEPLAY_SENDERS", "").split(",") if x.strip()]
    comp = (opp.get("identity") or {}).get("company") or {}
    domains = sorted({d.lower() for d in [comp.get("primary_domain"), *(comp.get("all_domains") or [])] if d})
    base = {"sender": email, "sender_domain": dom, "company_domains": domains,
            "source": "Crustdata POST /company/identify basic_info.primary_domain / all_domains"}
    if email in roleplay:
        return {**base, "status": "ROLE_PLAY", "ok": True,
                "reason": "teammate playing the buyer (ROLEPLAY_SENDERS); a real reply must come from " +
                          (", ".join(domains) or "the company's own domain")}
    if dom in FREE_MAIL:
        return {**base, "status": "HELD_IMPOSTOR_RISK", "ok": False,
                "reason": f"free-mail address ({dom}) claiming to speak for {opp.get('company_name')}"}
    if not domains:
        return {**base, "status": "HELD_CANNOT_VERIFY", "ok": False,
                "reason": "the company's domains are unknown (identity not resolved), so the sender cannot be checked"}
    if any(dom == d or dom.endswith("." + d) for d in domains):
        return {**base, "status": "DOMAIN_MATCH", "ok": True, "reason": f"{dom} is one of the company's domains"}
    return {**base, "status": "HELD_DOMAIN_MISMATCH", "ok": False,
            "reason": f"{dom} is not one of the company's domains ({', '.join(domains)})"}


def _write_ics(key: str, when_iso: str, opp: dict, attendee: str, asset_id: str | None) -> str:
    approvals.OUTBOX.mkdir(parents=True, exist_ok=True)
    path = approvals.OUTBOX / f"meeting_{key}.ics"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return path.name  # already booked: a replay changes nothing
    start = datetime.fromisoformat(when_iso)
    end = start + timedelta(minutes=30)
    fmt = "%Y%m%dT%H%M%S"
    sender = client_profile().get("sender", {})
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SignalHunt//EN", "METHOD:REQUEST", "BEGIN:VEVENT",
        f"UID:{key}@signalhunt", f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}Z",
        f"DTSTART;TZID=Asia/Kolkata:{start.strftime(fmt)}", f"DTEND;TZID=Asia/Kolkata:{end.strftime(fmt)}",
        f"SUMMARY:{sender.get('company', 'SignalHunt')} x {opp.get('company_name')} ({asset_id or 'logistics'})",
        f"ORGANIZER:mailto:{sender.get('email', '')}", f"ATTENDEE:mailto:{attendee}",
        "END:VEVENT", "END:VCALENDAR", ""])
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(ics)
    return path.name


def record_reply(action_id: str, from_email: str, text: str, today: date | None = None) -> dict:
    a = approvals.get(action_id)
    if not a:
        raise ReplyError("action not found")
    if a["status"] != "SENT":
        raise ReplyError(f"action is {a['status']}: a reply can only answer an offer that was sent")
    rid = "rep_" + sha("reply", action_id, from_email.strip().lower(), text.strip(), n=16)
    with db.connect() as con:
        prev = con.execute("SELECT * FROM replies WHERE id=?", (rid,)).fetchone()
    if prev:
        d = dict(prev)
        d["detail"] = db.loads(d.pop("detail_json"), {})
        d["replayed"] = True
        return d
    opp = _opp(a["opp_id"])
    payload = db.loads(a["payload_json"], {})
    asset_id = payload.get("asset_id")
    sender = check_sender(from_email, opp)
    detail = {"sender_check": sender, "asset_id": asset_id, "meeting_at": None, "ics": None}
    if not sender["ok"]:
        verdict = sender["status"]
    else:
        when = parse_meeting_time(text, today)
        detail["meeting_at"] = when
        with db.connect() as con:
            other = con.execute("SELECT opp_id FROM asset_offers WHERE asset_id=? AND opp_id<>?",
                                (asset_id, a["opp_id"])).fetchone() if asset_id else None
        if not when:
            verdict = "HELD_NO_TIME"
            detail["reason"] = "no day and time found in the reply; a person reads it"
        elif other:
            verdict = "HELD_CAPACITY"
            detail["reason"] = f"{asset_id} is already offered to another buyer ({other['opp_id']}); a person decides"
        else:
            key = sha("meeting", action_id, n=24)
            detail["ics"] = _write_ics(key, when, opp, from_email.strip(), asset_id)
            _crash_point("after_ics")
            if asset_id:
                with db.connect() as con:
                    con.execute("INSERT OR IGNORE INTO asset_offers(asset_id, opp_id, action_id, status, meeting_at,"
                                " created_at) VALUES (?,?,?,?,?,?)",
                                (asset_id, a["opp_id"], action_id, "OFFERED", when, db.now()))
            verdict = "BOOKED"
    with db.connect() as con:
        con.execute("INSERT OR IGNORE INTO replies(id, action_id, opp_id, from_email, text, verdict, detail_json, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (rid, action_id, a["opp_id"], from_email.strip(), text.strip()[:2000], verdict, db.dumps(detail), db.now()))
    db.log(None, "INFO" if verdict == "BOOKED" else "WARN", "reply", f"{action_id} reply from {from_email}: {verdict}")
    return {"id": rid, "action_id": action_id, "opp_id": a["opp_id"], "from_email": from_email.strip(),
            "verdict": verdict, "detail": detail}


def list_replies(opp_id: str | None = None) -> list[dict]:
    with db.connect() as con:
        rows = (con.execute("SELECT * FROM replies WHERE opp_id=? ORDER BY created_at DESC", (opp_id,)) if opp_id
                else con.execute("SELECT * FROM replies ORDER BY created_at DESC")).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = db.loads(d.pop("detail_json"), {})
        out.append(d)
    return out
