# Winston — Architecture Audit

**Date:** 2026-08-23
**Scope:** Full repository audit preceding the sales-intelligence transformation.
**Status:** Pre-implementation. No production code modified.

Every claim below was verified against the repository, the SQLite database, and a full
test run. Where the README or prior assumptions disagree with reality, reality is recorded.

---

## 1. Current architecture

Winston is a two-headed system mid-migration from a single-file Flask app to a package.

```
                    ┌──────────────────────────────────────┐
                    │  winston_app.py  (1,894 lines, 77KB) │
                    │  legacy monolith — the running app   │
                    └──────────────────────────────────────┘
                                     │
  Google Places API ──► run_scan() ──┼──► fetch_local_html() ──► extract_emails/phone/social
  (textsearch + details)             │    (requests + stdlib HTMLParser, 2MB cap)
                                     │
                                     ├──► write_email() ──► AIService.generate()
                                     │
                                     ├──► state["pending"]  ◄── IN-MEMORY ONLY, lost on restart
                                     │
                                     └──► Flask dashboard (templates/dashboard.html)
                                              │
        approve ──► create_draft ──► reviewed ──► approved   [drafts persisted HERE, not before]
                                                     │
                            POST /drafts/<id>/queue ─┤
                                                     ▼
                            POST /send-jobs/<id>/confirm ──► claim_send() ──► send_email_fn()
                                                                                  │
                                                                            Gmail SMTP_SSL:465

                    ┌──────────────────────────────────────┐
                    │  winston/  (853 lines) — the future  │
                    │  repository.py 504 | ai.py 184       │
                    │  migration.py 164  | __init__.py 5   │
                    └──────────────────────────────────────┘
```

**Persistence is currently dual-write.** SQLite (`winston.db`, 11 tables, WAL mode, foreign
keys on, busy timeout, IMMEDIATE transactions) is authoritative for the workflow state
machine. Legacy JSON files (`contacts.json`, `social_leads.json`, `emailed.json`,
`followups.json`, `stats.json`) are still read and written by the running app via
`load_json`/`save_json` with atomic replace.

**AI routing** (`winston/ai.py`) is a provider list walked in order — Gemini → Ollama →
Claude — with `zero_cost_mode` skipping any provider flagged `paid` *before* the network
call. Every attempt is written to `provider_usage` with latency, tokens, cost, and error.
A failed free call never escalates to a paid one. This design is sound.

**Tests:** 21 tests across 4 files, **all passing**. Coverage is concentrated on the
safety-critical paths (state machine, suppression, idempotency, AI routing, scraper).

---

## 2. Technical debt

Ranked by how much it will obstruct the transformation.

| # | Debt | Severity | Why it blocks the mission |
|---|---|---|---|
| 1 | **No outcome data anywhere.** 139 emails sent; zero reply/meeting/close/revenue tracking. Only `send_jobs.status` exists, and it stops at `sent`. | **Critical** | §14–17 (ML, expected revenue, learning loop) are unbuildable. There are no labels. Zero positive examples. |
| 2 | **Ollama success rate is 20.6%** (76 of 369 calls). `provider_usage` contains *no* Gemini rows at all — the free tier has never successfully served a request. | **Critical** | The generation layer fails 4 times out of 5. Every downstream engine inherits that failure rate. |
| 3 | **Review queue is in-memory.** `state["pending"]` is a module-level dict. Drafts are only persisted at approve-time — which is why `drafts` has **0 rows** despite 139 historical sends. | **Critical** | Restart loses the queue. Persistent prospect memory (§3) is impossible on this foundation. |
| 4 | **1,894-line monolith** with routes, scraping, SMTP, prompts, Places API, and CSV export in one module. | High | Every new engine (scoring, pricing, offers) has nowhere to live but the monolith. |
| 5 | **No AI caching of any kind.** No hash, no TTL, no memo. Confirmed absent from `ai.py` and `repository.py`. | High | Directly violates §20. Re-pays for identical inference. |
| 6 | **`contacts` table has 15 flat columns** and no room for signals, weaknesses, timeline, or provenance. Only 4 indexes exist. | High | The prospect intelligence layer (§3–4) needs a real schema. |
| 7 | **`check_and_send_followups()` bypasses the state machine entirely.** It reads `followups.json` and calls `send_email_fn()` directly — no suppression check, no idempotency, no claim lock. Gated *only* by a settings flag. | High | One flag flip re-enables an unsafe send path that the new safeguards do not cover. |
| 8 | **`send_email_fn()` has no dry-run mode** and no rate limiting. | High | Required by §26. Makes end-to-end testing risky. |
| 9 | Orphaned `FIRECRAWL_KEY` in `.env` — zero code references. | Low | Dead credential. Delete. |
| 10 | **README stats are stale and scrambled**: claims 1,149 leads / 131 emails / 1 follow-up. Actual `stats.json`: 1,749 / 139 / **131**. | Low | Misleading, and the follow-up figure matters (see §5). |
| 11 | `winston_dashboard.png` — 5.9MB binary committed to git. | Low | Repo bloat; may render real prospect emails. |

