"""Static reference data: Indian states, major industrial cities -> state, trigger and industry taxonomies.
Deterministic, so geography checks never depend on a model."""
from __future__ import annotations

import re

STATE_CITIES: dict[str, list[str]] = {
    "Gujarat": ["Ahmedabad", "Surat", "Vadodara", "Baroda", "Rajkot", "Bhavnagar", "Jamnagar", "Gandhinagar", "Kutch", "Kachchh",
                "Mundra", "Kandla", "Sanand", "Dholera", "Bharuch", "Dahej", "Hazira", "Vapi", "Halol", "Mehsana", "Anand",
                "Kheda", "Morbi", "Gandhidham", "Ankleshwar", "Becharaji", "Mandal", "Valsad", "Navsari", "Junagadh",
                "Porbandar", "Palanpur", "Viramgam", "Kalol", "Chhatral", "Changodar", "Bavla", "Savli", "Pipavav", "GIFT City",
                "Dahod", "Godhra", "Himmatnagar", "Surendranagar", "Amreli", "Botad", "Tapi", "Vyara"],
    "Rajasthan": ["Jaipur", "Jodhpur", "Udaipur", "Kota", "Ajmer", "Bhiwadi", "Neemrana", "Alwar", "Bikaner", "Bhilwara",
                  "Sri Ganganagar", "Chittorgarh", "Pali", "Sikar", "Jhunjhunu", "Banswara", "Barmer", "Kishangarh", "Ghiloth",
                  "Bhilwara", "Rajsamand", "Beawar", "Tonk", "Dausa"],
    "Maharashtra": ["Mumbai", "Pune", "Nagpur", "Nashik", "Aurangabad", "Chhatrapati Sambhajinagar", "Thane", "Bhiwandi", "Chakan",
                    "Raigad", "Navi Mumbai", "Kolhapur", "Satara", "Jalna", "Ratnagiri", "Talegaon", "Ranjangaon", "Palghar",
                    "Butibori", "Supa", "Shirwal", "Khopoli", "Panvel", "JNPT", "Nhava Sheva", "Wardha", "Amravati", "Solapur"],
    "Karnataka": ["Bengaluru", "Bangalore", "Mysuru", "Mysore", "Hubballi", "Hubli", "Dharwad", "Belagavi", "Belgaum", "Mangaluru",
                  "Mangalore", "Tumakuru", "Tumkur", "Kolar", "Narasapura", "Doddaballapur", "Hoskote", "Bidadi", "Kalaburagi"],
    "Tamil Nadu": ["Chennai", "Coimbatore", "Hosur", "Sriperumbudur", "Madurai", "Tiruchirappalli", "Trichy", "Tiruppur", "Tirupur",
                   "Salem", "Oragadam", "Thoothukudi", "Tuticorin", "Krishnagiri", "Ranipet", "Vellore", "Kanchipuram", "Erode"],
    "Telangana": ["Hyderabad", "Warangal", "Secunderabad", "Medchal", "Shamshabad", "Sangareddy", "Zaheerabad", "Karimnagar"],
    "Andhra Pradesh": ["Visakhapatnam", "Vizag", "Vijayawada", "Sri City", "Anantapur", "Anantapuramu", "Nellore", "Tirupati",
                       "Kurnool", "Kakinada", "Guntur", "Krishnapatnam", "Chittoor"],
    "Haryana": ["Gurugram", "Gurgaon", "Manesar", "Faridabad", "Panipat", "Sonipat", "Rohtak", "Bahadurgarh", "Jhajjar",
                "Kharkhoda", "Rewari", "Bawal", "Karnal", "Ambala", "Hisar", "Palwal", "Nuh"],
    "Uttar Pradesh": ["Noida", "Greater Noida", "Lucknow", "Kanpur", "Ghaziabad", "Agra", "Varanasi", "Prayagraj", "Jewar",
                      "Gorakhpur", "Meerut", "Aligarh", "Bareilly", "Moradabad", "Unnao", "Hapur", "Mathura", "Jhansi"],
    "Delhi": ["New Delhi", "Delhi NCR", "Okhla", "Narela", "Bawana"],
    "Madhya Pradesh": ["Indore", "Bhopal", "Pithampur", "Jabalpur", "Gwalior", "Dewas", "Ujjain", "Mandideep", "Sagar", "Ratlam"],
    "West Bengal": ["Kolkata", "Howrah", "Kharagpur", "Durgapur", "Siliguri", "Asansol", "Haldia", "Hooghly"],
    "Punjab": ["Ludhiana", "Mohali", "Amritsar", "Jalandhar", "Rajpura", "Patiala", "Bathinda"],
    "Odisha": ["Bhubaneswar", "Cuttack", "Jharsuguda", "Kalinganagar", "Paradip", "Angul", "Rourkela", "Sambalpur", "Gopalpur"],
    "Kerala": ["Kochi", "Cochin", "Thiruvananthapuram", "Trivandrum", "Kozhikode", "Calicut", "Thrissur", "Palakkad"],
    "Assam": ["Guwahati", "Dibrugarh", "Silchar", "Jagiroad"],
    "Bihar": ["Patna", "Muzaffarpur", "Bhagalpur", "Gaya", "Begusarai"],
    "Jharkhand": ["Jamshedpur", "Ranchi", "Bokaro", "Dhanbad", "Hazaribagh"],
    "Chhattisgarh": ["Raipur", "Bhilai", "Durg", "Bilaspur", "Korba"],
    "Uttarakhand": ["Haridwar", "Dehradun", "Rudrapur", "Pantnagar", "Sitarganj", "Kashipur", "Roorkee"],
    "Himachal Pradesh": ["Baddi", "Solan", "Nalagarh", "Una", "Shimla"],
    "Goa": ["Panaji", "Verna", "Margao", "Vasco"],
    "Jammu and Kashmir": ["Jammu", "Srinagar", "Kathua", "Samba"],
    "Dadra and Nagar Haveli and Daman and Diu": ["Silvassa", "Daman", "Diu"],
    "Meghalaya": ["Shillong", "Byrnihat"],
    "Tripura": ["Agartala"],
    "Manipur": ["Imphal"],
    "Nagaland": ["Kohima", "Dimapur"],
    "Mizoram": ["Aizawl"],
    "Arunachal Pradesh": ["Itanagar"],
    "Sikkim": ["Gangtok"],
    "Puducherry": ["Pondicherry"],
    "Chandigarh": [],
}
STATE_ALIASES = {
    "UP": "Uttar Pradesh", "MP": "Madhya Pradesh", "TN": "Tamil Nadu", "AP": "Andhra Pradesh", "WB": "West Bengal",
    "NCR": "Delhi", "Orissa": "Odisha", "Bombay": "Maharashtra", "HP": "Himachal Pradesh", "J&K": "Jammu and Kashmir",
    "Pondicherry": "Puducherry",
}

