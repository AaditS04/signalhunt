# SignalHunt — autonomous 3PL customer-acquisition agent

**ContextCon 05 · Track: Revenue** ("book meetings on triggers from the last 30 days").

## The job, and who pays for it today

A mid-size Indian 3PL needs a steady pipeline of companies that are about to need warehousing, transport or
fulfilment. Today it pays **sales-development / lead-research agencies** (or in-house SDRs) per qualified lead or per
booked meeting. They read the news, spot expansions, work out who the company is, find the operations or
supply-chain head, and write a first email.

SignalHunt does that whole job: **request → live discovery → evidence-verified buying triggers → resolved company →
decision-maker → transparent score → report → personalised email → human approval → sent exactly once.**

| | |
|---|---|
| **Input** | A free-text targeting request, e.g. *"Find companies in Gujarat that have announced warehouse expansion or manufacturing expansion in the last 30 days and could potentially require third-party logistics services."* |
| **Finished result** | A ranked list of qualified opportunities. Each has the company, trigger, what happened and when, every fact with source URL + exact quote + record date + retrieval time, why it may mean 3PL demand (labelled inference), likely logistics needs, a sourced decision-maker, a 0–100 score with breakdown, confidence, a sales approach and an approval-gated email. Plus an explicit **CANNOT_VERIFY** list (naming what is missing) and a **REJECTED** list (outdated, out of scope, rival, no event). |
| **Pass mark** | (1) Every fact in an opportunity is backed by a verbatim quote found in a dated live source inside the request window. (2) No duplicate opportunity for one event; no merge of lookalike companies. (3) Unsettled questions come out as CANNOT_VERIFY, never a guess. (4) A kill at any moment followed by a restart repeats no completed call and sends no email twice. (5) No email leaves without a named human approval. (6) The run stays inside its cost and time limits. |

## Data sources the service calls

| Source | Used for | Endpoint |
|---|---|---|
| Bing News RSS | **Primary** live discovery: direct article URLs + publication dates | `https://www.bing.com/news/search?format=rss` |
| Crustdata Company Identify | Company identity, lookalike separation (5 req/min, 0.2 credits) | `POST https://api.crustdata.com/company/identify` |
| Crustdata Person Search | Current supply-chain / operations / procurement leaders (5 req/min, ~0.23 credits) | `POST /person/search` |
| Crustdata Web Fetch | Fallback page fetch for pages that block direct fetch (10 req/min, 1 credit) | `POST /web/enrich/live` |
| Crustdata Web Search | Optional extra discovery (`CRUSTDATA_SEARCH_QUERIES`, default 0, see note) | `POST /web/search/live` |
| Google News RSS | Second discovery fallback | `https://news.google.com/rss/search` |
| Publisher web pages | The evidence itself (text, publication-date meta field) | direct HTTPS |
| Anthropic Claude (`claude-opus-5-5`, configurable) | Request parsing, event extraction with quotes, report/email wording | Messages API, JSON-schema output |

All Crustdata calls send `Authorization: Bearer` and `x-api-version: 2025-11-01`, are priced from the `X-Credits-Used`
header, and are spaced to each endpoint's rate limit as reported by `GET /account/endpoints`.

