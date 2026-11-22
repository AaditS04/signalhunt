"""Crustdata API client. Endpoints and payloads follow docs.crustdata.com (API version 2025-11-01):

  POST /web/search/live     web + news search with start_date/end_date (unix seconds)
  POST /web/enrich/live     fetch page HTML (max 10 URLs)
  POST /company/identify    resolve a name/domain to candidate companies (basic_info)
  POST /company/enrich      full company record (fields: basic_info, locations, people, ...)
  POST /person/search       filter people, e.g. by current company id and title

Every call is priced from the X-Credits-Used response header and written to the ledger.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .. import net
from ..runtime import RunContext

BASE = "https://api.crustdata.com"
PROVIDER = "crustdata"


class CrustdataUnavailable(Exception):
    pass


def _headers(ctx: RunContext) -> dict:
    if not ctx.cfg.crustdata_key:
        raise CrustdataUnavailable("CRUSTDATA_API_KEY not set")
    return {
        "Authorization": f"Bearer {ctx.cfg.crustdata_key}",
        "x-api-version": "2025-11-01",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _cost_fn(ctx: RunContext):
    def fn(resp):
        try:
            credits = float(resp.headers.get("X-Credits-Used", "0") or 0)
        except ValueError:
            credits = 0.0
        return {"credits": credits}, credits * ctx.cfg.crustdata_usd_per_credit
    return fn


# Per-endpoint requests/minute, from GET /account/endpoints for this account (22 Nov 2026).
RATE_LIMIT_RPM = {"/company/identify": 5, "/company/enrich": 5, "/person/search": 5, "/job/search": 5,
                  "/web/search/live": 10, "/web/enrich/live": 10}
_last_call: dict[str, float] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _throttle(path: str) -> None:
    """Space calls to each endpoint at its published rate limit, so we rarely see a 429 at all.
    Thread-safe: calls to the same endpoint queue behind one lock; different endpoints proceed in parallel."""
    with _locks_guard:
        lock = _locks.setdefault(path, threading.Lock())
    with lock:
        interval = 60.0 / RATE_LIMIT_RPM.get(path, 60) + 0.3
        wait = interval - (time.monotonic() - _last_call.get(path, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[path] = time.monotonic()


def _post(ctx: RunContext, path: str, payload: dict, operation: str, retries: int = 2) -> Any:
    _throttle(path)
    resp = net.request(ctx, PROVIDER, operation, "POST", BASE + path, json=payload,
                       headers=_headers(ctx), cost_fn=_cost_fn(ctx), retries=retries, breaker_key=path,
                       timeout=45.0)
    return resp.json()


def web_search(ctx: RunContext, query: str, start_ts: int | None = None, end_ts: int | None = None,
               sources: list[str] | None = None, location: str = "IN", page: int | None = None) -> list[dict]:
    payload: dict[str, Any] = {"query": query, "location": location, "sources": sources or ["news"]}
    if start_ts:
        payload["start_date"] = int(start_ts)
    if end_ts:
        payload["end_date"] = int(end_ts)
    if page:
        payload["page"] = page
    data = _post(ctx, "/web/search/live", payload, "web_search")
    out = []
    for r in (data or {}).get("results", []) or []:
        if not r.get("url"):
            continue
        out.append({
            "title": r.get("title") or "",
            "url": r["url"],
            "snippet": r.get("snippet") or "",
            "published": None,  # Crustdata search results carry no date; the page itself is dated later.
            "provider": "crustdata.web_search",
            "source_kind": r.get("source"),
        })
    return out


def web_fetch(ctx: RunContext, urls: list[str]) -> list[dict]:
    data = _post(ctx, "/web/enrich/live", {"urls": urls[:10]}, "web_fetch", retries=1)
    return [d for d in (data or []) if isinstance(d, dict)]


def identify_company(ctx: RunContext, name: str | None = None, domain: str | None = None) -> list[dict]:
    if domain:
        payload = {"domains": [domain], "fields": ["basic_info"]}
    else:
        payload = {"names": [name], "fields": ["basic_info"]}
    data = _post(ctx, "/company/identify", payload, "company_identify")
    matches = []
    for entry in data or []:
        for m in entry.get("matches", []) or []:
            cd = m.get("company_data") or {}
            bi = cd.get("basic_info") or {}
            matches.append({
                "crustdata_company_id": cd.get("crustdata_company_id") or bi.get("crustdata_company_id"),
                "name": bi.get("name"),
                "primary_domain": bi.get("primary_domain"),
                "all_domains": bi.get("all_domains") or [],
                "website": bi.get("website"),
                "linkedin": bi.get("professional_network_url"),
                "industries": bi.get("industries") or [],
                "employee_count_range": bi.get("employee_count_range"),
                "confidence_score": m.get("confidence_score"),
                "match_type": entry.get("match_type"),
            })
    return matches


def enrich_company(ctx: RunContext, company_id: int) -> dict | None:
    data = _post(ctx, "/company/enrich",
                 {"crustdata_company_ids": [int(company_id)], "fields": ["basic_info", "locations"]},
                 "company_enrich")
    for entry in data or []:
        for m in entry.get("matches", []) or []:
            return m.get("company_data") or None
    return None


DECISION_TITLES = [
    "supply chain", "logistics", "warehouse", "procurement", "chief operating officer", "COO",
    "operations", "distribution", "purchase", "sourcing",
]


def search_decision_makers(ctx: RunContext, company_id: int, limit: int = 10) -> list[dict]:
    payload = {
        "filters": {
            "op": "and",
            "conditions": [
                {"field": "experience.employment_details.current.company_id", "type": "=", "value": int(company_id)},
                {"op": "or", "conditions": [
                    {"field": "experience.employment_details.current.title", "type": "(.)", "value": t}
                    for t in DECISION_TITLES
                ]},
            ],
        },
        "fields": ["basic_profile", "experience", "social_handles"],
        "limit": limit,
    }
    data = _post(ctx, "/person/search", payload, "person_search")
    return (data or {}).get("profiles", []) or []
