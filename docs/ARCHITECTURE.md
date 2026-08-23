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
