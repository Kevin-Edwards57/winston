# Speedrun baseline

Captured before the Phase 1 to 15 build-out, so later claims of progress can be checked
against a real starting point rather than a remembered one.

**Date:** 2026-08-24
**HEAD:** `aeb0811` on `main`
**Working tree:** clean
**Tests:** 408 passing, 28 seconds

## Correction to an earlier reading

The suite was previously reported at 196 seconds with `test_writer_guardian` alone taking
87. Re-measured per module it is 6 seconds, and the whole suite is 28. The earlier figure
was Ollama cold-loading `qwen3:8b` on first call, not a structural problem, so no test
mocking is required.

## Database

Integrity check `ok`. WAL mode.

| | |
|---|---|
| contacts | 1,396 |
| researched | 59 |
| stored signals | 889 |
| drafts | 6 |
| send jobs | 0 |
| messages | 139 |
| messages excluded from learning | **139** |
| suppressions | 122 |
| replies | 0 |
| deals | 0 |

Every message in the ledger is a legacy backfill. Nothing has been sent through the
production pipeline.

## Configuration

| | |
|---|---|
| `WINSTON_DRY_RUN` | **true** |
| Daily send cap | 5 |
| HTTP routes | 56 |
| Offerable services | `booking-systems`, `website-service` |
| Quotable services | `booking-systems`, `website-service` |
| ML status | `INSUFFICIENT_DATA` |

## Modules

6,686 lines across 17 modules: `ai` `catalog` `commercial` `costs` `fit` `fulfillment`
`guardian` `inbox` `migration` `pipeline` `pricing` `providers` `ratecard` `repository`
`signals` `writer`.

## Doctor

16 PASS, 2 WARN, 2 FAIL.

**Failing, and it blocks a real campaign:**

- `SMTP authentication` — the Gmail app password is rejected. Winston cannot send.
- `IMAP authentication` — same credential. Winston cannot detect replies.

**Warnings:**

- `Gemini free tier` — `GEMINI_API_KEY` empty, so the free tier is never attempted.
- `Winston responding` — the server was not running during the check.

The mail failures are operator-actionable only: `./winstonctl mail` walks through
replacing the credential. No amount of code fixes a revoked password.

## What is genuinely complete

Research and signal collection. Assertability, with confirmed, inferred and unknown as
first-class states. Catalogue with product, service, portfolio and internal separation
plus audience gating. Fit engine. Pricing with provenance and a protected-characteristic
allowlist enforced by test. Writer. Guardian, including a final send-boundary review
bound to a SHA-256 digest of the exact body. Send state machine with suppression,
idempotency, atomic claiming and dry-run. Provider routing with budget gating. Local
macOS deployment through `winstonctl` and launchd.

## What is boundary or unverified

**Inbox** is implemented and unit tested but has never authenticated against a real
mailbox, because the credential is dead.

**Website Builder** has an integration boundary only. The Builder exposes no HTTP API;
`app/` contains no route handlers.

**Learner and ML** are gated at `INSUFFICIENT_DATA` with zero eligible messages.

## Known blockers, in order

1. Gmail app password (operator action; blocks both sending and reply tracking)
2. No production sends, so no outcome data, so no learning
3. Website Builder has no API surface to integrate against