---

## 3. Existing capabilities

What genuinely works today and should be **preserved, not rewritten**:

- **Local zero-cost scraping.** `fetch_local_html` + `LocalLinkParser` + `discover_contact_pages`
  fetch public HTML with a 2MB cap and content-type enforcement, follow same-domain
  contact/about links, and extract emails, phones, and explicitly-linked social profiles.
  No paid scraping dependency. This is good, cheap infrastructure.
- **Email quality scoring.** `email_quality_score` / `is_valid_email` already rank
  `info@` vs `owner@` vs role addresses.
- **Provider telemetry.** `provider_usage` records purpose, model, success, latency,
  tokens, cost, error — per call. The observability primitive for §19 and §32 already exists.
- **The send state machine.** Genuinely well engineered (see §5).
- **Migration with audit trail.** `winston/migration.py` backs up to timestamped copies,
  stores every source row unchanged in `legacy_import_records` keyed by content hash, and
  never mutates the JSON source.
- **Deduplication.** Partial unique indexes on `place_id` and `normalized_email`.
- **Structured events.** 1,656 rows in `activity_events` — a usable timeline seed.

---

## 4. Existing data

| Store | Records | Notes |
|---|---|---|
| `contacts` (SQLite) | **1,172** | 578 with email · 981 with website · 1,126 with phone · 1,131 with place_id |
| — *researchable* (email **and** website) | **537** | The real addressable universe for Phase 1 |
| `sent_messages` | 139 | Historical sends, no outcomes attached |
| `activity_events` | 1,656 | Timeline seed |
| `provider_usage` | 369 | 76 successes, $0.00 spend |
| `legacy_import_records` | 1,861 | Immutable import audit |
| `drafts` / `send_jobs` / `suppressions` | **0 / 0 / 0** | New state machine never exercised in production |
| `contacts.json` | 1,355 (613 emails) | Legacy; superset of DB by 183 rows |
| `social_leads.json` | 426 | Businesses with social but no email — untapped |
| `followups.json` | 139 (122 unique emails) | **Tracked in git** |

**Category concentration:** Jamaican restaurant (114), barbershop (90), restaurant (89),
hair salon (76), dentist (67), cleaning service (65), photographer (60), auto repair (60).

Two facts worth naming plainly:

1. **The new state machine has never run in production.** Every one of the 139 sends went
   out through the legacy JSON path. The safety architecture is built and unit-tested but
   commercially unproven.
2. **`contacts.json` holds 183 more records than the DB.** Migration is incomplete or the
   JSON has drifted since. Must be reconciled before SQLite becomes authoritative.

---

## 5. Existing safety mechanisms

This is the strongest part of the codebase and the transformation must not weaken it.

**Verified working:**

- **Enforced stage machine** — `Draft → Reviewed → Approved → Queued → Confirmed → Sent`.
  `transition_draft` rejects illegal transitions.
- **Approval does not send.** Confirmed by reading `/approve` and `/approve_all` — neither
  touches SMTP. Only `POST /send-jobs/<job_id>/confirm` reaches `send_email_fn`.
