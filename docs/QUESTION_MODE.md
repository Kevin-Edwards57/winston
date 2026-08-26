# Question mode

## Why it exists

Assertability closed a real hole. A problem derived from absence was becoming a sales
claim, and 11 of 13 "no analytics" findings turned out to be wrong: a Shopify store that
measures by construction, a florist whose own URL carried campaign tracking. Inferred
problems were excluded from outreach entirely.

That fix was correct and slightly too blunt. Across 59 researched prospects there are 19
inferred booking gaps and 15 inferred ordering gaps. Winston cannot say *"you have no
online booking"*, because it does not know that. It can honestly ask *"do customers book
with you online?"* A question makes no claim, so it cannot be a false one.

## The three states, and what each permits

| State | Meaning | Commercial action |
|---|---|---|
| `confirmed` | Observed directly in the markup | May be stated. Goes to the Writer. |
| `inferred` | Derived from absence | May be **asked about**. Goes to the QuestionWriter. |
| `unknown` | Insufficient research | Neither. |

## An investigation needs somewhere to go

An inferred problem alone is not enough. Asking a salon about booking is only worth
anyone's time if YardLink can build one, so an investigation is **actionable** only when a
verified, offerable service stands behind it. Unactionable investigations are recorded and
shown, but produce no draft.

On the 59 researched prospects: **27 actionable**, 35 unactionable.

## What Guardian enforces

Question mode is the obvious place for the discipline to leak, so it is held to a
stricter standard than normal outreach, not a looser one.

A question-mode draft is rejected if it:

- contains no question at all
- states the business lacks anything, in any phrasing
- has no investigation with a verified offer behind it

The assertion patterns catch the drift case specifically. *"I noticed you don't offer
online booking, do you?"* is an assertion wearing a question mark, and it fails.

Everything from normal mode still applies: em dashes, guarantees, invented statistics,
unverified services, consumer products pitched as B2B, suppression, duplicates.

The normal path is unchanged. A claim-based draft with no observed evidence still fails
on `no_evidence`.

## Measured effect

Same 50-prospect cohort, before and after:

| | Prospects |
|---|---|
| Claim-based outreach, from confirmed evidence | 26 |
| Question-only, from actionable inference | **8** |
| Nothing to say | 9 |
| **Addressable before** | **26 (60%)** |
| **Addressable after** | **34 (79%)** |

Question topics: online booking (6), site age (2).

The 8 added prospects receive a question, not a claim. No inference became a fact.

## A real generated example

Estelle Hair Studio, a hair salon. Booking absence inferred at 0.55 confidence, evidence
"no booking platform found on the pages researched", `booking-systems` verified.

> **Quick question about online booking**
>
> I looked at Estelle Hair Studio's site but couldn't tell if customers book online or
> call. Do customers book with you online or do they call? If the answer is no, YardLink
> Studio offers Booking Systems.

Guardian PASS. Contains a question, asserts nothing, no em dash.

## Routes

| Route | Purpose |
|---|---|
| `GET /investigations` | How much inferred evidence exists and how much is actionable |
| `GET /prospects/<id>/investigations` | Investigations for one prospect |
| `POST /prospects/<id>/question` | Generate a question draft and review it |

Question drafts are not queued for sending. They are generated and reviewed only.

## The line this holds

Question mode is an investigation mechanism, not a route around evidence. The moment a
question implies its own answer, it has become a claim built on inference, which is the
failure assertability exists to prevent.
