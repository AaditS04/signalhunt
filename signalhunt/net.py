"""Resilient HTTP: retries with backoff, per-provider circuit breaker, every attempt written to the ledger."""
from __future__ import annotations

import random
import time
from typing import Any

import httpx

from .runtime import RunContext

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 SignalHunt/1.0"
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class SourceError(Exception):
    def __init__(self, provider: str, message: str, status: int | None = None):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status = status


class CircuitBreaker:
    """After `threshold` consecutive failures a provider is skipped for `cooldown` seconds, so the
    workflow moves to the fallback source instead of burning its time limit on a dead one."""

    def __init__(self, threshold: int = 3, cooldown: float = 120.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures: dict[str, int] = {}
        self.opened_at: dict[str, float] = {}

    def allow(self, provider: str) -> bool:
        t = self.opened_at.get(provider)
        if t is None:
            return True
        if time.monotonic() - t > self.cooldown:
            self.opened_at.pop(provider, None)
            self.failures[provider] = 0
            return True
        return False

    def success(self, provider: str) -> None:
        self.failures[provider] = 0
        self.opened_at.pop(provider, None)

    def failure(self, provider: str) -> None:
        n = self.failures.get(provider, 0) + 1
        self.failures[provider] = n
        if n >= self.threshold:
            self.opened_at[provider] = time.monotonic()


BREAKER = CircuitBreaker()
_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True,
                               headers={"User-Agent": USER_AGENT})
    return _client


def request(ctx: RunContext, provider: str, operation: str, method: str, url: str, *,
            retries: int = 2, cost_fn=None, breaker_key: str | None = None, **kwargs: Any) -> httpx.Response:
    """Perform an HTTP request. Raises SourceError when all attempts fail.

    * cost_fn(response) -> (units, cost_usd) prices the call (e.g. Crustdata's X-Credits-Used header).
    * breaker_key scopes the circuit breaker (one per API endpoint, one per publisher domain), so one bad
      website or one rate-limited endpoint never blocks the others.
    * HTTP 429 is back-pressure, not an outage: we wait (Retry-After, else 12 s) and retry up to 3 times, and it
      does not count towards opening the breaker.
    """
    bkey = f"{provider}:{breaker_key or operation}"
    if not BREAKER.allow(bkey):
        ctx.record(provider, operation, "skipped", detail=f"circuit open for {bkey} after repeated failures")
        raise SourceError(provider, f"circuit open for {bkey} (failing repeatedly); using fallback")
    last_err = ""
    status = None
    attempt = 0
    rate_limited = 0
    while True:
        ctx.check_limits(f"{provider}.{operation}")
        t0 = time.monotonic()
        wait = 0.0
        try:
            resp = client().request(method, url, **kwargs)
            ms = (time.monotonic() - t0) * 1000
            status = resp.status_code
            units, cost = cost_fn(resp) if cost_fn else ({}, 0.0)
            if resp.status_code < 400:
                ctx.record(provider, operation, "ok", ms, units, cost, detail=url[:300])
                BREAKER.success(bkey)
                return resp
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code == 429 and rate_limited < 3:
                rate_limited += 1
                wait = min(_retry_after(resp) or 12.0, 30.0)
                ctx.record(provider, operation, "retry", ms, units, cost, detail=f"rate limited, waiting {wait:.0f}s: {url[:150]}")
                time.sleep(wait)
                continue
            retryable = resp.status_code in RETRYABLE and resp.status_code != 429
            final = not retryable or attempt >= retries
            ctx.record(provider, operation, "error" if final else "retry", ms, units, cost, detail=f"{url[:200]} {last_err}")
            if final:
                break
            wait = _retry_after(resp) or (1.5 * (2 ** attempt) + random.random())
        except httpx.HTTPError as e:
            ms = (time.monotonic() - t0) * 1000
            last_err = f"{type(e).__name__}: {e}"
            final = attempt >= retries
            ctx.record(provider, operation, "error" if final else "retry", ms, detail=f"{url[:200]} {last_err}")
            if final:
                break
            wait = 1.5 * (2 ** attempt) + random.random()
        attempt += 1
        time.sleep(min(wait, 8.0))
    if status != 429:
        BREAKER.failure(bkey)
    raise SourceError(provider, last_err or "request failed", status)


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None
