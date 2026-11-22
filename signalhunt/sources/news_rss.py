"""Free live news discovery (no key needed): Bing News RSS (primary fallback) and Google News RSS.

Used when Crustdata is not configured or fails. Each result keeps the feed's pubDate so the
publication date of a candidate is known before the page is fetched.
"""
from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .. import net
from ..runtime import RunContext


def _parse_date(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        return None


def _text(el, tag: str) -> str:
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def bing_interval(window_days: int) -> str:
    """Bing News 'qft interval': 7 = past 24 hours, 8 = past 7 days, 9 = past 30 days (measured 22 Nov 2026:
    interval 8 returned nothing older than 6 days). Bing has no wider filter; every date is re-checked anyway."""
    return "7" if window_days <= 1 else "8" if window_days <= 7 else "9"


def bing_pages(window_days: int) -> int:
    """Bing returns about 10 items per page, newest first. One page covered only the last week of a 45-day
    request, so wider windows read more pages (page 3 held a plant opening 4 weeks old)."""
    return 1 if window_days <= 7 else 3


def bing_news(ctx: RunContext, query: str, window_days: int = 30) -> list[dict]:
    out, seen = [], set()
    for page in range(bing_pages(window_days)):
        url = ("https://www.bing.com/news/search?format=rss&mkt=en-IN&qft=interval%3d%22" + bing_interval(window_days)
               + "%22" + (f"&first={page * 10 + 1}" if page else "") + "&q=" + urllib.parse.quote_plus(query))
        resp = net.request(ctx, "bing_news_rss", "news_search", "GET", url, retries=1)
        root = ET.fromstring(resp.content)
        new = 0
        for item in root.iter("item"):
            link = _text(item, "link")
            parsed = urllib.parse.urlparse(link)
            real = urllib.parse.parse_qs(parsed.query).get("url", [link])[0]
            if real in seen:
                continue
            seen.add(real)
            new += 1
            out.append({
                "title": _text(item, "title"),
                "url": real,
                "snippet": _text(item, "description"),
                "published": _parse_date(_text(item, "pubDate")),
                "provider": "bing_news_rss",
                "source_kind": "news",
            })
        if not new:
            break  # no further results
    return out


def google_news(ctx: RunContext, query: str, days: int = 30) -> list[dict]:
    q = urllib.parse.quote_plus(f"{query} when:{days}d")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    resp = net.request(ctx, "google_news_rss", "news_search", "GET", url, retries=1)
    root = ET.fromstring(resp.content)
    out = []
    for item in root.iter("item"):
        src = item.find("source")
        out.append({
            "title": _text(item, "title"),
            "url": _text(item, "link"),
            "snippet": "",
            "published": _parse_date(_text(item, "pubDate")),
            "provider": "google_news_rss",
            "source_kind": "news",
            "publisher": (src.text or "").strip() if src is not None and src.text else None,
        })
    return out
