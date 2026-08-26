# Agent contracts

## Why this is a module and not a document

A list of agent names in a README drifts from the code it claims. The code changes, the
list does not, and eventually a dashboard reports eleven autonomous agents when there are
four functions and a cron job.

Two decisions prevent that here.

**Implementations are resolved, not described.** Every contract names a real module
attribute and `verify_implementations()` imports it. A role claiming to be operational
while pointing at code that does not exist is a test failure.

**Status is derived, not declared.** Whether Inbox works is a question about whether a
mailbox scan has ever succeeded. Whether Learner can say anything is a question about how
many eligible outcomes exist. Both are computed from the database at call time, so the
Agent Center cannot claim a capability Winston does not have.

## The roles

| Role | Kind | Status | Implementation |
|---|---|---|---|
| Scout | deterministic | active | `winston_app.google_places_search` |
| Researcher | deterministic | active | `winston.signals.research_contact` |
| Auditor | deterministic | active | `winston.fit.derive_problems` |
| Strategist | deterministic | active | `winston.fit.FitEngine` |
| Fit Engine | deterministic | active | `winston.fit.FitEngine` |
| Pricer | deterministic | active | `winston.pricing.PricingEngine` |
| Writer | hybrid | active | `winston.writer.Writer` |
| Question Writer | hybrid | active | `winston.questions.QuestionWriter` |
| Guardian | deterministic | active | `winston.guardian.Guardian` |
| Inbox | hybrid | **blocked_external** | `winston.inbox.InboxScanner` |
| Negotiator | hybrid | **planned** | does not resolve |
| Learner | deterministic | **active_with_insufficient_data** | `winston.commercial.CommercialLedger` |

**10 of 12 operational. 8 are deterministic functions**, which the registry states plainly
rather than dressing up as autonomy. Calling `derive_problems` an agent would be theatre;
it is a pure function over stored signals, and that is a strength.

ML is not listed. It is a capability state, `insufficient_data`, not a role.

## Derived statuses, and what would change them

| Role | Current | Becomes active when |
|---|---|---|
| Inbox | `blocked_external` | a mailbox scan succeeds and records `inbox_last_scanned_at` |
| Negotiator | `planned` | real replies exist to build against |
| Learner | `active_with_insufficient_data` | at least one production message becomes learner-eligible |

Negotiator is the only contract whose implementation does not resolve, and that is
correct: there is nothing to build it against. `verify_implementations()` permits a
PLANNED role to be missing and fails only when an operational role is.

## What each role refuses

Refusals are part of the contract, because a role that cannot refuse anything is not a
safeguard.

- **Researcher** records unreachable rather than guessing at a site it could not fetch.
- **Auditor** will not assert a problem derived from absence.
- **Strategist** will not route an inferred problem to a claim.
- **Fit Engine** will not recommend an unverified service or offer a consumer product to
  a business.
- **Pricer** refuses without a rate card, rejects protected characteristics before any
  arithmetic runs, and refuses when the configured price is below delivery cost.
- **Writer** states only what the brief contains and declines when no verified offer fits.
- **Question Writer** will not assert anything the business might not lack.
- **Guardian** refuses em dashes, unsupported claims, unobserved problems, unverified
  entries, portfolio pitched as product, protected-characteristic pricing, suppressed
  recipients, duplicates, and assertion drift in question mode.

## Separation that the tests enforce

Guardian does not depend on either writer, so it can refuse whatever produced the text.
The two writers are distinct roles with distinct output modes, so a question can never be
mistaken for a claim. Proof ranking has exactly one definition, in `winston.fit`, which
both writers import rather than reimplement; a test counts the definitions across the
package so a second one cannot appear quietly.

## Execution history

`agent_executions` records real runs only. Researcher and Writer record on every
invocation, Guardian on every review. A role with no history reports `has_history: false`
and a `success_rate` of `None`, because no runs is a different statement from a zero
success rate.

Nothing writes a synthetic execution to make the dashboard look populated.

## Personality

Each role has an operating style, and it is exactly that. Scout is curious, Auditor is
skeptical, Guardian is uncompromising. Personality never influences a deterministic rule;
it shapes tone in the prompts that have prompts and is otherwise documentation.

## Route

`GET /agents` returns the whole registry: contracts, derived status with its reason,
refusals, failure states, and measured execution history.
