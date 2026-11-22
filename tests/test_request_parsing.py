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
