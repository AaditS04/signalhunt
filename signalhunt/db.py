"""SQLite persistence. Every piece of workflow state lives here so a killed process can resume.

WAL mode + short transactions: safe for one API process plus one worker thread.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    request_text TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    spec_json TEXT,
    status TEXT NOT NULL,              -- QUEUED RUNNING COMPLETED FAILED STOPPED_LIMIT
    current_step TEXT,
    progress_json TEXT DEFAULT '{}',
    error TEXT,
    attempts INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS step_cache (
    run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    output_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, step_key)
);
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,               -- deterministic fingerprint of (company identity, event type, place)
    company_key TEXT NOT NULL,
    company_name TEXT NOT NULL,
    score REAL,
    priority TEXT,
    verification TEXT,
    data_json TEXT NOT NULL,
    first_run_id TEXT,
    last_run_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS run_opportunities (
    run_id TEXT NOT NULL,
    opp_id TEXT NOT NULL,
    PRIMARY KEY (run_id, opp_id)
);
CREATE TABLE IF NOT EXISTS run_candidates (
    run_id TEXT NOT NULL,
    cand_id TEXT NOT NULL,
    status TEXT NOT NULL,              -- VERIFIED CANNOT_VERIFY REJECTED DUPLICATE
    reason TEXT,
    data_json TEXT NOT NULL,
    PRIMARY KEY (run_id, cand_id)
);
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    opp_id TEXT NOT NULL,
    kind TEXT NOT NULL,                -- email
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,              -- PENDING_APPROVAL APPROVED SENDING SENT REJECTED FAILED
    approved_by TEXT,
    approved_at REAL,
    sent_at REAL,
    provider_ref TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ts REAL NOT NULL,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,              -- ok error retry cached skipped
    latency_ms REAL,
    units_json TEXT,
    cost_usd REAL DEFAULT 0,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    step TEXT,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replies (
    id TEXT PRIMARY KEY,               -- sha(action_id, from, text): the same reply recorded twice is one row
    action_id TEXT NOT NULL,
    opp_id TEXT NOT NULL,
    from_email TEXT NOT NULL,
    text TEXT NOT NULL,
    verdict TEXT NOT NULL,             -- BOOKED HELD_IMPOSTOR_RISK HELD_DOMAIN_MISMATCH HELD_NO_TIME HELD_CAPACITY HELD_CANNOT_VERIFY
    detail_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_offers (
    asset_id TEXT NOT NULL,
    opp_id TEXT NOT NULL,
    action_id TEXT,
    status TEXT NOT NULL,              -- OFFERED (meeting booked on this capacity)
    meeting_at TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (asset_id, opp_id)
);
CREATE INDEX IF NOT EXISTS idx_ledger_run ON ledger(run_id);
CREATE INDEX IF NOT EXISTS idx_logs_run ON logs(run_id);
CREATE INDEX IF NOT EXISTS idx_actions_opp ON actions(opp_id);
"""

_init_lock = threading.Lock()
_initialized: set[str] = set()


def _path() -> str:
    return settings().db_path


def init_db(path: str | None = None) -> None:
    p = path or _path()
    with _init_lock:
        if p in _initialized:
            return
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            con.commit()
        finally:
            con.close()
        _initialized.add(p)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    p = _path()
    init_db(p)
    con = sqlite3.connect(p, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
    finally:
        con.close()


def now() -> float:
    return time.time()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, sort_keys=True)


def loads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


def log(run_id: str | None, level: str, step: str | None, message: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO logs(run_id, ts, level, step, message) VALUES (?,?,?,?,?)",
            (run_id, now(), level, step, message[:4000]),
        )
    print(f"[{level}] {run_id or '-'} {step or '-'}: {message[:300]}", flush=True)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