- **Idempotency** — `send_jobs.idempotency_key = stable_id("send", draft_id, draft.version)`
  with a uniqueness check; a repeat `queue_draft` returns the existing job, `created=False`.
- **Atomic claim** — `UPDATE ... WHERE id=? AND status='confirmed'` guarded by a `rowcount == 1`
  check inside an IMMEDIATE transaction. A double-claim is impossible.
- **Suppression checked inside the claiming transaction** — not before it, not after. If the
  recipient is suppressed the job is cancelled atomically and `claim_send` returns `None`.
- **Follow-ups hard-disabled** — `POST /followups` returns HTTP 409 unconditionally
  (`winston_app.py:1859`), and `followup_scheduler()` returns immediately unless
  `automatic_followups_enabled` is set. That setting is currently false.
- **No secrets in git history.** Verified across all refs: `.env` was never committed and no
  API-key patterns appear in any blob.

**Gaps that must be closed:**

| Gap | Detail |
|---|---|
| **PII committed to git** | `followups.json` (122 unique business emails **plus full email bodies**) and `emailed.json` (139 addresses) are tracked in the repo and pushed to `github.com/Kevin-Edwards57/winston`. |
| **PII one `git add .` from exposure** | `contacts.json` (613 emails) and `social_leads.json` are untracked but **not** gitignored. |
| **Legacy follow-up path bypasses everything** | `check_and_send_followups()` sends straight from JSON with no suppression, idempotency, or claim lock. 131 follow-ups already went out this way. |
| **No dry-run mode** | Cannot run end-to-end tests against the real send path. |
| **No rate limiting** | Only a `time.sleep(10)` inside the disabled follow-up loop. |
| **No bounce or unsubscribe handling** | `suppressions` table exists but is empty and nothing writes to it. |
| **No campaign-level send caps** | Required by §26. |

---

## 6. Migration risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Duplicate sends during JSON→SQLite cutover** — both stores briefly authoritative | Medium | **Severe** (reputation, deliverability, legal) | Seed `suppressions` from all 139 `sent_messages` + `emailed.json` *before* cutover. Make suppression the hard gate. |
| **183-record drift** between `contacts.json` and `contacts` | High | Medium | Reconcile and re-run migration; diff before/after. |
| **`state["pending"]` loss on restart** | High | Medium | Persist the queue to `drafts` at creation, not at approval. |
| **Re-enabling follow-ups against 122 already-contacted businesses** | Medium | **Severe** | Keep disabled. Route follow-ups through the same state machine before ever re-enabling. |
| **Gmail SMTP reputation** — no warmup, caps, or bounce handling | Medium | High | Rate limits + bounce/unsubscribe handling before volume increases. |
| **Schema expansion breaking the monolith** | High | Medium | Additive migrations only; never drop or rename existing columns. |
| **Ollama's 20.6% success rate silently degrading new engines** | **High** | High | Fix the generation layer before building anything on top of it. |
| **PII already in git history** | Certain | High | Rewrite history or rotate the repo; decide before the next push. |

---

## 7. Recommended architecture

Keep the working parts. Add a layered intelligence core between data and outreach.

```
                            ┌─────────────────────────────────┐
                            │      winston/  (package)        │
                            └─────────────────────────────────┘

  DISCOVERY          MEMORY              INTELLIGENCE           COMMERCIAL
  ─────────          ──────              ────────────           ──────────
  discovery/         memory/             intel/                 commercial/
   places.py          prospects.py        digital_audit.py       catalog.py      (YardLink services)
   scraper.py  ──►    timeline.py   ──►   signals.py       ──►   offers.py       (offer matching)
   dedupe.py          signals.py          weaknesses.py          pricing.py      (pricing engine)
                      provenance.py       scoring.py             discounts.py
                            │                   │                     │
                            └───────────────────┴─────────────────────┘
                                                │
                       ┌────────────────────────┼────────────────────────┐
                       ▼                        ▼                        ▼
                  ml/                      outreach/                 ops/
                   features.py              ghostwriter.py            router.py   (AI routing)
                   models.py                personality.py            cache.py    (AI cache)
                   expected_value.py        experiments.py            metrics.py
                   learning_loop.py         send.py  ◄── EXISTING state machine, unchanged
                                                          + dry-run, rate limits, suppression
```

