# Winston to Website Builder: integration contract

## The finding that shapes everything

**The YardLink Studio Website Builder exposes no HTTP API.**

Verified by inspection: `app/` contains no `route.ts` handlers, and `README.md` states
the core path (intake, generation, preview, export) runs entirely in the browser with no
backend. Cloud pieces activate only when credentials are supplied, and `PRODUCTION.md`
places server-side publishing in Phase 4.

So Winston does not call the Builder. It produces a brief the operator imports, and
records project status a human reports. Building an HTTP client against endpoints that
do not exist would be a fake integration that fails the moment anyone uses it.

## What Winston produces

A handoff shaped to the Builder's real `SiteData` type in `lib/site-generator.ts`. Every
field below exists in that type:

```
businessName  industry   tagline    phone     email      address
story         ownerName  ownerTitle ownerBio  team       accolades
serviceArea   seoTitle   seoDescription       seoKeywords
aiEnabled     aiName     aiTone     menu      assetCount logoLabel
```

Alongside it, context a blank intake form cannot supply:

| Field | Source |
|---|---|
| `observed_problems` | Signals collected during research, with evidence and confidence |
| `known_assets` | Existing site, socials, current CMS |
| `suggested_sections` | Industry-aware section order |
| `sales_context` | Recommended service, fit scores, proof cited |
| `gaps` | Fields Winston could **not** observe |

`gaps` matters most. Winston does not extract menu items, brand colours, logo files, or
photography, so it says which are missing rather than leaving blanks to be discovered
mid-build.

## What Winston does not do

- **Generate the website.** Production stays with the Builder.
- **Poll project status.** There is nothing to poll. Status is operator-reported and
  labelled `status_source: operator-reported`.
- **Invent a published URL.** Marking a project `published` requires the live URL.

## What the Builder would need to expose for a live connection

If the Builder later grows a backend, four endpoints would close the loop. Listed as a
specification, not as something Winston already calls.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/projects` | Accept a `SiteData` brief, return a project id |
| `GET` | `/api/projects/{id}` | Return status, audit score, published URL |
| `GET` | `/api/projects/{id}/audit` | Return the deterministic audit result |
| `POST` | `/api/projects/{id}/publish` | Trigger publishing, return the live URL |

Winston already stores `builder_reference` for the returned id, so adopting these would
mean implementing a client, not reshaping the data model.

### Security considerations for that future API

- A shared secret or signed request; the brief contains business contact details.
- Idempotency on `POST /api/projects` keyed on Winston's project id, so a retry does not
  create a second engagement. Winston already blocks duplicates on its own side.
- No prospect PII beyond what the client has agreed to, since a sold engagement means
  the business is now a customer rather than a researched prospect.

## Lifecycle

```
handoff_ready -> intake_imported -> in_production -> in_review -> published
                                                              -> cancelled
```

Each transition records who reported it and when.

## The commercial loop this closes

```
Winston                                    Website Builder
  discover -> research -> diagnose
  match service -> price -> outreach
  approve -> send -> reply -> close
                              |
                              +--- handoff brief ---> intake -> generate
                                                        -> audit -> export
                                                        -> publish
                              <--- operator reports ---+
  published URL becomes proof for the next sale
```

The last line is the point. A completed project is evidence, and Winston's catalogue
already models proof relationships, so delivered work can be linked as proof for future
prospects in the same industry.
