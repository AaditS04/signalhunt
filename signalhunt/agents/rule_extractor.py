"""Deterministic buying-trigger extractor (no model).

Works sentence by sentence:
  1. keep sentences that state a NEW development (new/set up/inaugurate/invest/expand/foundation...) about a
     facility-type noun, and drop noise (fires, thefts, court cases, awards, IPO-size changes...);
  2. find the company with three grammatical patterns, best evidence first:
       passive     "...plant being established by TMC Transformers India Limited"
       possessive  "Reliance's ₹1,600 crore beverage plant"
       active      "Honda Motorcycle & Scooter India is investing Rs 2,420 crore..."
     and score candidates (legal suffix / corporate word / appears in headline / not a person or official);
  3. take the location from the same sentence, else the neighbouring sentence or the headline;
  4. every returned field is copied verbatim from the page, so the verifier can check it.
"""
from __future__ import annotations

import re

from .. import geo

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'‘“(])")

TOKEN = r"(?:[A-Z][A-Za-z0-9&.'’\-]*|[A-Z]{2,}(?:/[A-Z]{2,})?)"
NAME = rf"{TOKEN}(?:\s+(?:{TOKEN}|&|of|and|de)){{0,6}}"

AUX = (r"(?:(?:has|have|had|will|would|is|are|was|were|plans\s+to|planning\s+to|set\s+to|is\s+set\s+to|has\s+announced(?:\s+plans)?\s+to|"
       r"today|recently|formally|officially|also|now|on\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\s+)*")
VERB = (r"(?:open|announc|set(?:s|ting)?\s+up|launch|inaugurat|commission|invest|expand|unveil|start|build|built|construct|"
        r"establish|add|break(?:s|ing)?\s+ground|lay|laid|begin|began|commenc|acquir|nearing|bet|enter|roll(?:s|ed|ing)?\s+out|"
        r"scal(?:e|es|ed|ing)\s+up|ramp|develop|plan|signs?\b|signed|commits?|committed|unveil|operationali[sz]|"
        r"doubl|tripl|increas|boost|strengthen|secur(?=\w*\s+[^.]{0,70}?\b(?:land|plot|site)\b)|"
        r"receiv(?=\w*\s+(?:a\s+|the\s+)?(?:land|plot)))\w*")
ACTIVE_RE = re.compile(rf"({NAME})\s*(?:\([^)]{{1,40}}\)\s*)?(?:,[^,]{{0,90}},\s*)?{AUX}{VERB}")
PASSIVE_RE = re.compile(rf"(?:being\s+|been\s+|is\s+|was\s+|will\s+be\s+)?(?:established|set\s+up|built|developed|commissioned|"
                        rf"opened|launched|operated|constructed|promoted|owned|run|inaugurated|unveiled)\s+by\s+(?:the\s+)?({NAME})")
POSSESSIVE_RE = re.compile(rf"({NAME})['’]s\s+(?:[^.;]{{0,70}}?)(?:plant|facility|warehouse|factory|unit|hub|centre|center|park|"
                           rf"complex|campus|expansion|investment|project|fab|line)")

FACILITY = re.compile(r"\b(plant|factory|facility|facilities|manufacturing|production|warehouse|warehousing|fulfil?ment|"
                      r"distribution|logistics park|cold storage|storage|unit|hub|complex|campus|fab|assembly|capacity|"
                      r"production lines?|godown|dark stores?)\b", re.I)
NEWNESS = re.compile(r"\b(new|set(?:s|ting)? up|inaugurat\w*|commission\w*|open(?:s|ed|ing)?|launch\w*|expan\w*|foundation|"
                     r"ground-?breaking|greenfield|brownfield|upcoming|invest\w*|to build|building|construct\w*|"
                     r"capacity addition|add(?:s|ed|ing)?|first|plans?|planned|proposed|will produce|will manufacture|"
                     r"begins?|began|commenc\w*|scal\w* up|ramp\w* up|double|MoU|memorandum|establish\w*|"
                     r"being set up|strengthen\w*)\b", re.I)
