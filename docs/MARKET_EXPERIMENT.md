# 50-prospect intelligence experiment

Run 2026-08-23 against live prospect websites. Research and assessment only; dry-run was
verified on before starting and no message was sent. Reproducible from the same database.

## Why this was run

Winston's architecture had been validated on single prospects. One correct decision is a
proof of concept; fifty is a signal. The question was not whether the pipeline works but
**what it actually sees in this market**.

## Method

50 unresearched prospects, four per industry across 13 industries, each with a website
and an email address and not suppressed. Each ran the real pipeline: research, signal
extraction, problem derivation, offer matching, proof ranking, pricing.

## Funnel

| Stage | Count | Rate |
|---|---|---|
| Sampled | 50 | |
| Reachable | 43 | 86% |
| At least one observed problem | 39 | 78% |
| Verified offer matched | 28 | 56% |

## The finding that mattered

**Winston's most common detected problem was one it could not sell against.**

`no_online_booking` appeared in 42% of reachable prospects, more than any other problem,
and `booking-systems` was not enabled. Across 43 businesses, **36 observed problems had
no enabled service**.

Nine prospects had real, evidenced problems and Winston had nothing to offer them. That
is a catalogue configuration gap, not a detection failure, and it was invisible until
the pipeline ran at volume.

## The second finding

**The "outdated website" pitch is largely dead in this market.**

100% of reachable sites were mobile-responsive. 98% had SSL. The mental model of small
businesses running broken decade-old sites does not match what is out there. Only 7 of
43 showed `outdated_website`.

What these businesses actually lack is *transactional capability*: booking, lead capture,
measurement.

## Signal prevalence, of 43 reachable

| Signal | Positive | Rate |
|---|---|---|
| mobile responsive | 43 | 100% |
| SSL | 42 | 98% |
| CMS detected | 29 | 67% |
| analytics | 21 | 49% |
| contact form | 18 | 42% |
| online booking | 14 | 33% |
| client-rendered | 13 | 30% |
| online ordering | 4 | 9% |

## Problems detected

| Problem | Count | % of reachable |
|---|---|---|
| `no_online_booking` | 18 | 42% |
| `weak_seo_basics` | 16 | 37% |
| `no_lead_capture` | 15 | 35% |
| `no_measurement` | 13 | 30% |
| `outdated_website` | 7 | 16% |
| `no_online_ordering` | 4 | 9% |
| `no_ssl` | 1 | 2% |

## Intervention: enabling booking-systems

One catalogue change, measured on the same 50 without re-fetching anything.

| | Before | After |
|---|---|---|
| Offer matched | 28 (56%) | **34 (68%)** |
| website-service | 28 | 19 |
| booking-systems | 0 | **15** |

Six prospects moved from "no offer" to a matched, priced offer. `booking-systems`
accounts for 44% of all matches, from a single line of configuration.

Four industries reached 100% coverage: daycare, cleaning service, barbershop, auto repair.

## Where the opportunity is

Ranked by post-intervention match rate. From observed data only.

| Industry | Match rate | Problems each |
|---|---|---|
| daycare | 100% | 2.2 |
| cleaning service | 100% | 1.5 |
| barbershop | 100% | 2.0 |
| auto repair | 100% | 2.0 |
| gym | 75% | 2.0 |
| hair salon | 75% | 3.0 |
| nail salon | 75% | 1.8 |
| restaurant | 50% | 2.0 |
| florist | 25% | 1.2 |
| **accountant** | **25%** | **0.2** |

**Appointment businesses are the segment.** Every industry at or above 75% is
booking-driven.

**Accountants are the weakest tested.** Three of four had zero observed problems.

**Restaurants underperform their prominence in the database.** They are 253 of 1,396
contacts, the largest single block, and matched at 50%. An earlier Caribbean-restaurant
sample showed heavy Toast and Resy adoption, and RUMBA Island had SSL, mobile, Resy,
Toast, analytics and complete SEO with zero derived problems.

The Caribbean restaurant market is strategically central to YardLink Eats. On this
evidence it is not where the website and booking services sell best.

## Refusals

