# Winston — Phase 2

**Reliable intelligence + commercial learning foundation.**

Phase 0 removed the hazards. Phase 1 built the ledger that records what outreach
produces. Phase 2 builds the intelligence that decides *who* to contact, *what* to
offer, and *what to charge* — and does so from observed evidence rather than assertion.

---

## Inspection findings

Before placing any component, the existing architecture was audited against the
directive. Three findings determine the sequencing.

### 1. Winston stores no commercial signals — this blocks Sections 5 and 6

The `contacts` table holds 15 flat columns: identity, address, phone, website, three
social handles. That is all.

The directive's scoring inputs — website quality, booking capability, ordering
capability, social activity, review volume, technology gaps — **do not exist anywhere in
the schema**. Neither does anything that could produce them.

The scraper is the reason. `scrape_local_page()` fetches each site, runs `LocalLinkParser`
over the HTML, extracts email/phone/social, and **discards the HTML**. Every signal
needed to score a business passes through memory and is thrown away.

Scoring prospects on data Winston does not have would be fabricated intelligence. So
signal collection is not one Phase 2 feature among fifteen — it is the prerequisite that
unblocks scoring, offer matching, and pricing.

The remedy is cheap. The fetch already happens, the parse already happens. Extracting
signals from HTML that is already in memory costs **zero additional network requests and
zero AI calls** — which is also what the cost-optimization section demands: deterministic
code before local models, local models before free APIs, free APIs before paid ones.

### 2. Some directive sections are already satisfied by Phase 1

| Directive section | Status |
|---|---|
| 3. Commercial event ledger | **Exists.** `winston/commercial.py` — 9 tables, provenance on every row. Needs the event-type vocabulary widened. |
| 2. Real inbox ingestion | **Exists.** `winston/inbox.py` — 4-tier classification. Needs a confidence model, human correction, and real-mailbox validation. |
| 1. AI retry/backoff/health | **Partial.** Retry, backoff, and `provider_health()` landed in Phase 1. Missing: capability registry and task-based routing. |
| 11. Security/privacy | **Exists and must not regress.** Suppression, idempotency, atomic claiming, approval gates, dry-run, PII guards — all covered by tests. |

Rebuilding these would be churn. Phase 2 extends them.

### 3. Section 14 has prerequisites only the operator can satisfy

Real-world validation requires a `GEMINI_API_KEY` and a live mailbox scan. Neither is
something this work can provision. **Phase 2 cannot be declared complete without them**,
regardless of how much code lands.

---

## Component placement

| # | Directive section | Module | Depends on |
|---|---|---|---|
| 1 | AI provider reliability | `winston/providers.py` (registry) + `winston/ai.py` | — |
| 2 | Real inbox ingestion | `winston/inbox.py` (extend) | live mailbox |
| 3 | Commercial event ledger | `winston/commercial.py` (extend vocabulary) | — |
| 4 | Prospect memory | `winston/signals.py` + schema | **signal collection** |
| 5 | Opportunity scoring | `winston/scoring.py` | **signal collection** |
| 6 | Offer intelligence | `winston/catalog.py`, `winston/offers.py`, `winston/pricing.py` | scoring |
| 7 | Outcome learning | `winston/dataset.py` | scoring + offers + real outcomes |
| 8 | Dashboard | `templates/`, `static/`, routes | all of the above |
| 9 | Experimentation | `winston/experiments.py` | ledger + outcomes |
| 10 | Cost optimization | `winston/providers.py` + `winston/cache.py` | registry |
| 11 | Security/privacy | existing — **preserve, never weaken** | — |
| 12 | Testing | `tests/` | each milestone |
| 13 | Observability | `winston/observability.py` | — |
| 14 | Real-world validation | operator-gated | Gemini key + mailbox |
| 15 | UI/UX | `templates/`, `static/` | backend surfaces |

---

## Dependency order

```
        signal collection  ◄── the unblocking prerequisite
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
  prospect   opportunity   offer
   memory      scoring    matching
                 │           │
                 └─────┬─────┘
                       ▼
                    pricing
                       │
                       ▼
              outcome dataset  ◄── needs REAL outcomes, which need live sends
                       │
                       ▼
                 experiments / ML     ◄── Phase 3, not before
```

Provider registry, observability, and caching run parallel to this chain — nothing
depends on them, and everything benefits.

The UI comes last deliberately. Its purpose is to make Winston's reasoning visible; a
dashboard built before the reasoning exists would display empty panels, and building it
first would mean rebuilding it once the data model settles.