NOISE = re.compile(r"\b(fire|blaze|gutted|theft|stole|stolen|robbery|looted|court|tribunal|judgment|verdict|accident|explosion|"
                   r"blast|arrest\w*|raid\w*|museum|award\w*|protest\w*|strike|collaps\w*|flood\w*|demolish\w*|shut\s?down|"
                   r"closure|layoffs?|seized|penalty|fined|probe|lawsuit|IPO size|cuts? IPO|bankrupt\w*|insolven\w*)\b", re.I)
HC = re.compile(r"\bHC\b")
EXISTING = re.compile(r"\bexisting\b", re.I)

CORP_WORDS = {"limited", "ltd", "pvt", "private", "industries", "motors", "steel", "pharma", "pharmaceuticals", "foods", "electronics",
              "technologies", "technology", "tech", "group", "corp", "corporation", "chemicals", "healthcare", "solutions", "systems",
              "energy", "power", "cements", "cement", "textiles", "auto", "automotive", "enterprises", "international", "global",
              "labs", "laboratories", "polymers", "plastics", "packaging", "dairy", "beverages", "ventures", "infra", "engineering",
              "manufacturing", "products", "consumer", "brands", "retail", "metals", "agro", "fertilisers", "fertilizers", "inc",
              "llp", "sons", "co", "company", "batteries", "solar", "mobility", "transformers", "thermal", "scooter", "motorcycle"}
STOP_LEADS = {"The", "This", "That", "These", "India", "Indian", "Government", "State", "Chief", "Minister", "In", "On", "At",
              "A", "An", "It", "He", "She", "They", "We", "According", "Officials", "Sources", "Company", "Its", "New", "After",
              "Union", "Central", "Prime", "District", "Collector", "Speaking", "Meanwhile", "However", "Also", "With", "For",
              "Under", "As", "By", "Read", "Watch", "Also", "Updated", "Share", "Advertisement", "PTI", "IANS", "ANI", "Reuters",
              "Mumbai", "Delhi", "First", "Global", "Local", "Industry", "Industries", "Shares", "Stock", "Our", "Their", "His",
              "Her", "Last", "Next", "Several", "Many", "Both", "All", "Beyond", "Besides", "Apart", "Through",
              "Following", "Earlier", "Recently", "Today", "Now", "Here", "There", "What", "When", "While", "Once",
              "Since", "Despite", "Amid", "Besides", "Additionally", "Further", "Moreover", "Overall", "Notably"}
PERSON_CONTEXT = re.compile(r"\b(Minister|CM|Chief Minister|MLA|MP|Collector|Commissioner|Secretary|Governor|Mayor|"
                            r"Chairman|Chairperson|CEO|MD|Managing Director|Founder|President|Director|Officer|Shri|Smt|Mr|Ms|Dr)\b")
WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
MONTHS = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec"}
TRAIL = {"Inc", "Inc.", "on", "has", "will", "Ltd", "Ltd.", "Limited", "is", "are", "and", "of", "&", "de"}
LOGISTICS_NAME = re.compile(r"\b(logistics|express|courier|freight|cargo|shipping|forwarders?|warehousing|3pl|supply chain solutions)\b", re.I)

TYPE_RULES = [  # (type, sentence regex) — first match wins
    ("WAREHOUSE_EXPANSION", re.compile(r"\b(warehouse|warehousing|cold storage|storage facility|logistics park|godown)\b", re.I)),
    ("DISTRIBUTION_OPERATIONS", re.compile(r"\b(distribution (?:centre|center|hub|facility)|sorting (?:centre|center)|depot|"
                                           r"mother hub)\b", re.I)),
    ("ECOMMERCE_GROWTH", re.compile(r"\b(fulfil?ment (?:centre|center)|dark stores?|quick commerce|e-?commerce)\b", re.I)),
    ("MANUFACTURING_EXPANSION", re.compile(r"\b(plant|factory|manufacturing|production (?:facility|line|unit|capacity)|"
                                           r"assembly|fab|capacity|facility|unit|complex)\b", re.I)),
    ("SUPPLY_CHAIN_HIRING", re.compile(r"\b(hir(?:e|es|ing)|recruit\w*|jobs?)\b.*\b(supply chain|warehouse|logistics|operations)\b", re.I)),
    ("FUNDING_OR_INVESTMENT", re.compile(r"\b(raises?|raised|funding|series [a-e]|invest\w*|MoU)\b", re.I)),
    ("GEOGRAPHIC_EXPANSION", re.compile(r"\b(expan\w* (?:into|to|in)|enters?|entry into|new market|launch\w* in)\b", re.I)),
]
INV_RE = re.compile(r"((?:Rs\.?|INR|₹)\s?[\d,.]+\s?(?:lakh\s)?(?:crore|cr\b|billion|million|bn|mn)|[\d,.]+\s?crore|"
                    r"(?:US)?\$\s?[\d,.]+\s?(?:billion|million|bn|mn))", re.I)
