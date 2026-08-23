# Winston architecture

Winston is being incrementally separated from the legacy single-file Flask application. The current application remains in `winston_app.py`; new persistence and workflow rules live under `winston/`.

## Phase 2 data flow

1. Legacy JSON files remain the compatibility source for the current scanner UI.
2. `winston.migration` copies every source file to a timestamped backup.
3. Every source row is stored unchanged in `legacy_import_records`, keyed by source, index, and content hash.
4. Canonical contacts are deduplicated by normalized email and Google Place ID.
5. Drafts follow the enforced state machine: Draft → Reviewed → Approved → Queued → Confirmed → Sent.
6. Transactional send jobs use a unique idempotency key and an atomic claim lock.
7. Suppressions are checked inside the same transaction that claims a send.
8. Structured events are persisted in `activity_events`.

SQLite uses foreign keys, WAL mode, a busy timeout, and immediate transactions for competing workflow writes. JSON writes are temporarily retained for compatibility but now use atomic file replacement.

Automatic follow-ups are disabled. They must remain disabled until reply detection, bounce and unsubscribe handling, campaign state, and suppression checks are implemented and tested.

## Local website scraping

Winston does not use a paid scraping provider. Public pages are fetched locally with
`requests`, capped at 2 MB, and accepted only when the response is HTML. The standard-library HTML parser
discovers likely contact/about links on the same domain and extracts mailto addresses,
phone numbers, and explicit social-profile URLs. JavaScript-only or bot-protected pages
are skipped rather than routed to a paid fallback.

## Zero-discovery-cost drafting

`POST /draft-existing` accepts a batch size from 1 to 50 and runs in a background
thread. Candidate selection excludes suppressed contacts, sent recipients, and contacts
with an active draft. Drafting uses the configured zero-cost AI provider and places
successful results into the same human review queue. It never calls Google Places and
never sends email.

## AI provider routing

`AIService` routes generation through configured providers in this order:

1. Gemini using the configured free-tier model
2. Local Ollama
3. Claude only when both `WINSTON_ZERO_COST_MODE=false` and `WINSTON_ENABLE_CLAUDE=true`

Every attempted provider call records purpose, model, success, latency, token usage,
error summary, and estimated cost. Zero-cost mode skips paid providers before any API
call is made. It never silently promotes a failed free request to Claude.

---

## Phase 0 + 1 (2026-08-23)

### One send path

The legacy follow-up sender was **deleted**, not disabled. It delivered mail directly
from `followups.json`, bypassing suppression, idempotency, atomic claiming, audit
logging, and human confirmation; 131 follow-ups went out through it. `send_email_fn`
is now reachable from exactly one caller, `confirm_send`, and `tests/test_no_legacy_send.py`
parses the AST to prove it — a second call site or a second SMTP user fails the build.

Three guards sit in front of SMTP:

1. **Suppression backstop** — re-checked in `send_email_fn` even though `claim_send`
   already checks it inside the claiming transaction. Defence in depth against a
   future caller that skips the state machine.
2. **Dry-run** — `WINSTON_DRY_RUN` defaults to **true**. Real delivery is an explicit
   opt-out, so a fresh checkout or a forgotten `.env` cannot mail real people.
3. **Rate limiting** — minimum inter-send interval plus a daily cap.

All 122 previously contacted addresses are suppressed. Historical data shows 139 sends
to 122 unique recipients: 17 duplicate sends had already occurred.

### Commercial event model

`winston/commercial.py` records what outreach produced, along the real funnel:

```
campaign -> message -> message_event -> reply -> meeting -> proposal -> deal -> revenue
```

Two invariants:

- **Nothing is inferred.** A row exists only when something observably happened. The
  backfill of 139 historical sends records that they were sent and nothing more.
- **Absent evidence is unknown, not zero.** `funnel()` returns `None` for reply-derived
  rates until an inbox scan has actually run. A 0% reply rate and "nobody ever looked"
  are different claims, and conflating them is how a system concludes a campaign failed
  when it was merely unmeasured.

Revenue is event-sourced in `revenue_events`, so a deal's realised value is a `SUM` of
deposits, milestones, and recurring payments rather than a field someone must remember
to update. A lost deal cannot be closed without a `loss_reason_code`.

### Inbox intelligence

`winston/inbox.py` classifies inbound mail cheapest-first: RFC 3834 headers, then
RFC 3463 delivery-status codes, then keywords, and only then a model call. Hard bounces
(5.x.x), complaints, and unsubscribe requests suppress the address immediately; soft
bounces (4.x.x) are transient and deliberately do not. Bounce handling resolves the
*original* recipient from the DSN rather than the postmaster that sent it.

### Persistent review queue

Drafts are written to SQLite at generation time, not at approval. `state["pending"]` is
now a cache of the `drafts` table, and `rehydrate_pending_queue()` rebuilds it at startup.
Previously an unreviewed queue existed only in memory, which is why `drafts` held 0 rows
against 139 historical sends.

### Contact identity

A Google Place ID identifies a business; an email address does not. Shared platform
inboxes and addresses scraped out of embedded font licences legitimately appear on many
unrelated businesses. Treating email as identity had two consequences:

- **Data loss** — distinct businesses merged into their first-seen namesake.
- **Data corruption** — the merge path *overwrote rows in place*, leaving 37 contacts
  whose stored ID derived from one business while their contents described another.

An email match is now an identity match only when the matched row has no Place ID of its
own. Migration is idempotent as a result: it previously oscillated, inserting businesses
on one pass and deleting them on the next. Reconciliation recovered 224 businesses
(1,172 → 1,396) with zero duplicate Place IDs, stable across repeated runs.

### AI reliability

293 of 369 historical generation calls failed with a single error. Root cause: `qwen3:8b`
is a hybrid reasoning model, and without `think` disabled it spends the whole
`num_predict` budget inside `<think>` and returns an empty `response`. At the 200-token
budget used for DMs it never escaped — `instagram_dm` and `facebook_dm` recorded 0
successes across 120 attempts. Verified against a local Ollama: think omitted 0/6,
`think=False` 6/6. The flag is now a named constant locked by test.

Gemini's zero usage had a different cause: `GEMINI_API_KEY` was empty, so `available()`
returned false and the provider was skipped in silence while sitting first in the routing
order. `AIService.misconfigured()` now surfaces that rather than letting it vanish, and
`repository.provider_health()` reports per-provider success rate, latency, cost, and top
error so an unreliable inference layer cannot degrade everything above it unnoticed.
Retries use exponential backoff and never escalate a free provider to a paid one.