| Count | Reason |
|---|---|
| 7 | Site unreachable |
| 9 | Problems found, no verified service covered them |
| 4 | Healthy site, no problem found |

The four healthy sites are the system working. A business without a problem is not a
prospect, and Winston declined to contact them.

## Cautions on the data

- **13 client-rendered sites** withheld negatives, 10 of them for contact forms. Recorded
  as unknown rather than missing, which is correct but means detection is weaker on
  roughly 30% of sites.
- **18 `no_online_booking` claims sit at 0.55 confidence**, inferred from absence rather
  than observed. This is simultaneously the most common problem and the weakest evidence
  class in the set. Worth manual verification before a batch of outreach leans on it.
- **All 34 prices are operator assumptions.** No closed engagement supports any of them.

## Still uncovered

| Problem | Count | Service that would cover it |
|---|---|---|
| `no_measurement` | 13 | marketing-automation, or an analytics setup service |
| `no_online_ordering` | 4 | ordering-systems |

## What this changes

1. Enable services against observed problems, not assumed ones. One configuration change
   moved offer coverage from 56% to 68%.
2. Target appointment businesses. Six of the top seven industries are booking-driven.
3. Treat the restaurant block as a weaker commercial segment than its size suggests,
   whatever its strategic value to YardLink Eats.
4. Build the priority engine *after* service coverage, not before. Ranking prospects is
   pointless while the bottleneck is having nothing to offer them.

---

# Correction: the measurement detector was manufacturing opportunities

The experiment recommended enabling `marketing-automation` because 13 prospects showed
`no_measurement`. Investigating those 13 before acting found the recommendation was
wrong, and the reason mattered more than the recommendation.

## What was actually wrong

`no_measurement` derived from a single boolean: no analytics tag found in static HTML.
That is not the same claim as "this business does not measure anything".

A static scan cannot see analytics that is:

- loaded by a tag manager after the document
- gated behind a consent banner until the visitor accepts
- supplied by the platform, as on Shopify, Wix, Squarespace or WooCommerce
- injected client-side after render
- one of twenty external scripts, any of which may load it

## The four-state model

| State | Meaning | May be sold against |
|---|---|---|
| `detected` | Analytics found | no |
| `confirmed_absence` | Server-rendered, no platform, no tag manager, no consent gate, few scripts | **yes** |
| `not_detected` | Winston could not see it, and something could be hiding it | **no** |
| `unknown` | Research did not complete | no |

Every finding stores its state, evidence, confidence, and the specific limitations that
applied.

## Re-running the same 43 reachable prospects

| | Before | After |
|---|---|---|
| `no_measurement` findings | 13 | **2** |
| Total problems | 74 | 63 |
| Offer match | 34/50 | **34/50** |

**Eleven of thirteen were false positives**, an 85% error rate on that signal.

| Withdrawn | Why |
|---|---|
| Ditmars Flower Shop | **Google Tag Manager** present |
| Ode à la Rose | Google Tag Manager present |
| Ora La Casa | **Shopify** platform analytics |
| Flowers by Giorgie, Moe's Auto | WooCommerce platform analytics |
| Hills Barber Shop | Square Online platform analytics |
| Great Bear Auto | Consent manager defers loading |
| Planet Maids, Posh Hair, Live In The Cut, Royal Beauty | 15 to 22 external scripts |

Two genuine confirmed absences remain, both daycares.

## The result that matters

**Offer match did not move.** Correcting the detector cost zero commercial
opportunities, because all eleven businesses already matched an offer on other evidence.
`no_measurement` was never the reason to contact any of them; it was noise that would
have justified a worse pitch.

State distribution across 43 reachable prospects: 49% detected, 47% not detected,
**5% confirmed absence**. The original detector reported 30% as having no analytics. The
real, assertable figure is 5%.

## What this changes about Winston

The architecture now separates three things that were previously collapsed:

```
observed      no recognised analytics tag in static HTML
inferred      analytics may be absent, and here is what could be hiding it
commercial    do not pitch unless absence is confirmed
```

A detector that cannot distinguish "I did not see it" from "it is not there" will
generate revenue-shaped noise. The fix was not a better analytics scanner. It was
refusing to let an inference cross into a sales claim.
