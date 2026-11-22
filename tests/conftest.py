"""Test fixtures. Mock data is used ONLY here; the application itself always uses live sources."""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIX = Path(__file__).parent / "fixtures" / "articles.json"


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Each test gets its own database and outbox, no API keys (deterministic rule path)."""
    monkeypatch.setenv("SIGNALHUNT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CRUSTDATA_API_KEY", "")
    monkeypatch.setenv("MAX_RUN_COST_USD", "1.0")
    monkeypatch.setenv("MAX_RUN_SECONDS", "300")
    monkeypatch.setenv("OUTREACH_MODE", "sandbox")
    monkeypatch.setenv("TRUST_NEWS_CHECK", "0")  # no live news search inside tests
    monkeypatch.setenv("ALLOWED_RECIPIENTS", "")
    from signalhunt import approvals, net
    monkeypatch.setattr(approvals, "OUTBOX", tmp_path / "outbox")
    net.BREAKER.failures.clear()
    net.BREAKER.opened_at.clear()
    yield


def load_fixture() -> list[dict]:
    """Articles with relative dates (`age_days`) so the fixtures never go stale."""
    arts = json.loads(FIX.read_text(encoding="utf-8"))
    for a in arts:
        a["published"] = days_ago(a.pop("age_days")) if a.get("age_days") is not None else None
    return arts


@pytest.fixture
def articles():
    return load_fixture()


@pytest.fixture
def fake_sources(monkeypatch, articles):
    """Replace live search + fetch with fixture data. Returns call counters."""
    from signalhunt.agents import discovery
    from signalhunt.sources import fetcher

    calls = {"search": 0, "fetch": {}}
    selected = {"ids": None}

    def _search_one(ctx, q, spec, **kw):
        calls["search"] += 1
        res = []
        for a in articles:
            if selected["ids"] is not None and a["id"] not in selected["ids"]:
                continue
            res.append({"title": a["title"], "url": a["url"], "snippet": a["text"][:120], "published": a["published"],
                        "provider": "fixture", "source_kind": "news"})
        return {"provider": "fixture", "results": res, "failures": []}

    def fetch_article(ctx, url):
        calls["fetch"][url] = calls["fetch"].get(url, 0) + 1
        for a in articles:
            if a["url"] == url or url.split("?")[0] == a["url"]:
                if a.get("fetch_fails"):
                    return {"ok": False, "url": url, "retrieved_at": "2026-11-22T00:00:00+00:00", "errors": ["HTTP 503"]}
                return {"ok": True, "url": url, "final_url": a["url"], "retrieved_at": "2026-11-22T00:00:00+00:00",
                        "via": "fixture", "title": a["title"], "published": a["published"],
                        "date_field": "fixture meta", "publisher": a["publisher"], "text": a["text"]}
        return {"ok": False, "url": url, "retrieved_at": "x", "errors": ["not in fixture"]}

    monkeypatch.setattr(discovery, "_search_one", _search_one)
    monkeypatch.setattr(fetcher, "fetch_article", fetch_article)
    calls["select"] = lambda ids: selected.__setitem__("ids", set(ids) if ids is not None else None)
    return calls


GUJ_REQUEST = ("Find companies in Gujarat that have announced warehouse expansion or manufacturing expansion "
               "in the last 30 days and could potentially require third-party logistics services.")