SCALE_RE = re.compile(r"([\d,.]+\s?(?:lakh\s|million\s)?(?:sq\.?\s?ft\.?|square feet|sq\.?\s?m|square metres?|acres?|hectares?|"
                      r"tonnes?(?: per (?:annum|day|month))?|tpa|MTPA|units(?: per (?:annum|month|year))?|MW|GW|GWh|MVA|pallets?))", re.I)


def split_sentences(text: str) -> list[str]:
    out = []
    for para in text.split("\n"):
        for s in SENT_SPLIT.split(para):
            s = s.strip()
            if s:
                out.append(s)
    return out


def clean_company(name: str) -> str:
    words = name.replace("’", "'").split()
    while words and (words[0] in STOP_LEADS or words[0] in WEEKDAYS or words[0] in MONTHS
                     or words[0].lower() in geo.CITY_TO_STATE or geo.resolve_state(words[0]) == words[0]
                     or words[0].endswith("-based") or words[0].lower() in ("its", "their", "the")):
        words = words[1:]
    while words and (words[-1] in TRAIL or words[-1] in WEEKDAYS or words[-1] in MONTHS):
        words = words[:-1]
    name = " ".join(words).strip(" ,.'\"")
    if name.endswith("'s"):
        name = name[:-2]
    # live findings: "and Mysuru", "Aluminium Division", "... Manufacturing Line The": fragments, divisions and
    # production lines are not companies
    while name and name.split()[-1] in ("The", "A", "An", "Its"):
        name = " ".join(name.split()[:-1])
    if name and (name.split()[0].lower() in ("and", "or", "of", "for", "with", "to") or
                 name.split()[-1].lower() in ("division", "unit", "segment", "department", "plant", "facility", "line")):
        return ""
    return name


def company_score(name: str, how: str, sentence: str, title: str) -> float:
    if not name or len(name) < 3 or len(name) > 70:
        return -99
    words = name.split()
    if all(w in STOP_LEADS or w in MONTHS or w in WEEKDAYS for w in words):
        return -99
    if geo.resolve_state(name) and name.lower() in {s.lower() for s in geo.STATE_CITIES} | set(geo.CITY_TO_STATE):
        return -99
    lw = {w.lower().strip(".,") for w in words}
    # a lone capitalised word ("Vithalapur", "Beyond") is usually a place or a sentence opener, not a company,
    # unless it is an acronym or the headline names it too
    if len(words) == 1 and not (lw & CORP_WORDS) and not name.isupper() and name not in title:
        return -99
    s = 0.0
    if lw & CORP_WORDS:
        s += 2
    if how in ("passive", "possessive"):
        s += 1.5
    if name in title:
        s += 1
    if len(words) >= 2:
        s += 0.5
    if re.search(r"[A-Z]{2,}", name):
        s += 0.3
    # names that sit right after an official's title are people, not companies
    idx = sentence.find(name)
    before = sentence[max(0, idx - 40):idx]
    if PERSON_CONTEXT.search(before) or PERSON_CONTEXT.search(name):
        s -= 4
    if len(words) == 2 and not (lw & CORP_WORDS) and all(w[0].isupper() and w[1:].islower() for w in words) and how == "active":
        s -= 0.8  # "Bhupendra Patel"-shaped: two plain capitalised words, likely a person
    return s


