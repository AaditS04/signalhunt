"""Request parsing must not silently narrow the search (live finding, 22 Nov 2026)."""
from __future__ import annotations

from signalhunt.agents import discovery, request_parser

MP_UP = ("Find me top tier clients expanding in Madhya Pradesh and Uttar Pradesh in the past 45 days "
         "which would need trucks and cold storage services")


def test_expanding_plus_cold_storage_keeps_plant_expansions():
    spec = request_parser.validate(request_parser.rule_parse(MP_UP), MP_UP)
    assert spec["states"] == ["Madhya Pradesh", "Uttar Pradesh"] and spec["window_days"] == 45
    # "cold storage" must not swallow "expanding" and "trucks": new plants are the main freight demand
    assert {"MANUFACTURING_EXPANSION", "WAREHOUSE_EXPANSION"} <= set(spec["triggers"])


def test_queries_cover_plants_and_cold_storage_in_both_states():
    spec = request_parser.validate(request_parser.rule_parse(MP_UP), MP_UP)
    qs = discovery.build_queries(spec)
    for st in ("Madhya Pradesh", "Uttar Pradesh"):
        assert any(st in q and "plant" in q for q in qs), qs
        assert any(st in q and "cold storage" in q for q in qs), qs


def test_specific_request_is_not_widened():
    t = "Find companies in Gujarat that have announced warehouse expansion in the last 30 days"
    assert request_parser.rule_parse(t)["triggers"] == ["WAREHOUSE_EXPANSION"]


def test_noise_headlines_rank_below_business_news():
    spec = {"triggers": ["WAREHOUSE_EXPANSION", "MANUFACTURING_EXPANSION"], "states": ["Uttar Pradesh"], "window_days": 45}
    fire = {"title": "UP: Fire breaks out at warehouse in Ghaziabad; no casualties reported", "published": None}
    plant = {"title": "Sugs Lloyd secures Noida land for electrical goods plant", "published": None}
    assert discovery.relevance(plant, spec) > discovery.relevance(fire, spec)


def test_land_allotment_for_a_plant_is_an_expansion():
    from signalhunt.agents import rule_extractor as rx
    art = {"title": "Sugs Lloyd secures Noida land for electrical goods plant", "url": "https://x.example/a",
           "text": ("Sugs Lloyd secures Noida land for electrical goods plant. Sugs Lloyd Limited has secured the allotment of "
                    "3,342 square metres of industrial land in Noida, Uttar Pradesh, to establish a new manufacturing "
                    "facility for electrical goods.")}
    spec = {"triggers": ["MANUFACTURING_EXPANSION"], "states": ["Uttar Pradesh"], "industries": []}
    evs = rx.rule_extract(art, spec)
    assert evs and evs[0]["company_name"].startswith("Sugs Lloyd") and evs[0]["location_state"] == "Uttar Pradesh"


def test_bing_reads_month_filter_and_three_pages_for_long_windows(monkeypatch):
    from signalhunt import net
    from signalhunt.runtime import RunContext
    from signalhunt.sources import news_rss

    urls = []

    class Resp:
        def __init__(self, content):
            self.content = content

    def fake(ctx, provider, op, method, url, **kw):
        urls.append(url)
        page = 0 if "first=" not in url else int(url.split("first=")[1].split("&")[0]) // 10
        items = "".join(f"<item><title>T{page}-{i}</title><link>https://n.example/{page}/{i}</link>"
                        f"<pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate></item>" for i in range(10))
        return Resp(f"<rss><channel>{items}</channel></rss>".encode())

    monkeypatch.setattr(net, "request", fake)
    res = news_rss.bing_news(RunContext(None), "Uttar Pradesh new manufacturing plant", window_days=45)
    assert len(res) == 30 and len(urls) == 3
    assert all("interval%3d%229%22" in u for u in urls) and "first=21" in urls[2]
    urls.clear()
    news_rss.bing_news(RunContext(None), "x", window_days=7)
    assert len(urls) == 1 and "interval%3d%228%22" in urls[0]
