# SignalHunt: empty trucks and idle space, sold to buyers worth having

**ContextCon 05 · Theme: Services, Replaced · Track: Revenue, with trust and compliance built in.**

SignalHunt turns an Indian 3PL's empty return trips and idle warehouse space into guaranteed pickup slots for
manufacturers who need them. Before any offer goes out it checks that the buyer is real, that the project is real on
the ground, and that the payment terms are safe. It sells the finished work (a priced offer and a booked meeting),
not a dashboard.

## 1. The service: who does it today, who pays

| | |
|---|---|
| **The job** | Fill a 3PL's perishable capacity (an empty Chennai to Pune return leg, 800 free cold-chain pallets in Chakan) with contract customers the 3PL would be happy to have. |
| **Who does it today** | Sales-development agencies and lead-research firms (paid per qualified lead or booked meeting), verification firms (paid per check), and the 3PL's own sales staff working the phone. |
| **Who pays for SignalHunt** | The head of sales or business head of a mid-size Indian 3PL. |
| **Why it matters** | 25 to 30 percent of Indian truck return trips run empty (KPMG, Jan 2026). Logistics firms wait 37 days on average to be paid (Razorpay). Fake firms and dummy brokers cost logistics companies crores (GST drives found 29,273 bogus firms in eight months). |
| **Input** | A request in the customer's words, e.g. *"Our trucks come back empty from Chennai to Pune every week, and we have 800 cold-chain pallets free in Chakan from December. Fill them with clients we'd be happy to have."* plus the 3PL's profile (`client_profile.json`: capacity sheet, rate card and floors, service limits, payment policy, exclusions). |
| **Finished output** | Per opportunity: a case file (every fact with source, exact quote or field, record date), a capacity match, a reality-ladder rung, a trust verdict, payment terms, and a priced offer at or above the floor. After a human approves: sent once, the reply verified, the meeting booked once and the capacity marked OFFERED. Per run: a capacity board, a refusal list, a watching list, a can't-verify list and the cost ledger. |

## 2. What it does now, and where it still needs a person

A run moves through four layers. Everything checkable (matching, dates, numbers, rules, verdicts) is plain Python. A
model is optional and only reads requests and articles and words drafts; the deterministic path runs with no model key.

| Layer | What happens | Code |
|---|---|---|
| **1. Find the freight** | Parse the request; discover dated news of new plants and warehouses (Bing/Google News RSS); verify every fact against a verbatim quote; resolve one record per company (Crustdata identify); match each site to an empty lane or free space. A lane match is **BOTH_ENDS** only with evidence at both ends: site at the origin and the company present at the destination (outbound), or site at the destination and the company already operating at the origin (inbound supplies can ride the empty leg). Presence comes from Crustdata headcount-by-region and job postings. | `agents/discovery.py`, `verifier.py`, `identity.py`, `lanes.py`, `ground.py` |
| **Reality ladder** | ANNOUNCED, FUNDED_OR_APPROVED, HIRING_ON_GROUND, OPERATING. Only ground evidence (hiring at the site, or operating) earns outreach. An MoU headline with nothing behind it becomes **WATCHING**, with no draft. | `agents/ladder.py` |
| **2. Trust every party** | Worth winning? Captive logistics group (client list), identity status, six-month headcount trend (Crustdata enrich), insolvency/NCLT/layoff news. Verdict PURSUE, PURSUE_WITH_CAUTION, DONT_PURSUE (no outreach) or CANNOT_VERIFY (held for a person). Thin evidence is never a refusal. | `agents/trust.py` |
| **3. Set safe terms** | By rule: PSU or government buyer, caution, or unverified gives a part advance; distress gives advance only; a much larger buyer gets fixed terms; an MSME-registered 3PL cites the 45-day MSMED Act window. | `agents/terms.py` |
| **4. Close the deal** | Offer: pickup slot, truck type, price at or above the floor, service promise, terms, the 3PL's trust card. Every sentence is checked in code: a sentence carrying a number that is in neither the verified facts nor the client profile is removed. A named person approves and types the recipient; it is sent once; the reply's sender domain is checked against the company's domains (free-mail is held); the meeting `.ics` is created once and the capacity is marked OFFERED so it is never promised twice. | `agents/reporter.py`, `approvals.py`, `replies.py` |

