"""Company & signal discovery: TargetSpec -> ranked list of candidate articles.

Source chain per query: Crustdata web search (news, date-filtered) -> Bing News RSS -> Google News RSS.
A failing source is logged and skipped; the next one is used. Results are de-duplicated by
canonical URL and by near-identical headline (syndicated copies of one story).
"""
from __future__ import annotations

import os
import re
import urllib.parse
from datetime import date, datetime, timezone

from .. import geo, net
from ..runtime import RunContext, sha
from ..sources import crustdata, news_rss

TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "fbclid", "gclid", "amp", "from"}


class SourcesUnavailable(Exception):
    """Every discovery source failed (network down / providers down). The run fails visibly and can be resumed."""


def canonical_url(url: str) -> str:
    p = urllib.parse.urlparse(url.strip())
    host = p.netloc.lower().replace("www.", "").replace("m.", "", 1) if p.netloc.lower().startswith(("www.", "m.")) else p.netloc.lower()
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if k.lower() not in TRACKING]
    path = re.sub(r"/amp/?$", "", p.path.rstrip("/"))
    return urllib.parse.urlunparse(("https", host, path, "", urllib.parse.urlencode(q), ""))


def title_key(title: str) -> str:
    t = re.sub(r"\s+[-|–]\s+[^-|–]{2,40}$", "", title or "")  # drop " - Publisher" suffix
    words = re.findall(r"[a-z0-9]+", t.lower())
    return " ".join(w for w in words if len(w) > 2)[:120]


def build_queries(spec: dict, max_queries: int = 10) -> list[str]:
    geos = spec["cities"][:3] + spec["states"] if spec.get("cities") else list(spec.get("states") or [])
    if not geos:
        geos = ["India"]
    inds = spec.get("industries") or []
    queries: list[str] = []
    # breadth first: template #0 for every (trigger, geo) pair, then template #1, ... so a small
    # query budget still covers every requested combination
    depth = max(len(v) for v in geo.QUERY_TEMPLATES.values())
    for idx in range(depth):
        for t in spec["triggers"]:
            tmpls = geo.QUERY_TEMPLATES.get(t, ["{geo} expansion"])
            if idx >= len(tmpls):
                continue
            for g in geos:
                q = tmpls[idx].format(geo=g)
                if q not in queries and len(queries) < max_queries:
                    queries.append(q)
    for ind in inds[:2]:
        for g in geos[:2]:
            q = f"{g} {ind} company expansion new facility"
            if len(queries) < max_queries + 2 and q not in queries:
                queries.append(q)
    return queries


# Pages that are not usable as evidence records: JS-only renderers, redirect interstitials, social posts.
UNUSABLE_URL = re.compile(r"(google\.com/goto|news\.google\.com/rss/articles|msn\.com/|instagram\.com|facebook\.com|"
                          r"youtube\.com|youtu\.be|twitter\.com|x\.com/|linkedin\.com/posts|threads\.net)", re.I)


def is_english(title: str) -> bool:
    """The extractor reads English; non-Latin headlines (e.g. Hindi) are skipped, not guessed at."""
    letters = [ch for ch in title if ch.isalpha()]
    return bool(letters) and sum(ch.isascii() for ch in letters) / len(letters) > 0.8


def usable(item: dict) -> bool:
    return bool(item.get("url")) and not UNUSABLE_URL.search(item["url"]) and is_english(item.get("title", ""))


def _search_one(ctx: RunContext, q: str, spec: dict, use_crustdata: bool = True) -> dict:
    """Bing News RSS (direct article URLs + dates) is primary; Crustdata web search adds coverage (only its
    direct-URL results are kept: many of its news links are google.com/goto interstitials that are not records);
    Google News RSS is the fallback when both return nothing."""
    failures, results, providers = [], [], []
    try:
        res = [r for r in news_rss.bing_news(ctx, q) if usable(r)]
        results += res
        providers.append(f"bing_news_rss:{len(res)}")
    except Exception as e:  # noqa: BLE001 - malformed XML or HTTP failure must not kill the run
        failures.append(f"bing_news_rss: {type(e).__name__}: {e}")
    if ctx.cfg.crustdata_enabled and use_crustdata:
        start = datetime.fromisoformat(spec["window_start"]).replace(tzinfo=timezone.utc)
        try:
            raw = crustdata.web_search(ctx, q, start_ts=int(start.timestamp()),
                                       end_ts=int(datetime.now(timezone.utc).timestamp()), sources=["news"])
            res = [r for r in raw if usable(r)]
            results += res
            providers.append(f"crustdata:{len(res)}/{len(raw)}")
        except (net.SourceError, crustdata.CrustdataUnavailable, ValueError) as e:
            failures.append(f"crustdata: {e}")
    if not results:
        try:
            res = [r for r in news_rss.google_news(ctx, q, spec["window_days"]) if usable(r)]
            results += res
            providers.append(f"google_news_rss:{len(res)}")
        except Exception as e:  # noqa: BLE001
            failures.append(f"google_news_rss: {type(e).__name__}: {e}")
    out = {"provider": ", ".join(providers) or None, "results": results, "failures": failures}
    if not results and failures:
        out["_transient"] = True  # every source failed: don't memoize, a resumed run searches again
    return out


