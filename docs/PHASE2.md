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

---

## Phase B · Pricing engine

`winston/pricing.py`. Scope and effort in, an explainable band out.

### It refuses more often than it quotes

A price is a commercial commitment YardLink then has to honour, so the engine needs a
configured rate card and an effort estimate for the service being sold. Without either it
raises `NoPricingBasis` and names the missing input. Guessing $1,200 because that sounds
like a website price would be inventing an obligation.

`GET /pricing` reports readiness honestly. At time of writing: no rate configured, and 15
of 15 services carry no effort estimate.

### Protected characteristics are structurally excluded

Not a policy someone has to remember. Every input passes through
`ALLOWED_PRICING_VARIABLES`, and any feature whose key or value matches a protected term
raises `ProtectedCharacteristicError` before arithmetic runs. Race, ethnicity,
nationality, religion, sex, disability, and cultural identity are not variables that were
left out; they are variables the engine cannot accept.

Proxies are rejected too, because `neighborhood_demographic` and `surname` are the same
discrimination with extra steps.

Matching splits whole words from stems deliberately. `age` and `black` match as complete
words so that `page_count` and `Blackbaud` are not falsely rejected, while `ethnic` and
`pregnan` match as prefixes so `ethnicity` and `pregnancy` are caught. A naive substring
check flagged `page_count`, which is what prompted the split.

`tests/test_pricing.py` enumerates every protected term and fails the build if any
becomes acceptable.

### Bands

```
delivery_cost = adjusted_hours x hourly_rate
floor         = delivery_cost x (1 + min_margin)
target        = floor x target_uplift
premium       = target x premium_uplift
```

Every multiplier is recorded as an `Adjustment` carrying its factor, value, reason, and
the evidence behind it, so a reviewer can disagree with a specific step rather than with
a number. Scope assumptions are listed separately.

### Discounts

Ten reason codes, each with a ceiling. A discount without a recognised reason is
rejected, a discount above its ceiling is rejected, and a discount that would breach the
margin floor is rejected with the arithmetic shown. No reason code references a protected
characteristic, enforced by test.

### Confidence is capped

There are zero won deals and zero proposals, so no comparable engagement exists for any
service. Confidence is capped accordingly and the rationale says so explicitly rather
than presenting an unvalidated number as authoritative.

### Known limitation

The default uplifts (35% minimum margin, 1.25 target, 1.35 premium) compound, and they
are **unvalidated**. They were chosen as plausible agency defaults, not derived from
YardLink's actual delivery costs or won deals, because neither exists yet. Absolute
prices are driven almost entirely by the effort estimates in the catalogue. A service
estimated at 10 hours prices very differently from one estimated at 30, and that estimate
is the operator's to set.

---

## Phase B.5 · Commercial calibration

`winston/ratecard.py`. Operator-editable commercial parameters where provenance is
structural rather than a comment.

### Basis travels with the number

Every price and effort value carries a `Basis`:

| Basis | Meaning | Evidence backed |
|---|---|---|
| `OPERATOR_ASSUMPTION` | A starting point someone chose | No |
| `OBSERVED` | Quoted in real proposals, outcomes unknown | No |
| `HISTORICAL` | Derived from completed engagements | Yes |
| `CALIBRATED` | Adjusted from measured close rates and margins | Yes |

The basis travels into the pricing engine, the API, and the review screen. Only
`calibrate_from_outcomes()` can raise it, and it refuses below eight closed engagements.
An operator editing a price **drops** the basis back to assumption, because typing a
number is asserting a judgement, not reporting a measurement.

Every entry is currently `OPERATOR_ASSUMPTION`. `GET /ratecard` says so in a `warning`
field rather than leaving it to be inferred.

### Having a price is not a decision to sell

Starter entries seed **disabled**. The pricing engine refuses to quote a disabled
service even when a price exists. Enabling requires both a target price and an effort
range.

### The starter card is internally checkable

The engine refuses when a configured price sits below the delivery cost its own effort
estimate implies, and names the three ways to resolve it. That check found a real
inconsistency: the seeded rates are healthy at a $40 to $60 internal hourly rate and
unprofitable above roughly $95 on the larger services.

| Service | Target | Effort | @$40/h | @$60/h | @$85/h | @$110/h |
|---|---|---|---|---|---|---|
| website-service | $950 | 8-15h | 59% | 38% | 13% | loss |
| website-redesign | $1,100 | 8-18h | 60% | 40% | 15% | loss |
| ai-chatbots | $950 | 6-18h | 57% | 36% | 9% | loss |
| landing-pages | $450 | 3-6h | 66% | 49% | 28% | 7% |

Scope multipliers may only raise a banded price, never lower it. Discounting a fixed
band for "low complexity" double-counts scope the operator already priced in, and drove
a target below delivery cost during testing.

Prices round to the nearest $25. Manufactured precision reads as false confidence.

---

## Phase C · Provider registry and routing

`winston/providers.py`. Routing by task difficulty, with escalation as a decision.

### Task classes

| Class | Work | Default route |
|---|---|---|
| `LIGHT` | classification, extraction, parsing | `llama3.2:3b` |
| `MEDIUM` | audits, drafting, synthesis | `qwen3:8b` |
| `HEAVY` | ambiguous reasoning, strategy | `qwen3:8b`, then cloud |
| `CRITICAL` | customer-facing commercial output | strongest configured |

Unknown purposes default to `MEDIUM`. The policy lives in settings and is editable;
invalid task classes and unknown provider keys are rejected.

`llama3.2:3b` is now registered as a second Ollama provider so the light tier is real
rather than notional. `AIService` previously instantiated only one Ollama model.

### Two invariants

**No silent paid escalation.** A paid provider is reachable only when zero-cost mode is
off. A free tier failing does not authorise spending money, and when Claude is selected
the decision records "Paid provider selected deliberately."

**Routing does not affect Guardian.** A stronger model decides who writes the text, not
whether the text may reach a prospect.

### Refusal over downgrade

With the current environment, `proposal_generation` routes to **nothing**: Gemini is
unconfigured, `qwen3:8b` is not marked suitable for critical work, and Claude is blocked
by zero-cost mode. Winston declines rather than quietly handling customer-facing
commercial output with a model not trusted for it.

Every decision records what was skipped and why.

### Measured, not declared

`performance()` reports success rate, latency, and cost per provider and task from
`provider_usage`, which records every call. Total AI cost to date: **$0.00**.