**Why Crustdata web search is off by default (measured 22 Nov 2026):** across 5 date-filtered news queries it returned
32 results. 27 were `google.com/goto` interstitial links that only resolve in a browser (they are not records you can
cite), and several were Hindi headlines the extractor cannot read. That left **2 usable URLs for 5 credits and ~100 s**.
Bing News RSS returns direct, dated article URLs for free. Crustdata is used where it is strongest: company identity and decision-makers.

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
copy .env.example .env            # then add ANTHROPIC_API_KEY and CRUSTDATA_API_KEY
python -m uvicorn signalhunt.api:app --port 8000
# open http://localhost:8000
```

CLI (same pipeline, JSON out):

```bash
python -m signalhunt.cli run "Find companies in Gujarat ... last 30 days" --out result.json
python -m signalhunt.cli resume                 # after a crash: finishes interrupted runs from the step cache
python -m signalhunt.cli actions                # list outreach drafts
python -m signalhunt.cli approve ACTION_ID --by "Your Name" --to buyer@company.com
```

Tests (mock fixtures only; the app itself always uses live data): `python -m pytest tests -q`

**Kill-and-restart demo:** start a run in the dashboard, kill the server (Ctrl+C or end the process) during
"verifying evidence", start it again. The run resumes; the run page shows "recovered after restart ×1 (N steps replayed
from cache)", and the ledger shows each page was fetched once.

### Default configuration: Crustdata + deterministic extraction (no LLM key needed)

The production setup runs **without any model**. `agents/rule_extractor.py` finds the company and the event using three
grammatical patterns: passive ("plant being established by TMC Transformers India Limited"), possessive ("Reliance's ₹1,600
crore plant") and active ("Honda Motorcycle & Scooter India is investing…"). It expands acronyms from their in-article
definition ("ArcelorMittal Nippon Steel India (AM/NS India)", which is kept as the verbatim proof), drops noise (fires,
thefts, court cases, IPO changes, "existing facility") and drops officials' names. Benefits: model cost ₹0 per job, every
extraction reproducible and explainable, and nothing can come from model memory.

On the live Gujarat request (22 Nov 2026) this gave 7 verified opportunities from 25 articles in about 64 s at ₹0 model cost.

| Key | Without it |
|---|---|
| `CRUSTDATA_API_KEY` | Discovery uses Bing/Google News RSS, JS-heavy pages can't be fetched, identity stays `UNRESOLVED_NAME_ONLY`, decision-makers are `CANNOT_VERIFY` (we never guess people). |
| `ANTHROPIC_API_KEY` (optional) | Not needed. If set, Claude does extraction and email wording; the deterministic verifier still decides every fact. |

## How correctness is enforced

* **Facts vs inference.** The model only *proposes* facts, each with a verbatim quote. `agents/verifier.py` (no model involved) keeps a fact only if the quote is found in the fetched page **and** the value is stated inside that quote. Everything the model writes about *why* a company may need a 3PL is labelled "Model interpretation".
* **Dates.** Every record carries its publication date and the HTML field it came from (e.g. `<meta property="article:published_time">`) or the feed's `pubDate`. No date gives CANNOT_VERIFY. A date before the window start gives REJECTED (OUTDATED).
* **Duplicates.** Listings are de-duplicated by canonical URL and headline. Verified events are grouped by (resolved identity, state, trigger family). One opportunity collects every independent source; the opportunity id is a hash of that key, so re-runs update it instead of duplicating it.
* **Lookalikes.** Names are compared only after deterministic normalisation. "Tata Steel Ltd" equals "Tata Steel", but "Tata Steel BSL" and "Acme Foods India" are separate entities. Crustdata candidates with similar but not equal names are listed as "lookalikes not merged". Two companies with the identical name and no distinguishing domain in the source give AMBIGUOUS.
* **Contradictions.** If verified sources disagree on an amount, no figure is reported. The field is shown as CANNOT_VERIFY with both values and their sources, and confidence drops.
* **Outreach guard.** A model-written email that contains any number not present in the verified facts is discarded for a template.

## Scoring (deterministic, 0–100)

| Component | Weight | Rule |
|---|---|---|
| Recency | 20 | linear from today (1.0) to window start (0.0) |
| Evidence strength | 20 | 1 independent source 0.6 · 2 → 0.85 · 3+ → 1.0 · −0.25 if sources conflict |
| Logistics relevance | 20 | warehouse/distribution 1.0 · manufacturing/e-commerce 0.9 · geographic 0.7 · hiring 0.6 · funding 0.5 |
| Geographic fit | 10 | in requested state (and client's serviceable states) |
| Industry fit | 10 | matches requested industries; 0.5 when unstated |
| Decision-maker | 10 | sourced current decision-maker found |
| Identity confidence | 10 | resolved 1.0 · name only 0.5 · ambiguous 0.2 |

Priority: HIGH ≥ 70, MEDIUM ≥ 50. Every report prints the breakdown, reasons and assumptions.

## Reliability

* **Crash recovery:** every external result is memoized in `step_cache` keyed by (run, step). On boot, `RUNNING` runs resume, and finished steps replay from SQLite with no new calls or cost. Active time survives restarts.
* **Exactly-once email:** `actions.idempotency_key` is UNIQUE (one email per opportunity). Transitions are conditional SQL updates (`PENDING_APPROVAL → APPROVED → SENDING → SENT`). The sandbox outbox creates `data/outbox/<key>.json` with `O_EXCL`. `reconcile()` on boot finishes any send interrupted mid-flight without duplicating it.
* **Source failures:** retries with backoff, then the next provider in the chain, plus a log line and a ledger row. Circuit breakers are scoped per API endpoint and per publisher domain, so one dead site never blocks the others. HTTP 429 is treated as back-pressure: wait, retry, and don't trip the breaker. A source that cannot be fetched gives CANNOT_VERIFY, never invented content.
* **Transient failures are never cached:** a rate-limited lookup or an unreachable page is retried when the run resumes.
* **Total outage:** if every discovery source is unreachable, the run ends `FAILED: SOURCES_UNAVAILABLE`, never "0 opportunities found". It resumes cleanly when the network returns.
* **Speed:** pages are fetched in parallel (6 at a time). Decision-maker lookups run on a second thread while identity resolution continues, because the two endpoints have separate rate limits.
* **Limits:** `MAX_RUN_COST_USD`, `MAX_RUN_SECONDS`, `MAX_LLM_CALLS_PER_RUN`, `MAX_ARTICLES_PER_RUN`. Hitting one ends the run as `STOPPED_LIMIT` with the partial, still-verified results.
* **Requirement changes:** client facts (exclusions, serviceable states, sender, prices, rules) live in `client_profile.json`; limits and model live in `.env`; scoring weights live in `agents/scoring.py:WEIGHTS`. None of these need code changes.

## Ethics and personal data

* Only business-role data is kept for people: name, current title at the resolved company, public professional-profile URL, and the source record. **No personal email or phone is collected, inferred or guessed.** The approver types the recipient address.
* A person is used only if Crustdata lists the role as *current* at the *resolved* company id. If the company identity is not resolved, people lookup is skipped, so a namesake's staff are never contacted.
* Nothing irreversible happens without a named human approval. Rejections are logged with the reviewer's name.
* Every decision is explainable: verified quotes, score breakdown, and why each candidate was rejected or could not be verified.
* Rivals (logistics providers, the exclusion list) are never treated as prospects.

## Economics

* **Measured cost per job** is recorded per run (dashboard → run page, `/api/runs/{id}` → `cost`): API calls, Crustdata credits, USD and INR.
* **Measured (live Gujarat run, 22 Nov 2026):** 25 articles, 7 verified opportunities, 5 with sourced decision-makers,
  **14.8 Crustdata credits ≈ ₹13 per job** (at an assumed $0.01/credit, set in `.env`), ₹0 model cost (no LLM).
  Identity and people lookups are cached across runs for 24 h, so repeat coverage of the same companies costs ~₹0.
* **Price (assumption, set in `client_profile.json`):** ₹2,500 per qualified, evidence-backed opportunity, or ₹15,000 per booked meeting. That is below what an outsourced SDR typically costs per meeting, and model plus data cost per job is a small fraction of the price.

## Feature status

**Implemented and tested:** request parsing (LLM + rules), live discovery (Crustdata / Bing / Google), article fetch with dated evidence, LLM + rule extraction, deterministic verification, CANNOT_VERIFY / REJECTED with reasons, duplicate clustering, contradiction detection, lookalike-safe identity (Crustdata), decision-makers (Crustdata), transparent scoring, reports, email drafts with number guard, approval gate, exactly-once outbox, crash recovery, cost ledger and limits, dashboard (6 views), CLI, 45 tests.

**Needs keys to exercise live:** Crustdata paths (identity, people, Crustdata search/fetch) and Claude paths. They are coded against the documented API shapes and unit-tested with fixtures.

**Pending / out of scope:** calendar booking after a reply, Crustdata job-postings signal, watchers for continuous monitoring, multi-client tenancy.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram.