**Where a person is needed (by design):**
* approving, editing or rejecting every offer (nothing irreversible happens without a named approver);
* typing the recipient address (SignalHunt never guesses or buys contact details);
* deciding every CANNOT_VERIFY buyer, every reply held as an impostor risk or domain mismatch, a reply with no clear time, and a second buyer for capacity already offered.

## 3. What it could do in future, and why not now

* **Round-trip pairing across prospects** (two new flows in opposite directions sold as one balanced lane): needs stable volume estimates first.
* **Subcontractor chain-of-custody against GST and MCA records**: the portals sit behind captchas; access not confirmed.
* **Senior-departure check** (leaders who left in the last 120 days): needs a reliable past-role end-date filter; not validated today.
* **Time-machine test of the reality ladder** (rewind to 1 October, place projects with only older records, check today): needs a few days of runs to compare.
* **Crustdata watches** on WATCHING companies, clients and partners, so a change brings them back for review (free to create, 5 credits per notification).
* **Live price negotiation within the floor, contracts, dispatch, CRM sync, several 3PL clients at once, learning from which offers convert.** Each needs more time than the build window or data we have not confirmed.

## 4. The future of this service

Freight-matching apps fill one truck, one trip at a time. Trigger tools start from a customer profile and alert on
events. SignalHunt starts from the seller's perishable capacity, checks Indian announcements against ground evidence,
refuses bad business with evidence, and sets payment terms before the deal. As YC's Requests for Startups put it, an
AI-native service company sells the completed work, not the tool: here a filled truck and a safe client.

## 5. How to run it from a fresh clone

```bash
git clone https://github.com/AaditS04/signalhunt.git && cd signalhunt
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env ; then add CRUSTDATA_API_KEY (and optionally ANTHROPIC_API_KEY)
python -m uvicorn signalhunt.api:app --port 8000
# open http://localhost:8000 , go to Discover, paste the request
```

CLI (same pipeline):

```bash
python -m signalhunt.cli run "Our trucks come back empty from Chennai to Pune every week ..." --out result.json
python -m signalhunt.cli actions
python -m signalhunt.cli approve ACTION_ID --by "Your Name" --to teammate@pilani.bits-pilani.ac.in
python -m signalhunt.cli reply ACTION_ID --from buyer@their-domain.com --text "Thursday 3 PM works"
python -m signalhunt.cli resume      # after a crash: finishes interrupted runs from the step cache
```

Tests (mock fixtures only; the app always uses live data): `python -m pytest tests -q`

**Contact rule (event rule, enforced in code):** `ALLOWED_RECIPIENTS` in `.env` lists teammate and organiser addresses
or `@domains`; `approve()` refuses anything else. `ROLEPLAY_SENDERS` lists the teammate addresses that play the buyer;
their replies are accepted and labelled ROLE_PLAY on screen, never shown as the real company. No real outside person is
ever contacted.

## 6. How it's built

* **Agents and steps:** one durable pipeline (`workflow.execute_run`) of narrow steps, not a free-roaming agent. Request
  parser, discovery, fetcher, extractor (rules by default, Claude optional), deterministic verifier, identity resolver,
  de-dup, decision-makers, then the capacity layers (`lanes`, `ladder`, `trust`, `terms`), the offer, approval, send,
  reply check and booking.
* **Models:** none required. With `ANTHROPIC_API_KEY`, Claude (`claude-opus-5-5`, configurable) parses requests,
  extracts events with verbatim quotes and words intro emails. Offers are always template-built and number-guarded.
* **Where Crustdata is called:**

| Endpoint | Used for | Where |
|---|---|---|
| `POST /company/identify` | One record per company; lookalikes kept apart; the company's domains for the reply impostor check | `agents/identity.py` |
| `POST /company/enrich` (`headcount`) | Six-month headcount trend (trust); headcount by region (presence at the far end of a lane) | `agents/ground.py` |
| `POST /job/search` | Postings by the resolved company id in the last 60 days: hiring on the ground (ladder) and destination presence | `agents/ground.py` |
| `POST /person/search` | Current supply-chain or operations decision-maker at the resolved company id | `agents/people.py` |
| `POST /web/enrich/live` | Fallback fetch for pages that block direct fetch | `sources/fetcher.py` |

  Every call sends `x-api-version: 2025-11-01`, is spaced to the endpoint's rate limit, is priced from the
  `X-Credits-Used` header into the ledger, and goes through the step cache. Only a resolved company id is ever looked up.