---

## Rules carried into every milestone

1. **Never fabricate.** A signal is recorded only when observed. Absent evidence is
   `unknown`, never a default value. This was already enforced in the Phase 1 funnel,
   where reply rates report `None` until an inbox scan has actually run.
2. **Everything explains itself.** No score, offer, or price is emitted without the
   evidence behind it and a confidence level.
3. **Protected characteristics are never inputs.** Not to scoring, not to offers, not to
   pricing — not inferred from names, language, neighbourhood, or imagery. Enforced by a
   hard allowlist plus a test that fails the build.
4. **Deterministic before paid.** Python, then cache, then local model, then free tier,
   then — only when explicitly configured — a paid model.
5. **Safety never regresses.** Suppression, idempotency, atomic claiming, human approval,
   and dry-run stay exactly as they are.

---

## Status

| Milestone | State |
|---|---|
| Phase 2 map | **Complete** — this document |
| M1 · Digital signal collection | **Complete** — see below |
| M2 · Opportunity scoring | Not started (unblocked by M1) |
| M3 · Offer matching + pricing | Not started |
| M4 · Provider capability registry | Not started |
| M5 · Inbox hardening + confidence model | Not started |
| M6 · Outcome dataset + experiments | Blocked on real outcomes |
| M7 · Dashboard + UI | Not started |
| Real-world validation | Blocked on operator |

Sections are filled in as milestones land.


---

## M1 · Digital signal collection

`winston/signals.py`. Reads commercial signals out of HTML the scraper already fetches.
Zero additional network requests beyond the pages researched, zero AI calls, no paid
services — deterministic pattern matching against platform fingerprints.

### What is detected

| Group | Signals |
|---|---|
| Capability | `online_booking`, `online_ordering`, `ecommerce` — 13 booking platforms, 9 ordering, 6 store platforms |
| Platform | `cms`, `client_rendered`, `analytics_tool`, `chat_tool` |
| Structure | `mobile_responsive`, `has_ssl`, `has_contact_form`, `has_title`, `has_meta_description`, `has_h1`, `image_alt_coverage` |
| Freshness | `copyright_year`, `years_since_copyright_update` |
| Weight | `script_count`, `page_bytes` |

Storage is `business_signals` (one row per contact/signal, upserted, carrying value,
confidence, evidence, source URL, method, and observation time) plus `research_runs`,
which exists so that "no signals" can be distinguished from "never looked".

### Two false-negative classes found against live sites

Synthetic fixtures passed cleanly. Running against real prospects did not, and both
failures would have produced confidently wrong recommendations.

**Client-rendered pages.** Wix and Squarespace businesses reported `has_contact_form:
False`, `has_analytics: False`, `has_chat_widget: False` — not because those features
were missing, but because those platforms build the DOM in JavaScript and the static
HTML had nothing to find. Winston would have recommended "add a contact form" to
businesses that already had one.

The fix: detect client-rendered platforms and **withhold negatives** on them. A positive
detection still counts — finding a chat widget is proof it exists — but failing to find
one on an unrendered page is now `unknown` rather than `False`. Server-rendered pages
still report genuine negatives, verified by test so the guard cannot over-suppress.

**Redirect-hidden SSL.** A barbershop reported `has_ssl: False` because the stored URL
was `http://www.thefadegame.com/`, which 301s to `https://thefadegame.com/`. The signal
read the address on file rather than the address actually served. `fetch_page()` now
returns the final URL after redirects, and SSL is judged on that.

Neither bug was theoretical. Both were caught only by running against the real web,
which is why live validation is a requirement rather than a formality.

### Merge semantics

Signals from several pages of one site fold into a single view. Capability signals are
optimistic — booking found on `/contact` counts even when the homepage lacked it —
because a positive observation anywhere is proof, while its absence on one page is not.
Structural signals prefer the homepage. Higher confidence breaks ties.

### Coverage

1 of 1,396 contacts researched at time of writing. The remaining 1,395 have **no**
signals, and every downstream consumer must treat them as unknown rather than
unqualified. `GET /research/coverage` reports this honestly.

### Routes

- `POST /research/<contact_id>` — research one business, return signals with evidence
- `GET /research/coverage` — how much of the base has evidence behind it

### Tests

20 tests in `tests/test_signals.py`. The ones that matter most cover absence:
client-rendered withholding, positive-detection survival, server-rendered negatives
still reporting, SSL after redirects, unfetchable sites yielding nothing, and malformed
HTML not crashing.
