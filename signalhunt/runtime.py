"""Run context: durable step memoization, cost ledger and limit enforcement.

Durability model
----------------
Every unit of work that touches the outside world (a search, a page fetch, an LLM call, an API
lookup) runs through `RunContext.step(key, fn)`. The first time, `fn()` runs and its JSON result is
written to `step_cache` under (run_id, key). If the process is killed and the run is resumed, the
same key returns the stored result without repeating the call. So a restart repeats no completed
work and spends no money twice.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from . import db
from .config import MODEL_PRICES, Settings, settings


class LimitExceeded(Exception):
    """Raised when a run reaches its cost, time or call limit. The run stops cleanly with partial results."""


def sha(*parts: Any, n: int = 16) -> str:
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:n]


class RunContext:
    def __init__(self, run_id: str | None, cfg: Settings | None = None, prior_active_seconds: float = 0.0):
        self.run_id = run_id
        self.cfg = cfg or settings()
        self.session_start = time.monotonic()
        self.prior_active = prior_active_seconds
        self.cache_hits = 0
        self.cache_misses = 0

    # ---------------- limits ----------------
    def elapsed(self) -> float:
        return self.prior_active + (time.monotonic() - self.session_start)

    def cost_so_far(self) -> float:
        if not self.run_id:
            return 0.0
        with db.connect() as con:
            r = con.execute("SELECT COALESCE(SUM(cost_usd),0) c FROM ledger WHERE run_id=?", (self.run_id,)).fetchone()
            return float(r["c"])

    def llm_calls_so_far(self) -> int:
        if not self.run_id:
            return 0
        with db.connect() as con:
            r = con.execute(
                "SELECT COUNT(*) n FROM ledger WHERE run_id=? AND provider='anthropic' AND status='ok'", (self.run_id,)
            ).fetchone()
            return int(r["n"])

    def check_limits(self, about_to: str = "") -> None:
        if self.elapsed() > self.cfg.max_run_seconds:
            raise LimitExceeded(f"time limit {self.cfg.max_run_seconds}s reached before {about_to}")
        c = self.cost_so_far()
        if c >= self.cfg.max_run_cost_usd:
            raise LimitExceeded(f"cost limit ${self.cfg.max_run_cost_usd:.2f} reached (spent ${c:.4f}) before {about_to}")

    def check_llm_budget(self) -> None:
        self.check_limits("LLM call")
        if self.llm_calls_so_far() >= self.cfg.max_llm_calls:
            raise LimitExceeded(f"LLM call limit {self.cfg.max_llm_calls} reached")

    # ---------------- memoization ----------------
    def step(self, key: str, fn: Callable[[], Any]) -> Any:
        if self.run_id:
            with db.connect() as con:
                row = con.execute(
                    "SELECT output_json FROM step_cache WHERE run_id=? AND step_key=?", (self.run_id, key)
                ).fetchone()
            if row is not None:
                self.cache_hits += 1
                return db.loads(row["output_json"])
        self.cache_misses += 1
        out = fn()
        # transient failures (rate limit, network) are NOT memoized: a resumed run retries them
        if self.run_id and not (isinstance(out, dict) and out.get("_transient")):
            with db.connect() as con:
                con.execute(
                    "INSERT OR REPLACE INTO step_cache(run_id, step_key, output_json, created_at) VALUES (?,?,?,?)",
                    (self.run_id, key, db.dumps(out), db.now()),
                )
        return out

    def shared_step(self, key: str, fn: Callable[[], Any], ttl_seconds: int = 86400) -> Any:
        """Cross-run cache for slow-changing reference data (company identity, current decision-makers).
        Saves credits and rate-limit budget when several runs touch the same company. Records keep their
        original retrieval timestamp, so the age of every fact stays visible."""
        with db.connect() as con:
            row = con.execute("SELECT output_json, created_at FROM step_cache WHERE run_id='__shared__' AND step_key=?",
                              (key,)).fetchone()
        if row is not None and db.now() - row["created_at"] < ttl_seconds:
            self.cache_hits += 1
            return db.loads(row["output_json"])
        out = self.step(key, fn)
        if not (isinstance(out, dict) and out.get("_transient")):
            with db.connect() as con:
                con.execute("INSERT OR REPLACE INTO step_cache(run_id, step_key, output_json, created_at) VALUES (?,?,?,?)",
                            ("__shared__", key, db.dumps(out), db.now()))
        return out

    # ---------------- ledger ----------------
    def record(self, provider: str, operation: str, status: str, latency_ms: float = 0.0,
               units: dict | None = None, cost_usd: float = 0.0, detail: str = "") -> None:
        with db.connect() as con:
            con.execute(
                "INSERT INTO ledger(run_id, ts, provider, operation, status, latency_ms, units_json, cost_usd, detail)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (self.run_id, db.now(), provider, operation, status, round(latency_ms, 1),
                 db.dumps(units or {}), cost_usd, detail[:1000]),
            )

    def llm_cost(self, model: str, input_tokens: int, output_tokens: int,
                 cache_read: int = 0, cache_write: int = 0) -> float:
        pin, pout = MODEL_PRICES.get(model, (4.0, 20.0))
        return (input_tokens * pin + output_tokens * pout + cache_read * pin * 0.1 + cache_write * pin * 1.25) / 1e6

    def log(self, level: str, step: str, message: str) -> None:
        db.log(self.run_id, level, step, message)