**Non-negotiable invariants:**

1. `send.py` keeps the existing `Draft → … → Sent` machine. New code calls it; nothing bypasses it.
2. Every score, price, and offer carries a **structured explanation** — no bare numbers.
3. Every stored field carries **source, timestamp, confidence, verification status**.
4. Protected characteristics are **never** stored as features, never inferred, never priced on.
   Enforced by a hard feature-allowlist in `ml/features.py` plus a test that fails the build
   if a disallowed field reaches the model.
5. The AI router asks in order: *deterministic Python → cache → local model → free tier → Claude*.

**Schema additions** (all additive):

`prospects` · `prospect_contacts` · `digital_footprint` · `business_signals` ·
`weaknesses` (evidence, confidence, severity, discovered_at, verified_at, yardlink_solution) ·
`prospect_timeline` · `opportunity_scores` (with `explanation` JSON) · `service_catalog` ·
`offer_recommendations` · `price_recommendations` (list/target/floor/margin/reason) ·
`campaigns` · **`outcomes`** (reply, positive_reply, meeting, proposal, close, lost_reason, revenue) ·
`experiments` · `ai_cache` (prompt_hash, response, ttl, version).

---

## 8. Phased implementation plan

Each phase ships independently, keeps tests green, and leaves the system working.

| Phase | Deliverable | Gate to pass |
|---|---|---|
| **0. Containment** | Purge PII from git; gitignore all data files; seed `suppressions` from 139 sends + `emailed.json`; add dry-run mode; delete `FIRECRAWL_KEY`; fix README stats. | No PII tracked; suppression blocks all 139 prior recipients. |
| **1. Outcome capture** | `outcomes` + `campaigns` tables; IMAP reply detection; bounce/unsubscribe → `suppressions`; backfill the 139 historical sends. | Every send has an outcome row. Replies auto-classified. |
| **2. Generation reliability** | Fix the 20.6% Ollama failure rate; get Gemini actually serving; add `ai_cache` with hashes + TTLs; retry/fallback. | ≥90% generation success. Cache hit rate reported. |
| **3. SQLite authoritative** | Reconcile the 183-record drift; persist `state["pending"]` to `drafts` at creation; retire JSON writes to read-only compatibility. | Restart loses nothing. Single source of truth. |
| **4. Prospect memory** | `prospects`, `prospect_timeline`, `provenance`. Migrate 1,172 contacts. Backfill timeline from 1,656 events. | "What do we know about X?" answerable. |
| **5. Digital audit** | Extend the scraper: tech detection, mobile, performance, lead capture, booking, job postings → `weaknesses` with evidence + confidence. | Weaknesses cite evidence. Never invented. |
| **6. Scoring** | Fit / Pain / Buying-signal / Digital-maturity, each with a stored explanation. | Every score explains itself. |
| **7. Service catalog + offer matching** | YardLink capability catalog; problem→capability ranking. | Never defaults to "build a website." |
| **8. Pricing engine** | List/target/floor/premium + margin floor + discount rules. Feature allowlist enforced by test. | Protected-characteristic test fails the build if violated. |
| **9. Expected value ranking** | `P(close) × deal value`. Prioritize by EV. | Prospect list ranked by EV, not activity. |
| **10. ML foundation** | Feature pipeline; interpretable models first (logistic regression → gradient boosting only if it beats it). | Must beat the heuristic baseline or it does not ship. |
| **11. Experimentation** | A/B on offer, price, subject, framing. Measured on revenue, not opens. | Experiments read out on revenue. |
| **12. Watchtower** | Tiered refresh by prospect value; change detection → sales triggers. | Score changes generate alerts. |
| **13. Command center + personality** | Prospect card, Money Mode, modes, "Why this?" explanations. | Every recommendation traceable to evidence. |
| **14. Revenue optimization** | Revenue per $1 AI spend; close the learning loop. | Loop measurably improves prediction. |

