"""Fetch an article and turn it into verifiable evidence material.

Returns the clean paragraph text (quotes are later checked against it verbatim), the publication
date *and the HTML field it came from* (so the date itself has a source), the publisher and the
retrieval timestamp.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from .. import net
from ..runtime import RunContext
from . import crustdata

DATE_META = [
    ("property", "article:published_time"),
    ("property", "og:published_time"),
    ("name", "article:published_time"),
    ("itemprop", "datePublished"),
    ("name", "publish-date"),
    ("name", "pubdate"),
    ("name", "publishdate"),
    ("name", "date"),
    ("name", "DC.date.issued"),
    ("name", "parsely-pub-date"),
    ("property", "article:modified_time"),
]


def _iso_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            return None
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s[:30].strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _jsonld_dates(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if "datePublished" in x:
                    d = _iso_date(str(x["datePublished"]))
                    if d:
                        return d
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
    return None


def parse_html(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    published, date_field = None, None
    for attr, val in DATE_META:
        tag = soup.find("meta", attrs={attr: val})
        if tag and tag.get("content"):
            d = _iso_date(tag["content"])
            if d:
                published, date_field = d, f'<meta {attr}="{val}" content="{tag["content"][:40]}">'
                break
    if not published:
        d = _jsonld_dates(soup)
        if d:
            published, date_field = d, "JSON-LD datePublished"
    if not published:
        t = soup.find("time", attrs={"datetime": True})
        if t:
            d = _iso_date(t["datetime"])
            if d:
                published, date_field = d, f'<time datetime="{t["datetime"][:40]}">'

    site = soup.find("meta", attrs={"property": "og:site_name"})
    publisher = site["content"].strip() if site and site.get("content") else urllib.parse.urlparse(url).netloc.replace("www.", "")

    for bad in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
        bad.decompose()
    paras = []
    seen = set()
    for p in soup.find_all(["p", "h1", "h2", "li"]):
        txt = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
        if len(txt) >= 40 and txt not in seen:
            seen.add(txt)
            paras.append(txt)
    text = fix_mojibake("\n".join(paras))
    return {"title": title, "published": published, "date_field": date_field, "publisher": publisher,
            "text": text[:60000]}


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that a site double-encoded as cp1252 ('groupâ€™s' -> 'group’s'), line by line.
    Applied before verification, so quotes and the text they are checked against stay consistent."""
    if "â€" not in text and "Ã" not in text:
        return text
    out = []
    for line in text.split("\n"):
        if "â€" in line or "Ã" in line:
            try:
                line = line.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        out.append(line)
    return "\n".join(out)


def decode_html(resp) -> str:
    """Most Indian news sites serve UTF-8 without declaring it in the header; a latin-1 guess turns ’ into â€™.
    Try UTF-8 first, then the declared/guessed encoding."""
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.text


def fetch_article(ctx: RunContext, url: str) -> dict:
    """Direct fetch first (free); Crustdata web fetch as fallback for blocked pages."""
    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors = []
    try:
        resp = net.request(ctx, "web", "fetch_page", "GET", url, retries=1,
                           breaker_key=urllib.parse.urlparse(url).netloc.lower())
        ctype = resp.headers.get("content-type", "")
        if "html" in ctype or not ctype:
            parsed = parse_html(decode_html(resp), str(resp.url))
            if len(parsed["text"]) > 300:
                return {"ok": True, "url": url, "final_url": str(resp.url), "retrieved_at": retrieved,
                        "via": "direct", **parsed}
            errors.append("direct fetch returned too little text")
        else:
            errors.append(f"unsupported content-type {ctype}")
    except net.SourceError as e:
        errors.append(str(e))
    if ctx.cfg.crustdata_enabled:
        try:
            res = crustdata.web_fetch(ctx, [url])
            if res and res[0].get("success") and res[0].get("content"):
                parsed = parse_html(res[0]["content"], url)
                if len(parsed["text"]) > 300:
                    return {"ok": True, "url": url, "final_url": res[0].get("url") or url, "retrieved_at": retrieved,
                            "via": "crustdata.web_fetch", **parsed}
            errors.append("crustdata web_fetch returned no usable content")
        except (net.SourceError, crustdata.CrustdataUnavailable) as e:
            errors.append(str(e))
    # network-level failures are transient: not cached, so a resumed run tries the page again
    transient = any(("Error" in e or "circuit open" in e or "HTTP 5" in e or "HTTP 429" in e) for e in errors)
    return {"ok": False, "url": url, "retrieved_at": retrieved, "errors": errors, "_transient": transient}