def find_company(sentence: str, title: str) -> tuple[str, str] | None:
    cands = []
    for how, rx in (("passive", PASSIVE_RE), ("possessive", POSSESSIVE_RE), ("active", ACTIVE_RE)):
        for m in rx.finditer(sentence):
            name = clean_company(m.group(1))
            sc = company_score(name, how, sentence, title)
            if sc > -1:
                cands.append((sc, name, how))
    if not cands:
        return None
    cands.sort(key=lambda x: -x[0])
    return cands[0][1], cands[0][2]


def classify(sentence: str, triggers: list[str]) -> str | None:
    for t, rx in TYPE_RULES:
        if t in triggers and rx.search(sentence):
            return t
    return None


def find_location(sentences: list[str], i: int, title: str, n_body: int | None = None) -> tuple[str, str, str]:
    """(city, state, quote) from the sentence, else its neighbours, else the headline."""
    n = len(sentences) if n_body is None else n_body  # neighbours are body sentences only, never the headline
    order = [i] + [j for j in (i - 1, i + 1) if 0 <= j < n and j != i]
    pools = [sentences[j] for j in order] + [title]
    for s in pools:
        if not s:
            continue
        body = strip_dateline(s)  # "Mumbai, Nov 20 (PTI) ..." is where the story was filed, not where the plant is
        low = body.lower()
        best = None
        for c, st in geo.CITY_TO_STATE.items():
            m = re.search(rf"\b{re.escape(c)}\b", low)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), body[m.start():m.end()], st)
        if best:
            return best[1], best[2], s
        sts = geo.states_in(body)
        if sts:
            st = sorted(sts, key=lambda x: low.find(x.lower()) if x.lower() in low else 999)[0]
            return "", st, s
    return "", "", ""


MONTH_RX = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
AGENCY_RX = r"\((?:PTI|IANS|ANI|Reuters|UNI|PRNewswire|BusinessWire|BSE|NSE)\)"
DATELINE_RE = re.compile(
    rf"^(?:[A-Z][^:.]{{0,110}}?\b{MONTH_RX}\s+\d{{1,2}}(?:,\s*\d{{4}})?\s*(?:{AGENCY_RX})?\s*[:\-–—]\s*"
    rf"|[A-Z][^:.]{{0,110}}?\b{MONTH_RX}\s+\d{{1,2}}(?:,\s*\d{{4}})?\s*{AGENCY_RX}\s*"
    rf"|[A-Z][\w ,()\[\]]{{0,60}}{AGENCY_RX}\s*[:\-–—]?\s*)")


def strip_dateline(s: str) -> str:
    return DATELINE_RE.sub("", s, count=1)


FOREIGN_RX = (r"(?:Thailand|Vietnam|Indonesia|Malaysia|Singapore|China|Japan|Korea|Taiwan|Bangladesh|Sri Lanka|Nepal|"
              r"Bhutan|UAE|Dubai|Abu Dhabi|Saudi Arabia|Oman|Qatar|Egypt|Kenya|Nigeria|South Africa|Africa|Europe|"
              r"Germany|France|Italy|Spain|Poland|Hungary|Czech|Netherlands|Belgium|UK|United Kingdom|England|"
              r"Ireland|Turkey|Russia|USA|U\.S\.|United States|America|Mexico|Canada|Brazil|Argentina|Australia|"
              r"New Zealand|Texas|Georgia|California|Ohio|Michigan|Ontario)")
FOREIGN_SITE = re.compile(rf"\b(?:plant|facility|factory|unit|warehouse|hub|centre|center|complex|campus|fab)\b[^.;]{{0,60}}?"
                          rf"\b(?:in|at|near)\s+(?:[A-Z][\w-]+,?\s+){{0,3}}{FOREIGN_RX}\b")


ACRO_RE = re.compile(rf"({NAME})\s*\(\s*([A-Z][A-Za-z]*[A-Z](?:/[A-Z]+)?(?:\s+India)?)\s*\)")