CITY_TO_STATE = {c.lower(): s for s, cities in STATE_CITIES.items() for c in cities}

EVENT_TYPES = {
    "WAREHOUSE_EXPANSION": "Warehouse expansion",
    "MANUFACTURING_EXPANSION": "Manufacturing expansion",
    "GEOGRAPHIC_EXPANSION": "Geographic expansion",
    "ECOMMERCE_GROWTH": "E-commerce growth",
    "SUPPLY_CHAIN_HIRING": "Supply-chain hiring",
    "DISTRIBUTION_OPERATIONS": "New distribution operations",
    "FUNDING_OR_INVESTMENT": "Funding or business expansion",
}

TRIGGER_KEYWORDS: dict[str, list[str]] = {
    "WAREHOUSE_EXPANSION": ["warehouse", "warehousing", "storage facility", "cold storage", "logistics park", "godown"],
    "MANUFACTURING_EXPANSION": ["manufacturing", "plant", "factory", "production facility", "manufacturing unit",
                                "facility", "capacity expansion", "greenfield", "brownfield", "unit"],
    "GEOGRAPHIC_EXPANSION": ["expand", "expansion", "enters", "new market", "new region", "new office", "launches in"],
    "ECOMMERCE_GROWTH": ["e-commerce", "ecommerce", "d2c", "online", "quick commerce", "fulfilment", "fulfillment", "dark store"],
    "SUPPLY_CHAIN_HIRING": ["hiring", "recruit", "jobs", "supply chain", "logistics manager", "warehouse manager"],
    "DISTRIBUTION_OPERATIONS": ["distribution centre", "distribution center", "distribution hub", "depot", "hub", "sorting centre"],
    "FUNDING_OR_INVESTMENT": ["raises", "funding", "investment", "invest", "crore", "mou", "series a", "series b", "capex"],
}

