# SignalHunt: Architecture

```mermaid
flowchart TB
    U[Sales user / judge<br/>Dashboard or CLI] -->|free-text request| API[FastAPI<br/>signalhunt/api.py]
    API -->|create run QUEUED| DB[(SQLite WAL<br/>runs · step_cache · opportunities<br/>run_candidates · actions · ledger · logs)]
    W[Background worker<br/>workflow.py] -->|pick RUNNING first, then QUEUED| DB

    subgraph PIPE[Pipeline: every external call goes through RunContext.step = memoized in step_cache]
        P1[1 Request Understanding<br/>LLM → TargetSpec<br/>validated: states, triggers, window] --> P2
        P2[2 Discovery<br/>queries per state × trigger<br/>canonical-URL + headline dedup<br/>date-window pre-filter] --> P3
        P3[3 Fetch article<br/>direct → Crustdata web fetch fallback<br/>publication date + the HTML field it came from] --> P4
        P4[4 Trigger extraction<br/>LLM: every field + verbatim quote<br/>fallback: rule extractor] --> P5
        P5[5 Evidence verification, deterministic<br/>quote must exist verbatim in page<br/>value must be inside its quote<br/>dated · in window · in geography · not a rival<br/>→ VERIFIED / CANNOT_VERIFY / REJECTED] --> P6
        P6[6 Identity resolution<br/>Crustdata company/identify<br/>exact normalised name only<br/>lookalikes listed, never merged] --> P7
        P7[7 Dedup + contradiction check<br/>group by identity · state · trigger family<br/>conflicting amounts → CANNOT_VERIFY] --> P8
        P8[8 Decision-makers<br/>Crustdata person/search<br/>current role at resolved company id] --> P9
        P9[9 Scoring, deterministic<br/>7 weighted components, reasons printed] --> Q1
        Q1[10 Ground evidence<br/>Crustdata company/enrich headcount + job/search postings<br/>resolved company id only] --> Q2
        Q2[11 Lane matcher<br/>site vs empty lanes and free space<br/>BOTH_ENDS / ORIGIN_ONLY / NONE] --> Q3
        Q3[12 Reality ladder<br/>ANNOUNCED → FUNDED → HIRING_ON_GROUND → OPERATING<br/>below ground evidence → WATCHING, no draft] --> Q4
        Q4[13 Worth winning?<br/>captive group · identity · headcount trend · distress news<br/>DONT_PURSUE → REFUSED · thin → CANNOT_VERIFY held] --> Q5
        Q5[14 Payment terms by rule<br/>PSU · caution · size · MSMED 45-day clause] --> P10
        P10[15 Offer or intro<br/>slot, price ≥ floor, promise, terms, trust card<br/>sentence-level number guard]
    end
    W --> PIPE

    P2 & P3 & P6 & P8 -->|HTTP| NET[net.request<br/>retries + backoff · Retry-After<br/>circuit breaker per provider]
    NET --> CD[(Crustdata API<br/>web/search/live · web/enrich/live<br/>company/identify · person/search)]
    NET --> RSS[(Bing News RSS · Google News RSS<br/>free live fallback)]
    NET --> WEB[(Publisher web pages)]
    P1 & P4 & P10 --> LLM[llm.structured<br/>Claude, JSON-schema output<br/>server-side refusal fallback]

    NET -->|every attempt: status, latency, credits, cost| LEDGER[[Cost ledger<br/>ledger table]]
    LLM -->|tokens × price| LEDGER
    LEDGER -->|cost / time / LLM-call limits<br/>LimitExceeded → STOPPED_LIMIT with partial results| W

    P10 -->|INSERT OR IGNORE<br/>idempotency_key = hash email,opp| ACT[actions: PENDING_APPROVAL]
    ACT -->|human: named approver + typed recipient| APPR{Approval gate<br/>approvals.py}
    APPR -->|conditional UPDATE<br/>APPROVED → SENDING| OUT[Outbox<br/>sandbox file O_EXCL / SMTP Message-ID=key]
    OUT --> SENT[SENT exactly once]
    APPR -->|reject| REJ[REJECTED]
    APPR -.->|recipient must be on ALLOWED_RECIPIENTS| OUT
    SENT -->|buyer reply| RP[replies.py<br/>sender domain vs Crustdata company domains<br/>free-mail → HELD · role-play labelled]
    RP -->|day + time parsed in code| BK[Meeting .ics O_EXCL<br/>capacity marked OFFERED once]
    BOOT[Process start] -->|reconcile SENDING actions<br/>resume RUNNING runs| W
```

## Where each required concern lives

| Concern | Where | How |
|---|---|---|
| Data calls | `sources/crustdata.py`, `sources/news_rss.py`, `sources/fetcher.py` | All through `net.request` |
| Retries | `net.py` | 2 retries, exponential backoff, honours `Retry-After`, 408/425/429/5xx only; circuit breaker after 3 consecutive failures → next provider |
| Fallbacks | `agents/discovery.py`, `sources/fetcher.py`, `llm.py` | Crustdata → Bing RSS → Google RSS; direct fetch → Crustdata fetch; LLM → rules/template |
| Cost ledger | `runtime.py` (`RunContext.record`), `ledger` table | Every attempt: provider, operation, status, latency, units (tokens / Crustdata `X-Credits-Used`), USD |
| Limits | `runtime.py` (`check_limits`, `check_llm_budget`) | Cost, active seconds (survives restarts), LLM calls, articles per run |
| Crash recovery | `runtime.RunContext.step`, `workflow.Worker` | Memoized steps in `step_cache`; RUNNING runs resumed on boot |
| Exactly-once actions | `approvals.py` | UNIQUE idempotency key, conditional state transitions, O_EXCL outbox, `reconcile()` on boot |
| Approval | `approvals.approve` | Named approver + human-typed recipient required; nothing sends otherwise |
| Evidence | `agents/verifier.py` | Deterministic verbatim-quote check; facts without a source never reach the output |
| Explainability | `agents/scoring.py` | Fixed weights, per-component reason, assumptions in every report |
| Capacity match | `agents/lanes.py`, `agents/ground.py` | Both-ends rule from Crustdata headcount-by-region and postings; cluster aliases (Chakan → Pune, Oragadam → Chennai) |
| Reality ladder | `agents/ladder.py` | Rung from dated source wording plus postings at the site in the last 60 days |
| Trust verdict | `agents/trust.py` | Captive groups (config), identity, six-month headcount trend, distress headlines naming the exact company |
| Payment terms | `agents/terms.py` | Pure rules; every tier carries reasons |
| Reply and booking | `replies.py` | Impostor check, time parsing, capacity lock, `.ics` with O_EXCL, reply row keyed on (action, sender, text) |
| Contact rule | `approvals.recipient_allowed` | `ALLOWED_RECIPIENTS` allow-list; nothing outside it is ever sent |
