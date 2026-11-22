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


# ---- regressions from the 2026-11-22 live sweep (sentences copied from the real articles) ----
def names(evs):
    return [e["company_name"] for e in evs]


KA = {**SPEC, "triggers": ["WAREHOUSE_EXPANSION", "MANUFACTURING_EXPANSION"], "states": ["Karnataka", "Andhra Pradesh"]}


def test_park_named_after_place_is_not_company():
    evs = rule_extract({"title": "IndoSpace expands its Karnataka footprint with the launch of Narasapura-II park",
                        "text": "The launch of Narasapura-II builds on IndoSpace’s established presence in the region through "
                                "Narasapura-I, a 1.60M sq. ft. industrial and logistics park that has attracted occupiers."}, KA)
    assert names(evs) == ["IndoSpace"]


def test_region_possessive_is_not_company():
    evs = rule_extract({"title": "Reliance commissions Asia's largest beverage plant at Orvakal in Andhra",
                        "text": "Reliance Consumer Products has commissioned Asia’s largest beverage plant at Orvakal in Kurnool, "
                                "just 12 months after ground-breaking, IT and HRD Minister Nara Lokesh said on Saturday."}, KA)
    assert names(evs) and names(evs)[0].startswith("Reliance") and "Asia" not in names(evs)


def test_leading_country_possessive_stripped():
    evs = rule_extract({"title": "ReNew plans upstream solar manufacturing in Andhra Pradesh",
                        "text": "India’s ReNew Energy Global has recently announced approximately INR 80,000 crore in investments in "
                                "the state of Andhra Pradesh, India, to develop solar manufacturing and digital infrastructure."}, KA)
    assert names(evs) == ["ReNew Energy Global"]


def test_state_prefix_kept_in_legal_name():
    evs = ex("Work begins at Sikka complex",
             "Meanwhile, at Sikka, work has commenced on schedule for an integrated phosphoric and sulphuric acid complex "
             "commissioned by Gujarat State Fertilizers & Chemicals Limited (GSFC) at Jamnagar, Gujarat.")
    assert names(evs) and names(evs)[0].startswith("Gujarat State Fertilizers & Chemicals")


def test_ministry_is_not_a_prospect():
    evs = rule_extract({"title": "ACME clears investment for Odisha green ammonia plant",
                        "text": "In July 2026, the Ministry of New and Renewable Energy announced a new 10-year agreement for a "
                                "Paradip facility in Odisha."}, {**SPEC, "states": ["Odisha"]})
    assert not any("Ministry" in n for n in names(evs))


def test_generic_product_word_is_not_company():
    evs = rule_extract({"title": "Ethanol plant row in Telangana",
                        "text": "Ethanol plant management said they will set up the new plant at Chittanur in Telangana."},
                       {**SPEC, "states": ["Telangana"]})
    assert "Ethanol" not in names(evs)


def test_short_name_expanded_to_full_name():
    evs = rule_extract({"title": "Jeh Aerospace inaugurates tooling center of excellence in Hyderabad",
                        "text": "Jeh Aerospace has opened a tooling centre in Hyderabad. Jeh Aerospace said the centre will cut lead "
                                "times. The capability will also help new programs ramp faster at Jeh’s new Mach-2 factory in "
                                "Hyderabad, which spans about 200,000 square feet."}, {**SPEC, "states": ["Telangana"]})
    assert names(evs) and all(n.startswith("Jeh Aerospace") for n in names(evs))


def test_historical_expansion_is_not_a_trigger():
    evs = rule_extract({"title": "Inside TVS ILP's Madurai park",
                        "text": "From 2016 to 2020, TVS ILP expanded its Grade-A warehousing network across India and opened a new "
                                "facility in Odisha."}, {**SPEC, "states": ["Odisha"]})
    assert evs == []


def test_title_case_headline_in_body_does_not_rename_company():
    for title, body, want in [
        ("Jeh Aerospace Opens Aero-Engine Tooling Centre In Hyderabad",
         "Jeh Aerospace Opens Aero-Engine Tooling Centre In Hyderabad\nJeh Aerospace Opens Aero-Engine Tooling Centre In Hyderabad\n"
         "Jeh has opened a new tooling centre at its Hyderabad plant in Telangana.", "Jeh"),
        ("Tata Steel Plans New Pellet Plant In Odisha",
         "Tata Steel Plans New Pellet Plant In Odisha\nTata Steel Plans New Pellet Plant In Odisha\n"
         "Tata Steel plans to set up a new pellet plant at Kalinganagar in Odisha.", "Tata Steel"),
        ("Safran To Invest Rs 300 Crore In Hyderabad Facility",
         "Safran To Invest Rs 300 Crore In Hyderabad Facility. Safran To Invest Rs 300 Crore again.\n"
         "Safran will invest Rs 300 crore to set up a new engine facility in Hyderabad, Telangana.", "Safran"),
    ]:
        evs = rule_extract({"title": title, "text": body}, {**SPEC, "states": ["Telangana", "Odisha"]})
        assert names(evs) == [want], (title, names(evs))
