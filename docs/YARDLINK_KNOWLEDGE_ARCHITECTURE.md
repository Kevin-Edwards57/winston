# YardLink knowledge architecture

How Winston knows what YardLink Studio sells, what it can prove, and which of the two
applies to a given prospect.

---

## The distinction this exists to enforce

**A portfolio project is not a product.**

Winston is proof that YardLink can build AI automation. Otonia is proof of consumer
mobile craft. Neither is something a prospect can buy. A system that cannot tell the
difference will pitch an internal tool to a barbershop, and a system that assumes every
project is sellable will make claims YardLink cannot honour.

## Entities

| Kind | Sellable | Meaning |
|---|---|---|
| `PRODUCT` | when status and verification allow | Something a customer can buy or license |
| `SERVICE` | yes | Work YardLink performs for a fee |
| `PORTFOLIO` | **never** | Evidence of capability, not an offer |
| `INTERNAL_TOOL` | **never** | Runs YardLink; may still be cited as proof |
| `FUTURE` | **never** | Planned, not yet deliverable |

### Statuses

`ACTIVE_PRODUCT` · `BETA_PRODUCT` · `SERVICE` — sellable
`COMING_SOON` · `PORTFOLIO_ONLY` · `INTERNAL_TOOL` · `EXPERIMENTAL` · `ARCHIVED` — not

Kind and status must agree. A `PORTFOLIO` entry cannot carry `ACTIVE_PRODUCT`; the
attempt raises `CatalogValidationError` rather than being silently coerced, because a
sellable gate that can be bypassed by mislabelling is not a gate.

## The two recommendation gates

**Gate 1 — status.** Only sellable statuses may be recommended, regardless of how well
an entry matches a prospect's problems.

**Gate 2 — verification.** An entry whose capabilities and pricing nobody has confirmed
is `verified = 0` and cannot be recommended.

This second gate exists because of how the catalogue was seeded. YardLink Eats, WedLink,
and GuardLink were seeded from **names alone**. Their capabilities, pricing, target
industries, and readiness are genuinely unknown, and inventing them in order to make
Winston look capable is the exact failure the directive forbids. They are recorded as
`EXPERIMENTAL`, unverified, and Winston will not offer them until a human fills them in.

Editing an entry's claims — kind, status, description, pricing, capabilities, problems
solved, industries — **revokes verification**, because the thing that was checked is no
longer the thing being sold. Cosmetic edits such as internal notes do not.

## Storage

| Table | Purpose |
|---|---|
| `catalog_entries` | One row per product/service/portfolio/internal item |
| `catalog_links` | Typed relationships: `proves`, `pairs_with`, `upsell`, `cross_sell`, `replaces` |
| `catalog_revisions` | Every create, update, verify, and archive, with actor and timestamp |

Deletion archives rather than destroys: history survives, recommendations stop.

Adding a product requires **no code change**. `POST /catalog` with a slug is sufficient,
and the fit engine picks it up on the next assessment.

## Prospect → YardLink matching

`winston/fit.py`. Three questions, answered separately.

### 1. What does this business need?

Problems are derived from `business_signals` — the evidence layer from Phase 2 M1 — and
**only from signals that were actually observed**. If `mobile_responsive` is unknown
because the page was client-rendered or unreachable, Winston does not conclude the site
is not mobile-friendly. Unknown lowers confidence; it never manufactures a problem to
sell against.

Capability gaps are industry-conditional. A restaurant with no online ordering has a
problem. A photographer with no online ordering does not.

Every derived problem carries a severity, a confidence, and the evidence string from the
signal that produced it.

### 2. What can YardLink genuinely provide?

Only entries passing both gates. Products and services are scored **separately** against
the observed problems, with an industry-match bonus.

### 3. What proves we can build it?

Entries linked by the `proves` relation. Proof is cited in outreach and never offered
for sale — enforced by test.

## Scores

Reported separately, never collapsed into one unexplained number:

| Score | Meaning |
|---|---|
| `PRODUCT_FIT` | How well the best sellable product matches observed problems |
| `SERVICE_FIT` | Same, for services |
| `PORTFOLIO_RELEVANCE` | Whether linked proof exists for the chosen offer |
| `PROBLEM_SEVERITY` | Severity of the worst observed problem |
| `COMMERCIAL_OPPORTUNITY` | `best_fit × severity × confidence` |
| `CONFIDENCE` | Evidence coverage behind the assessment |

`PRODUCT_FIT = 0` with a high `SERVICE_FIT` is meaningful, not a failure: it means no
packaged product suits this prospect and custom work is the honest offer. Winston states
that explicitly rather than forcing a product.

## Blockers

An assessment reports what is preventing a confident recommendation:

- *Not researched yet* — no evidence to reason from
- *No verified sellable catalogue entries* — Winston has nothing it is allowed to offer
- *No portfolio evidence linked* — outreach cannot cite proof

Blockers are surfaced rather than absorbed, so an empty recommendation is explained
rather than looking like a system that found nothing worth selling.

## Routes

| Route | Purpose |
|---|---|
| `GET /catalog` | All entries plus readiness |
| `GET /catalog/<slug>` | One entry with proof and revision history |
| `POST /catalog` | Create or edit — no code change needed |
| `POST /catalog/<slug>/verify` | Confirm claims, enabling recommendation |
| `POST /catalog/link` | Link proof to an offer |
| `GET /prospects/<id>/fit` | Full assessment with scores, problems, proof, blockers |

## Current state

6 seeded entries, **0 sellable**. `GET /catalog` reports `can_recommend: false` and lists
`yardlink-eats`, `wedlink`, and `guardlink` as awaiting verification with their missing
fields enumerated.

This is the honest state, not a broken one. Winston will produce no offers until the
operator describes what these products actually do.

## Future ML extension points

The scoring here is explainable rules, deliberately not presented as machine learning.
Every assessment already persists the inputs a model would need: observed signals with
provenance, derived problems with severity and confidence, the offer chosen, and the
scores that led to it. When the commercial ledger accumulates real outcomes against
those assessments, `_match_score` becomes a learned ranking function trained on which
recommendations actually produced replies, meetings, and revenue — without changing the
surrounding architecture.

That transition happens in Phase 3, and not before enough labelled outcomes exist to
support it.