QUERY_TEMPLATES: dict[str, list[str]] = {
    "WAREHOUSE_EXPANSION": ["{geo} new warehouse -fire -blaze", "{geo} cold storage facility -fire",
                            "{geo} warehouse expansion company -fire", "{geo} logistics park lease sq ft"],
    "MANUFACTURING_EXPANSION": ["{geo} new manufacturing plant", "{geo} plant expansion investment crore",
                                "{geo} factory inaugurated"],
    "GEOGRAPHIC_EXPANSION": ["company expands operations {geo}"],
    "ECOMMERCE_GROWTH": ["{geo} fulfilment centre e-commerce"],
    "SUPPLY_CHAIN_HIRING": ["{geo} hiring supply chain warehouse jobs expansion"],
    "DISTRIBUTION_OPERATIONS": ["{geo} distribution centre opens", "{geo} new depot distribution network expansion"],
    "FUNDING_OR_INVESTMENT": ["{geo} company MoU investment new unit"],
}

INDUSTRIES = {
    "manufacturing": ["manufactur", "plant", "factory", "industrial", "production"],
    "e-commerce": ["e-commerce", "ecommerce", "online", "d2c", "marketplace", "quick commerce"],
    "fmcg": ["fmcg", "consumer goods", "food", "beverage", "dairy", "snack"],
    "pharma": ["pharma", "drug", "medicine", "api ", "formulation"],
    "automotive": ["auto", "automobile", "vehicle", "ev ", "electric vehicle", "two-wheeler", "tyre"],
    "chemicals": ["chemical", "petrochemical", "specialty chemical", "fertiliser", "fertilizer"],
    "textiles": ["textile", "apparel", "garment", "yarn", "fabric"],
    "electronics": ["electronics", "semiconductor", "mobile phone", "solar", "battery", "cell"],
    "retail": ["retail", "store", "supermarket"],
    "steel and metals": ["steel", "metal", "aluminium", "copper"],
    "renewable energy": ["solar", "wind", "renewable", "green hydrogen"],
    "cement and building materials": ["cement", "tiles", "ceramic", "building material"],
}

# Words a REQUEST uses for an industry (narrower than the article keywords above, which include
# generic words like "plant" or "store" that would over-match a request).
INDUSTRY_REQUEST_WORDS = {
    "pharma": ["pharma", "pharmaceutical", "drug maker", "api maker"],
    "automotive": ["auto", "automotive", "automobile", "auto component", "auto parts", "ev maker", "two-wheeler", "tyre"],
    "chemicals": ["chemical", "chemicals", "petrochemical", "fertiliser", "fertilizer"],
    "textiles": ["textile", "textiles", "apparel", "garment", "yarn", "mills"],
    "electronics": ["electronics", "semiconductor", "mobile phone", "battery"],
    "retail": ["retail", "retailer", "supermarket"],
    "steel and metals": ["steel", "metal", "metals", "aluminium", "copper"],
    "renewable energy": ["solar", "renewable", "green hydrogen", "wind energy"],
    "fmcg": ["fmcg", "consumer goods", "food processing", "food", "beverage", "dairy"],
    "e-commerce": ["e-commerce", "ecommerce", "d2c", "quick commerce"],
    "cement and building materials": ["cement", "tiles", "ceramic", "building material"],
}

# Places outside India that a request might name (the client serves India only).
FOREIGN_PLACES = ["dubai", "uae", "abu dhabi", "saudi", "usa", "united states", "america", "uk", "united kingdom",
                  "london", "singapore", "china", "bangladesh", "sri lanka", "nepal", "vietnam", "thailand",
                  "malaysia", "indonesia", "germany", "europe", "africa", "japan", "australia", "canada"]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def resolve_state(text: str) -> str | None:
    """Map a place string (state or city) to an Indian state, deterministically."""
    t = norm(text)
    if not t:
        return None
    for s in STATE_CITIES:
        if s.lower() == t or re.search(rf"\b{re.escape(s.lower())}\b", t):
            return s
    for alias, s in STATE_ALIASES.items():
        if t == alias.lower():
            return s
    for city, s in CITY_TO_STATE.items():
        if re.search(rf"\b{re.escape(city)}\b", t):
            return s
    return None


def states_in(text: str) -> set[str]:
    t = norm(text)
    found = set()
    for s in STATE_CITIES:
        if re.search(rf"\b{re.escape(s.lower())}\b", t):
            found.add(s)
    for city, s in CITY_TO_STATE.items():
        if re.search(rf"\b{re.escape(city)}\b", t):
            found.add(s)
    return found