def acronym_map(text: str) -> dict[str, tuple[str, str]]:
    """'ArcelorMittal Nippon Steel India (AM/NS India)' -> {'AM/NS India': ('ArcelorMittal Nippon Steel India', <that passage>)}
    The passage is verbatim page text, so it becomes the quote that proves the expansion."""
    out = {}
    for m in ACRO_RE.finditer(text):
        full = clean_company(m.group(1))
        if full and len(full) > len(m.group(2)):
            out[m.group(2).strip()] = (full, m.group(0))
    return out


def rule_extract(art: dict, spec: dict, max_events: int = 3) -> list[dict]:
    title = (art.get("title") or "").strip()
    sents = [s for s in split_sentences(art.get("text", "")) if 30 <= len(s) <= 600]
    n_body = len(sents)
    if title and 25 <= len(title) <= 300:
        sents = sents + [title]
    article_low = art.get("text", "").lower()
    industry = next((k for k, kws in geo.INDUSTRIES.items() if any(kw in article_low for kw in kws)), "")
    acros = acronym_map(art.get("text", "") + " " + title)
    events, seen, foreign_cos = [], set(), set()
    for i, s in enumerate(sents[:80]):
        if NOISE.search(s) or HC.search(s):
            continue
        if FOREIGN_SITE.search(s):
            # the facility itself is abroad (e.g. "plant in Rayong, Thailand"): not an Indian 3PL lead, and the
            # headline about the same plant must not become a lead either
            f = find_company(s, title)
            if f:
                foreign_cos.add(acros.get(f[0], (f[0],))[0].lower())
            continue
        if not FACILITY.search(s) or not NEWNESS.search(s):
            continue
        if EXISTING.search(s) and not re.search(r"\b(new|expan\w*|additional|capacity)\b", s, re.I):
            continue
        etype = classify(s, spec["triggers"])
        if not etype:
            continue
        found = find_company(s, title)
        if not found:
            continue
        company, how = found
        company_quote = s
        if company in acros:
            company, company_quote = acros[company]
        city, state, loc_q = find_location(sents, i, title, n_body)
        if (company.lower(), etype, state) in seen:
            continue
        seen.add((company.lower(), etype, state))
        inv = INV_RE.search(s)
        scale = SCALE_RE.search(s)
        events.append({
            "company_name": company, "company_quote": company_quote, "event_type": etype,
            "event_summary": s if len(s) <= 300 else s[:297] + "...", "event_quote": s,
            "status": ("completed_or_inaugurated" if re.search(r"inaugurat|commissioned|opened|began operations", s, re.I)
                       else "announced_planned" if re.search(r"will|plans?|to set up|proposed|foundation|upcoming", s, re.I)
                       else "unclear"),
            "location_city": city, "location_state": state, "location_quote": loc_q,
            "event_date": "", "event_date_quote": "",
            "investment": inv.group(1).strip() if inv else "", "investment_quote": s if inv else "",
            "scale": scale.group(1).strip() if scale else "", "scale_quote": s if scale else "",
            "industry": industry,
            "company_is_logistics_provider": bool(LOGISTICS_NAME.search(company)),
            "match_pattern": how,
            "_score": company_score(company, how, s, title) + (1.0 if state else 0.0),
        })
    events = [e for e in events if e["company_name"].lower() not in foreign_cos]
    return select(events, max_events)


def select(events: list[dict], max_events: int) -> list[dict]:
    """One event per (trigger type, state) per article: subsidiaries, abbreviations and repeated mentions of the
    same development collapse to the best-evidenced company name. Precision over recall: a wrong company is
    worse than a missed one."""
    best: dict[tuple, dict] = {}
    for e in events:
        k = (e["event_type"], e["location_state"] or "?")
        if k not in best or e["_score"] > best[k]["_score"]:
            best[k] = e
    out = sorted(best.values(), key=lambda e: -e["_score"])
    # drop a location-less duplicate when the same company already has a located event
    located = {e["company_name"].lower() for e in out if e["location_state"]}
    out = [e for e in out if e["location_state"] or e["company_name"].lower() not in located]
    for e in out:
        e.pop("_score", None)
    return out[:max_events]