def relevance(c: dict, spec: dict) -> float:
    text = f"{c['title']} {c.get('snippet', '')}".lower()
    s = 0.0
    for t in spec["triggers"]:
        if any(k in text for k in geo.TRIGGER_KEYWORDS.get(t, [])):
            s += 1.0
    if spec["states"] and geo.states_in(text) & set(spec["states"]):
        s += 1.5
    if re.search(r"crore|₹|rs\.?\s?\d|investment|sq\.? ?ft|acre", text):
        s += 0.5
    if c.get("published"):
        try:
            age = (date.today() - date.fromisoformat(c["published"])).days
            s += max(0.0, 1.0 - age / max(spec["window_days"], 1))
        except ValueError:
            pass
    return s


def discover(ctx: RunContext, spec: dict) -> dict:
    queries = build_queries(spec)
    raw: list[dict] = []
    query_log = []
    n_cd = int(os.getenv("CRUSTDATA_SEARCH_QUERIES", "0"))  # off by default: measured 2 usable URLs / 5 credits / ~100 s
    for qi, q in enumerate(queries):
        ctx.check_limits("discovery search")
        r = ctx.step(f"search:{sha(q, spec['window_start'])}",
                     lambda q=q, qi=qi: _search_one(ctx, q, spec, use_crustdata=qi < n_cd))
        query_log.append({"query": q, "provider": r["provider"], "n": len(r["results"]), "failures": r["failures"]})
        for f in r["failures"]:
            ctx.log("WARN", "discover", f"query '{q}': {f}")
        for item in r["results"]:
            item = dict(item)
            item["query"] = q
            raw.append(item)

    failed = [x for x in query_log if not x["n"] and x["failures"]]
    if query_log and len(failed) == len(query_log):
        # "no source reachable" must never be reported as "no opportunities exist": fail loudly, stay resumable
        raise SourcesUnavailable(f"all {len(query_log)} discovery queries failed on every source "
                                 f"(e.g. {failed[0]['failures'][0][:160]}); run can be resumed when sources recover")
    if failed:
        ctx.log("WARN", "discover", f"degraded: {len(failed)}/{len(query_log)} queries failed on every source")

    by_url: dict[str, dict] = {}
    by_title: dict[str, str] = {}
    dup_count = 0
    outdated = []
    for c in raw:
        cu = canonical_url(c["url"])
        tk = title_key(c["title"])
        if cu in by_url or (tk and tk in by_title):
            dup_count += 1
            keep = by_url.get(cu) or by_url.get(by_title.get(tk, ""), None)
            if keep is not None:
                keep.setdefault("also_seen_at", []).append(c["url"])
            continue
        if c.get("published") and c["published"] < spec["window_start"]:
            outdated.append({**c, "reason": f"published {c['published']} is before window start {spec['window_start']}"})
            continue
        c["canonical_url"] = cu
        c["cand_id"] = sha(cu)
        by_url[cu] = c
        if tk:
            by_title[tk] = cu
    cands = list(by_url.values())
    for c in cands:
        c["relevance"] = round(relevance(c, spec), 3)
    cands.sort(key=lambda c: c["relevance"], reverse=True)
    return {
        "queries": query_log,
        "raw_count": len(raw),
        "duplicate_listings_removed": dup_count,
        "outdated": outdated,
        "candidates": cands[: ctx.cfg.max_articles],
        "dropped_for_budget": max(0, len(cands) - ctx.cfg.max_articles),
    }