---

## 9. Highest-ROI first milestone

**Phase 0 + Phase 1, shipped together: Containment and Outcome Capture.**

Rationale, in order of force:

1. **Everything downstream is label-starved.** The mission's centerpiece — expected revenue,
   the learning loop, ML ranking, experimentation — requires knowing which emails produced
   replies, meetings, and money. Right now that data does not exist for a single one of the
   139 sends. Every day of outreach without outcome capture is training data destroyed at the
   moment it is created. This is the one problem that gets strictly worse with time.
2. **The PII exposure is live.** 122 business emails and their full message bodies are sitting
   in a git repo right now. That is a real disclosure with real exposure under NY SHIELD, and
   it costs an afternoon to fix.
3. **Suppression is empty while 139 people have already been contacted.** The suppression
   table is the single mechanism protecting against re-contacting them. It has zero rows.
4. **It is cheap.** Roughly 3–5 days. No ML, no new AI spend, no rewrite.

Concretely: purge and ignore the PII, seed suppression from every historical recipient, add
dry-run mode, then build `outcomes` + IMAP reply detection and backfill the 139 sends.

The moment that lands, every subsequent email becomes a labeled training example — and
Phases 6–11 become buildable instead of theoretical.

---

## 10. Exact files that need modification

### Phase 0 — Containment

| File | Action |
|---|---|
| `.gitignore` | Add `contacts.json`, `social_leads.json`, `emailed.json`, `followups.json`, `stats.json`, `*.png`, `winston.db*` |
| `followups.json`, `emailed.json` | **`git rm --cached`**, then purge from history (`git filter-repo`) — 122 emails + bodies exposed |
| `winston_dashboard.png` | Untrack (5.9MB; may render real prospect emails) |
| `winston/repository.py` | New `seed_suppressions_from_sent()`; `suppress()` reason taxonomy |
| `winston_app.py:645` | `send_email_fn` — add `WINSTON_DRY_RUN` guard + rate limiting |
| `winston_app.py:665` | `check_and_send_followups` — route through the state machine or delete outright |
| `.env`, `.env.example` | Remove orphaned `FIRECRAWL_KEY` |
| `README.md` | Fix stats table (1,749 / 139 / 131, not 1,149 / 131 / 1) |
| `tests/test_app_safety.py` | Tests: dry-run blocks SMTP; suppression blocks all 139 prior recipients |

### Phase 1 — Outcome capture

| File | Action |
|---|---|
| `winston/repository.py:69` | `initialize()` — add `outcomes`, `campaigns` tables + indexes |
| `winston/outcomes.py` | **New** — outcome recording, reply classification, revenue attribution |
| `winston/inbox.py` | **New** — IMAP reply/bounce/unsubscribe detection → `suppressions` |
| `winston/migration.py` | Backfill outcome rows for the 139 `sent_messages` |
| `winston_app.py:1810` | `confirm_send` — write an `outcomes` row on every send |
| `winston_app.py:1650` | `dashboard_data` — surface reply/meeting/close counts |
| `tests/test_outcomes.py` | **New** — classification, backfill, suppression-on-bounce |

### Deliberately untouched in Phases 0–1

`winston/repository.py:324–400` (queue/confirm/claim/complete) — the state machine is correct.
`winston/ai.py:152` routing order — correct; caching wraps it in Phase 2, no rewrite.
`winston_app.py:402–530` (scraper) — works and costs nothing; extended in Phase 5.

---

## Summary

Winston is in better shape than a 1,894-line monolith suggests. The safety architecture is
genuinely well engineered — atomic claims, transaction-scoped suppression, idempotency keys,
and a real state machine, all covered by 21 passing tests. That foundation is worth building on.

Three things stand between it and the mission:

1. **Zero outcome data.** The learning loop has nothing to learn from.
2. **A 20.6% generation success rate.** Every engine built on top inherits it.
3. **An in-memory review queue.** Persistent prospect memory cannot sit on RAM.

None require a rewrite. All are fixable incrementally, in the order above.