* **Where state lives:** SQLite (WAL) in `data/signalhunt.db`: runs, step cache, opportunities, candidates, actions,
  replies, capacity offers, ledger, logs. Outbox files (`data/outbox/<key>.json`, `meeting_<key>.ics`) are created with
  `O_EXCL`. Client facts live in `client_profile.json`; limits and keys in `.env`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram.

## 7. Sources and checks

| Source | What it gives | Check |
|---|---|---|
| Bing News RSS, Google News RSS | Dated article links | Free; date re-checked from the page |
| Publisher pages | The evidence text and publication date | Fact kept only if its quote is found verbatim and the value is inside the quote |
| Crustdata (above) | Identity, domains, headcount, postings, people | Field and retrieval date shown with every fact |
| Google News RSS (distress) | Insolvency, NCLT, layoff headlines | Only headlines naming the exact company count; generic names are not searched |
| `client_profile.json` | Capacity, rates, floors, limits, policy, exclusions | Customer input, labelled client-supplied; all demo values for a fictional 3PL |

* **Facts vs assessments:** volume bands, ladder rungs, trust verdicts and payment tiers are labelled as assessments with
  reasons, never as facts. Model text is labelled "Model interpretation".
* **CANNOT_VERIFY over a wrong fact:** no date, no quote, unresolved identity, a failed lookup or a timeout gives
  CANNOT_VERIFY with what is missing. A timeout is "held for a person", never "no data".
* **Where a person approves:** every send (named approver, typed recipient, allow-list), every held reply, every
  CANNOT_VERIFY buyer.
* **Cost ledger:** every attempt (provider, operation, status, latency, credits, cost); per-run limits on cost, active
  seconds, model calls and articles end a run cleanly as STOPPED_LIMIT with partial, still-verified results.
* **Crash safety:** a kill at any moment and a restart repeats no completed call, sends no email twice and books no
  meeting twice (tested).
* **Personal data:** only name, current title at the resolved company, role and public profile URL. No personal email or
  phone is bought, stored or guessed.

## 8. Approaches tried

* **Start from a customer profile (what trigger tools do)** vs **start from the 3PL's empty capacity**: we chose capacity, because the offer then has a concrete slot and a rupee value at stake.
* **Keyword alerts** vs **qualification**: a live Job Search test for warehouse and logistics roles in India returned a US company in "Indian Trail, NC" and a "Supply Chain Cyber Security" role. Qualification (both-ends lanes, ladder, trust) is the product.
* **Crustdata job search location filter** vs **filtering in code**: `location.country = IND` returned zero and a fuzzy "Pune" matched "Pine Brook, NJ", so we fetch the company's postings by id and resolve each location to an Indian state ourselves.
* **One opaque score** vs **hard gates plus separate signals**: refusals and WATCHING are gates with evidence; the 0 to 100 score only ranks what passes.
* **Free-roaming agent** vs **narrow steps**: narrow steps are verifiable and crash-safe.
* **Crustdata web search for discovery** was measured at 2 usable URLs for 5 credits and about 100 s (most results were `google.com/goto` interstitials), so free RSS is primary and Crustdata is used for identity, headcount, postings and people.

**What worked:** deterministic verification, the both-ends lane rule, WATCHING for announcement-only projects, exactly-once
send and booking. **What didn't:** the rule extractor sometimes produces a bare word as a company (a live run produced
"Group"); the distress check now refuses to search generic names and the buyer stays CANNOT_VERIFY.

## Economics

* Cost per run is measured from the ledger (dashboard run page, `/api/runs/{id}` cost block).
* Earlier news-only Gujarat run: 25 articles, 7 verified opportunities, 14.8 Crustdata credits, no model cost. The capacity
  layers add one enrich (2 credits) and one job search (0.03 per posting) per resolved company.
* Price (assumption, in `client_profile.json`, to be checked against agency rates): Rs 15,000 per booked meeting or Rs
  2,500 per qualified opportunity; the outcome-based option is a share of revenue on filled lanes. Each offer carries the
  rupee value at stake from the 3PL's own rate card.
