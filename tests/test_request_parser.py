"""Request understanding (no model): abbreviations, industries, trigger phrasings, exclusions, out-of-scope places."""
from __future__ import annotations

import pytest
from conftest import GUJ_REQUEST

from signalhunt import workflow
from signalhunt.agents.request_parser import excluded_places, foreign_places, request_exclusions, rule_parse, validate


def spec(q):
    return validate(rule_parse(q), q)


@pytest.mark.parametrize("q,states", [
    ("FMCG brands expanding distribution in UP, MP and Bihar last quarter", ["Bihar", "Madhya Pradesh", "Uttar Pradesh"]),
    ("Companies entering new markets in Assam and Meghalaya", ["Assam", "Meghalaya"]),
    ("Electronics manufacturing expansion in Noida and Gurgaon", ["Haryana", "Uttar Pradesh"]),
    ("I cannot find up-to-date plant news in Rajasthan", ["Rajasthan"]),  # lower-case "up" is not Uttar Pradesh
])
def test_states_aliases_and_cities(q, states):
    assert spec(q)["states"] == states


@pytest.mark.parametrize("q,industries", [
    ("Steel and cement capacity expansion in Odisha", ["steel and metals", "cement and building materials"]),
    ("Auto component makers hiring in Pune", ["automotive"]),
    ("Food processing plants in Punjab", ["fmcg"]),
    ("Textile mills setting up units in Surat", ["textiles"]),
    ("chemical companies, Gujarat", ["chemicals"]),
    ("Find manufacturing expansion in Gujarat", []),  # "manufacturing expansion" is a trigger, not an industry
])
def test_industries(q, industries):
    assert spec(q)["industries"] == industries


@pytest.mark.parametrize("q,trigger", [
    ("Steel capacity expansion in Odisha", "MANUFACTURING_EXPANSION"),
    ("Textile mills setting up units in Surat", "MANUFACTURING_EXPANSION"),
    ("Logistics parks and godowns in West Bengal", "WAREHOUSE_EXPANSION"),
    ("Who raised funding in Maharashtra recently?", "FUNDING_OR_INVESTMENT"),
    ("D2C brands setting up fulfilment centres in Bengaluru", "ECOMMERCE_GROWTH"),
])
def test_triggers(q, trigger):
    assert trigger in spec(q)["triggers"]


@pytest.mark.parametrize("q,days", [("last 2 weeks", 14), ("previous three months", 90), ("last quarter", 90),
                                    ("recently", 30), ("last 400 days", 365)])
def test_window(q, days):
    assert spec(f"plants in Gujarat {q}")["window_days"] == days


def test_exclusions_split_companies_from_places():
    assert request_exclusions("new plants in Telangana; exclude Ritco Logistics and TCI Express") == ["Ritco Logistics", "TCI Express"]
    assert request_exclusions("plants in Gujarat except Gujarat Fluorochemicals") == ["Gujarat Fluorochemicals"]
    q = "Companies setting up shop in UP and HP but not in Delhi"
    assert request_exclusions(q) == [] and excluded_places(q) == ["Delhi"]


def test_foreign_place_detected():
    assert foreign_places("warehouse expansion in Dubai last 30 days") == ["Dubai"]
    assert foreign_places("warehouse expansion in Gujarat") == []


def test_out_of_scope_request_stops_without_spending(fake_sources):
    rid = workflow.create_run("warehouse expansion in Dubai last 30 days")
    workflow.execute_run(rid)
    res = workflow.run_result(rid)
    assert res["status"] == "COMPLETED" and res["opportunities"] == []
    assert "OUT_OF_SCOPE" in (workflow.get_run(rid)["error"] or "")


def test_request_level_exclusion_rejects_company(fake_sources):
    rid = workflow.create_run(GUJ_REQUEST + "; exclude Acme Foods")
    workflow.execute_run(rid)
    res = workflow.run_result(rid)
    assert not any(o["company_name"].startswith("Acme Foods") for o in res["opportunities"])
    assert "exclusion list" in " ".join(c["reason"] for c in res["rejected"])


@pytest.mark.parametrize("title,noisy", [
    ("Major fire erupts at warehouse in Navi Mumbai; black clouds of smoke engulf area", True),
    ("Food safety violations: FDA suspends licences of Amazon, BigBasket dark stores in Pune", True),
    ("Sensex & Nifty 50 | Stock Market Highlights 17 Nov 2026", True),
    ("Humanoid Robot Market 2026 (New Report) by Top Prominent Players", True),
    ("President Murmu condoles demise of Rajya Sabha MP", True),
    ("IndoSpace expands its Karnataka footprint with 1.05M sq ft Narasapura-II park", False),
    ("Kia and Hyundai form alliance to set up battery plant in Anantapur", False),
    ("Tata Steel commissions new blast furnace at Kalinganagar", False),
    ("Blast at chemical unit in Dahej kills two", True),
])
def test_headline_screen(title, noisy):
    from signalhunt.agents.discovery import headline_noise
    assert bool(headline_noise(title)) is noisy
