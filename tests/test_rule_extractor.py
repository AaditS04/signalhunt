"""Deterministic extractor regression tests. Sentences are modelled on real Nov-2026 news shapes."""
from __future__ import annotations

from signalhunt.agents.rule_extractor import rule_extract
from signalhunt.agents.verifier import verify_event

SPEC = {"triggers": ["WAREHOUSE_EXPANSION", "MANUFACTURING_EXPANSION"], "states": ["Gujarat"],
        "window_start": "2026-10-23", "window_end": "2026-11-22"}


def ex(title, text):
    return rule_extract({"title": title, "text": text}, SPEC)


def test_passive_voice_company():
    evs = ex("Gujarat CM lays foundation for new transformer plant",
             "Panchmahal: A new transformer manufacturing plant is to be set up at Nurpura in Halol taluka, with Chief Minister "
             "Bhupendra Patel laying its foundation stone.\nThe plant, being established by TMC Transformers India Limited, "
             "will have advanced production facilities.")
    assert [e["company_name"] for e in evs] == ["TMC Transformers India"]
    assert evs[0]["location_city"] == "Halol"


def test_official_is_not_the_company():
    evs = ex("CM lays foundation stone", "Chief Minister Bhupendra Patel on Tuesday laid the foundation stone for a new plant in Halol.")
    assert all("Bhupendra" not in e["company_name"] for e in evs)


def test_possessive_company():
    evs = ex("Reliance plant", "Officials attended the groundbreaking of Reliance Consumer Products' new beverage plant in Sanand, Gujarat. "
                               "The Reliance Group's new beverage plant in Sanand will be built in two phases.")
    assert evs and evs[0]["company_name"].startswith("Reliance")


def test_acronym_expanded_with_verbatim_proof():
    art = {"title": "Honda plans capacity expansion",
           "text": "Honda Motorcycle & Scooter India (HMSI) announced its plans today.\n"
                   "HMSI's new production line at Vithalapur in Gujarat will add capacity of 6,00,000 units."}
    evs = rule_extract(art, SPEC)
    assert evs[0]["company_name"] == "Honda Motorcycle & Scooter India"
    assert evs[0]["company_quote"] == "Honda Motorcycle & Scooter India (HMSI)"
    v = verify_event(evs[0], {**art, "published": "2026-11-20"}, SPEC, [])
    assert v["status"] == "VERIFIED", v["missing"]


def test_place_name_not_taken_as_company():
    evs = ex("Plant news", "Vithalapur will get a new plant in Gujarat next year, officials said.")
    assert all(e["company_name"] != "Vithalapur" for e in evs)


def test_noise_is_ignored():
    assert ex("Fire", "A massive fire broke out at a textile warehouse in Surat, Gujarat on Monday.") == []
    assert ex("Theft", "Three engineers stole 104 laptops from the company's new warehouse in Ahmedabad.") == []
    assert ex("IPO", "Medicap Healthcare plans to use the proceeds to purchase machinery for its existing manufacturing facility.") == []


def test_subsidiary_mentions_collapse_to_one_event():
    evs = ex("Heller Industries sets up first reflow oven plant in Gujarat",
             "Heller Industries has set up its first Indian manufacturing plant near Ahmedabad, Gujarat.\n"
             "Operated through its subsidiary Heller Thermal Systems Private Limited (HTS), the new facility in Ahmedabad "
             "will produce 40 ovens a month.")
    assert [e["company_name"] for e in evs] == ["Heller Industries"]


def test_dateline_city_is_not_the_plant_location():
    evs = rule_extract({"title": "Birla Carbon inaugurates new plant",
                        "text": "PRNewswire Mumbai (Maharashtra) [India] / Marietta [Georgia], November 20: Birla Carbon today "
                                "inaugurated its new 120 kMT carbon black manufacturing plant in Rayong, Thailand."},
                       {**SPEC, "states": ["Maharashtra"]})
    assert evs == [], "plant abroad must not become a Maharashtra lead"


def test_dateline_stripped_but_real_location_kept():
    evs = rule_extract({"title": "Heller plant", "text": "Mumbai, Nov 20 (PTI) US-based Heller Industries Inc on Friday "
                                                         "inaugurated its first Indian manufacturing facility near Ahmedabad."}, SPEC)
    assert evs and evs[0]["location_city"] == "Ahmedabad"
