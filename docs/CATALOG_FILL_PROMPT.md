# Catalogue fill-in prompt

Copy everything inside the code block below into GPT (or any assistant). It will
interview you about YardLink's products and services and return JSON that posts
straight into Winston.

**Why the exact wording matters:** Winston's fit engine substring-matches your
`problems_solved` strings against its own internal problem vocabulary. If the answer
says *"legacy web presence modernization"* it matches nothing and the product will never
be recommended. It has to say *"outdated website"*. The prompt below pins that vocabulary
down, which is most of why it is long.

---

```text
You are helping me fill in a product catalogue for Winston, an internal sales-intelligence
system built for my company, YardLink Studio. Winston researches small businesses in NYC
and Long Island, detects problems with their digital presence, and recommends what
YardLink should offer them.

Your job: interview me about my products and services, then output strict JSON.

Ask me about ONE item at a time. Do not guess or invent capabilities, pricing, or
status — if I do not know something, leave the field empty or null. An empty field is
fine. A fabricated one is not, because Winston will use these strings to make claims in
real sales emails to real businesses.

=====================================================================
THE SCHEMA
=====================================================================

Each catalogue item is a JSON object with these fields:

  slug              string   lowercase-hyphenated id, e.g. "wedlink"
  name              string   display name, e.g. "WedLink"
  kind              enum     PRODUCT | SERVICE | PORTFOLIO | INTERNAL_TOOL | FUTURE
  status            enum     see the status table below
  description       string   1-2 plain sentences on what it actually does
  ideal_customer    string   who it is for
  industries        [string] MUST use the industry vocabulary below
  problems_solved   [string] MUST use the problem vocabulary below
  capabilities      [string] concrete features it actually has today
  limitations       [string] what it does NOT do — be honest, this prevents overselling
  integrations      [string] third-party systems it connects to
  deployment_model  string   e.g. "hosted SaaS", "installed per client", "custom build"
  pricing_model     string   e.g. "fixed scope", "monthly subscription", "hourly"
  price_min_usd     number|null   low end of a typical engagement
  price_max_usd     number|null   high end
  recurring_usd     number|null   monthly recurring, if any
  effort_hours_min  number|null   typical build hours, low
  effort_hours_max  number|null   typical build hours, high
  notes             string   anything else worth recording

=====================================================================
KIND AND STATUS — THE RULES WINSTON ENFORCES
=====================================================================

KIND
  PRODUCT        Something a customer can buy or license
  SERVICE        Work YardLink performs for a fee
  PORTFOLIO      Proof we can build something. NEVER sold.
  INTERNAL_TOOL  Runs YardLink itself. May be cited as proof. NEVER sold.
  FUTURE         Planned, not deliverable yet. NEVER sold.

STATUS
  ACTIVE_PRODUCT   shipping, customers can buy today        <- sellable
  BETA_PRODUCT     working, limited availability            <- sellable
  SERVICE          an offered service                       <- sellable
  COMING_SOON      announced, not deliverable
  PORTFOLIO_ONLY   evidence only
  INTERNAL_TOOL    internal use
  EXPERIMENTAL     prototype / unproven
  ARCHIVED         retired

HARD RULES — Winston rejects the item if these are broken:
  - kind PORTFOLIO, INTERNAL_TOOL, or FUTURE can NEVER have status
    ACTIVE_PRODUCT, BETA_PRODUCT, or SERVICE.
  - kind SERVICE must have status SERVICE, EXPERIMENTAL, COMING_SOON, or ARCHIVED.
  - price_min_usd must not exceed price_max_usd. Neither may be negative.

Push back on me if I try to mark a portfolio project as a sellable product. That is the
single most important distinction here: a project proving we CAN build something is not
the same as a product a customer can BUY.

=====================================================================
PROBLEM VOCABULARY — USE THESE EXACT STRINGS
=====================================================================

`problems_solved` entries must be drawn from this list, verbatim. Winston derives these
same problems from real website analysis and matches them against your strings. Anything
outside this list will silently never match.

  "no website"              business has no website at all
  "outdated website"        site looks unmaintained (stale copyright, old design)
  "not mobile friendly"     site does not adapt to phone screens
  "no ssl"                  site served over insecure HTTP
  "no lead capture"         no enquiry form; visitors cannot leave details
  "no online booking"       appointment business with no booking system
  "no online ordering"      food business with no ordering system
  "no measurement"          no analytics; marketing spend is unmeasurable
  "weak seo basics"         missing title, meta description, or H1

You may ALSO add extra free-text problems beyond this list for human readers — they just
will not drive automated matching. Put the vocabulary strings FIRST in the array.

=====================================================================
INDUSTRY VOCABULARY — USE THESE EXACT STRINGS
=====================================================================

`industries` must use these values. They are the actual business categories in Winston's
database of 1,396 prospects, with prospect counts:

  barbershop (130)          restaurant (128)         jamaican restaurant (125)
  hair salon (102)          nail salon (80)          auto repair (80)
  cleaning service (78)     dentist (67)             photographer (66)
  caribbean restaurant (66) catering (65)            gym (60)
  florist (60)              daycare (59)             accountant (59)
  plumber (46)              real estate agency (44)  electrician (41)
  tutoring (39)

If a product targets an industry not in this list (e.g. wedding venues, security
companies), say so explicitly in `notes` — those prospects are not in Winston's database
yet and I will need to run discovery for them.

=====================================================================
WHAT I NEED FILLED IN
=====================================================================

These already exist in Winston with names only. All are currently UNVERIFIED and Winston
refuses to recommend any of them:

  yardlink-eats   PRODUCT        — I said it exists. You need to ask me what it does.
  wedlink         PRODUCT        — referenced for wedding venues. Ask me.
  guardlink       PRODUCT        — referenced for security-company workforce management. Ask me.
  otonia          PORTFOLIO      — proof of consumer mobile development and polished UX
  susan           PORTFOLIO      — job-application automation project
  winston         INTERNAL_TOOL  — this system; proof of AI automation and data engineering

I also need SERVICE entries created from scratch. Ask me which of these YardLink actually
offers — do not assume all of them:

  web development, website redesign, landing pages, booking systems, ordering systems,
  mobile app development, AI chatbots, AI automation, marketing automation, CRM setup,
  custom software, API/backend development, data engineering

=====================================================================
HOW TO RUN THIS
=====================================================================

1. Start by asking me about ONE product. Ask what problem it solves, who it is for, what
   it actually does TODAY (not planned), what it does not do, and whether anyone is
   currently paying for it.
2. From my answers, map to the vocabularies above. If my answer does not map cleanly to a
   problem-vocabulary string, tell me which one is closest and confirm.
3. Confirm the status honestly. If nobody has ever paid for it and it is half-built, that
   is EXPERIMENTAL, not ACTIVE_PRODUCT.
4. Move to the next item.
5. When we have covered everything, output ONE JSON array containing every item.

Output format — a single fenced json block, nothing else:

[
  {
    "slug": "web-development",
    "name": "Web Development",
    "kind": "SERVICE",
    "status": "SERVICE",
    "description": "...",
    "ideal_customer": "...",
    "industries": ["restaurant", "barbershop"],
    "problems_solved": ["outdated website", "not mobile friendly", "no lead capture"],
    "capabilities": ["..."],
    "limitations": ["..."],
    "integrations": [],
    "deployment_model": "custom build",
    "pricing_model": "fixed scope",
    "price_min_usd": 900,
    "price_max_usd": 2500,
    "recurring_usd": null,
    "effort_hours_min": 25,
    "effort_hours_max": 60,
    "notes": ""
  }
]

Also output a second JSON array of proof links, connecting each sellable offer to the
portfolio or internal work that demonstrates we can deliver it:

[
  {"from": "web-development", "to": "otonia", "relation": "proves",
   "note": "consumer UX craft"}
]

Valid relations: proves, pairs_with, upsell, cross_sell, replaces.

Begin by asking me about YardLink Eats.
```

---

## Loading the result into Winston

Save GPT's first array as `catalog.json` and the second as `links.json`, then:

```bash
python3 -c "
import json, urllib.request
def post(path, body):
    req = urllib.request.Request('http://localhost:5000'+path, method='POST',
        data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req))
for entry in json.load(open('catalog.json')):
    r = post('/catalog', entry); print(entry['slug'], '->', 'ok' if r.get('success') else r)
for link in json.load(open('links.json')):
    post('/catalog/link', link)
print('loaded — now verify each entry you are confident in')
"
```

Entries load as **unverified**, so Winston still will not recommend them. Verify the ones
you stand behind:

```bash
curl -X POST http://localhost:5000/catalog/wedlink/verify
```

Then confirm Winston can actually sell:

```bash
curl -s http://localhost:5000/catalog | python3 -m json.tool | grep -A5 readiness
```

`can_recommend` flips to `true` once at least one entry is both sellable-status and
verified.

**Note:** editing an entry's claims after verification automatically revokes it, because
what was checked is no longer what is being sold. Re-verify after any material edit.
