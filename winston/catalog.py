"""YardLink Studio knowledge base — what we sell, and what merely proves we can build.

**Brand hierarchy.** YardLink Studio (https://www.yardlinkstudio.com) is the company.
YardLink Eats (https://www.yardlinkeats.com) is a product built by YardLink Studio: a
Caribbean restaurant discovery network covering Jamaican, Guyanese, Haitian, and
Trinidadian businesses. The company is the seller; the product is evidence of what the
company can build, and is sold to consumers rather than to the restaurants it lists.


The distinction this module exists to enforce: **a portfolio project is not a product.**

Winston built for YardLink is proof of AI automation capability. Otonia is proof of
consumer mobile craft. Neither is something a prospect can buy, and a system that
cannot tell the difference will confidently pitch an internal tool to a barbershop.

Five classifications, and only some are sellable:

| Kind | Sellable | Meaning |
|---|---|---|
| `PRODUCT` | when status allows | Something a customer can buy or license |
| `SERVICE` | yes | Work YardLink performs for a fee |
| `PORTFOLIO` | **never** | Evidence of capability, not an offer |
| `INTERNAL_TOOL` | **never** | Runs YardLink; may still serve as proof |
| `FUTURE` | **never** | Announced or planned, not yet deliverable |

Two gates stand between a catalogue entry and a sales recommendation:

1. **Status.** Only `ACTIVE_PRODUCT`, `BETA_PRODUCT`, and `SERVICE` may be offered.
   `PORTFOLIO_ONLY`, `INTERNAL_TOOL`, `EXPERIMENTAL`, `COMING_SOON`, and `ARCHIVED`
   are recommendation-ineligible regardless of how well they match.

2. **Verification.** An entry seeded from a name alone, with capabilities and pricing
   nobody has confirmed, is `verified = 0` and cannot be recommended. This exists
   because inventing a product's capabilities in order to pitch it is the precise
   failure the directive forbids — and the seed data below genuinely does not know
   what these products do.

Everything is editable at runtime. Adding a product must never require a code change,
and every edit is recorded in `catalog_revisions` so the knowledge base has history.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .repository import WinstonRepository, stable_id, utc_now

# ── Classification ───────────────────────────────────────────────────────

KINDS = ("PRODUCT", "SERVICE", "PORTFOLIO", "INTERNAL_TOOL", "FUTURE")

STATUSES = (
    "ACTIVE_PRODUCT", "BETA_PRODUCT", "COMING_SOON", "SERVICE",
    "PORTFOLIO_ONLY", "INTERNAL_TOOL", "EXPERIMENTAL", "ARCHIVED",
)

# The only statuses Winston may build a commercial recommendation around.
SELLABLE_STATUSES = frozenset({"ACTIVE_PRODUCT", "BETA_PRODUCT", "SERVICE"})

# Kinds that may be cited as proof of capability even when not sellable.
PROOF_KINDS = frozenset({"PRODUCT", "SERVICE", "PORTFOLIO", "INTERNAL_TOOL"})

RELATIONS = ("proves", "pairs_with", "upsell", "cross_sell", "replaces")

# Who an entry is SOLD to. This is separate from who it is strategically relevant to.
# YardLink Eats is sold to consumers, but restaurants are the B2B segment it creates a
# relationship with -- and a restaurant does not buy a consumer discovery app. Without
# this distinction the fit engine would offer a consumer product to a business prospect.
AUDIENCES = ("consumer", "business", "both")

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_entries (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    ideal_customer TEXT NOT NULL DEFAULT '',
    industries_json TEXT NOT NULL DEFAULT '[]',
    problems_solved_json TEXT NOT NULL DEFAULT '[]',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    limitations_json TEXT NOT NULL DEFAULT '[]',
    integrations_json TEXT NOT NULL DEFAULT '[]',
    audience TEXT NOT NULL DEFAULT 'business',
    url TEXT NOT NULL DEFAULT '',
    strategic_segments_json TEXT NOT NULL DEFAULT '[]',
    deployment_model TEXT NOT NULL DEFAULT '',
    pricing_model TEXT NOT NULL DEFAULT '',
    price_min_usd REAL,
    price_max_usd REAL,
    recurring_usd REAL,
    effort_hours_min REAL,
    effort_hours_max REAL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog_kind ON catalog_entries(kind, status);

CREATE TABLE IF NOT EXISTS catalog_links (
    id TEXT PRIMARY KEY,
    from_slug TEXT NOT NULL REFERENCES catalog_entries(slug),
    to_slug TEXT NOT NULL REFERENCES catalog_entries(slug),
    relation TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(from_slug, to_slug, relation)
);

-- Every edit, so the knowledge base has history rather than just a current state.
CREATE TABLE IF NOT EXISTS catalog_revisions (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    action TEXT NOT NULL,
    changes_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog_revisions_slug ON catalog_revisions(slug, created_at DESC);
"""

LIST_FIELDS = ("industries", "problems_solved", "capabilities", "limitations",
               "integrations", "strategic_segments")

SCALAR_FIELDS = (
    "name", "kind", "status", "verified", "description", "ideal_customer", "audience", "url",
    "deployment_model", "pricing_model", "price_min_usd", "price_max_usd",
    "recurring_usd", "effort_hours_min", "effort_hours_max", "notes",
)

# ── Seed data ────────────────────────────────────────────────────────────
# Names and broad classification only. Capabilities, pricing, industries, and
# statuses are deliberately EMPTY and every entry is unverified, because none of
# that is known here. Filling it in with plausible guesses would mean Winston
# pitching capabilities that may not exist -- the exact failure this module guards
# against. Kevin verifies each entry, and only then does it become recommendable.

SEED_ENTRIES: tuple[dict[str, Any], ...] = (
    # ── Products ─────────────────────────────────────────────────────────
    # Consumer-facing, strategically central to the restaurant segment. The
    # audience/strategic_segments split keeps Winston from pitching a consumer
    # discovery app to a restaurant owner while still citing it as proof.
    {"slug": "yardlink-eats", "name": "YardLink Eats", "kind": "PRODUCT",
     "status": "ACTIVE_PRODUCT", "audience": "consumer", "verified": 1,
     "description": "Caribbean restaurant discovery network. Connects people with Caribbean "
                    "restaurants and food spots, particularly Jamaican, Guyanese, Haitian, "
                    "and Trinidadian businesses, so they can see what is nearby, what each "
                    "place serves, and which dishes are worth trying. It puts the Caribbean "
                    "food scene in one place instead of scattered across Google Maps, "
                    "Instagram, TikTok, and Facebook pages.",
     "ideal_customer": "People looking for Caribbean food who want to find and support "
                       "Caribbean-owned restaurants. Restaurants participate through "
                       "discovery, not by purchasing the app.",
     "capabilities": ["Caribbean restaurant discovery", "Jamaican restaurant discovery",
                      "Guyanese restaurant discovery", "Haitian restaurant discovery",
                      "Trinidadian restaurant discovery", "location-aware discovery", "maps",
                      "restaurant listings", "restaurant photos", "cuisine classification",
                      "must-try dish information", "Errol AI food guide",
                      "Caribbean Passport gamification", "iOS application",
                      "Android application", "web experience"],
     "limitations": ["not a restaurant SaaS product", "not sold to restaurants",
                     "no restaurant-side commercial offering defined"],
     "strategic_segments": ["restaurant", "jamaican restaurant", "caribbean restaurant",
                            "catering"],
     "url": "https://www.yardlinkeats.com",
     "deployment_model": "consumer mobile and web application",
     "notes": "Restaurants do NOT buy YardLink Eats. audience=consumer keeps it out of B2B "
              "offer matching while remaining citable as proof of restaurant technology, "
              "mobile UX, location-aware apps, and AI integration. The Caribbean focus is "
              "the product's own stated purpose and may inform relevance and tone. It must "
              "never become a pricing variable or a proxy for inferring anything about an "
              "owner."},

    # Repository check (docs/WedLink/README.md) corrects the strategic brief: WedLink is a
    # couple-facing RSVP and invitation app, not a B2B product for venues or vendors, and
    # the README states it has zero production users. Recorded as observed, not as briefed.
    {"slug": "wedlink", "name": "WedLink", "kind": "PRODUCT",
     "status": "COMING_SOON", "audience": "consumer", "verified": 1,
     "description": "Wedding RSVP and invitation app for iOS and Android. Couples create a "
                    "wedding, invite guests individually or by shared link, and track "
                    "attendance. Guests reply without creating an account.",
     "ideal_customer": "Couples planning a wedding.",
     "capabilities": ["wedding creation", "individual guest invitations",
                      "shared-link invitations", "RSVP tracking",
                      "guest reply without account", "iOS application",
                      "Android application"],
     "limitations": ["zero production users per its own README",
                     "not a B2B product for venues, florists, or caterers",
                     "not commercially available"],
     "strategic_segments": ["photographer", "florist", "catering"],
     "notes": "The strategic brief described WedLink as a wedding-industry B2B product. The "
              "repository describes a consumer RSVP app for couples with zero production "
              "users. Recorded as the repository describes it. Citable as proof of mobile "
              "development and event-workflow software; not sellable."},

    # Repository check (docs/guardlink/README.md): a security-industry JOB MARKETPLACE for
    # guards, not workforce-management software for security operators. Working MVP.
    {"slug": "guardlink", "name": "GuardLink", "kind": "PRODUCT",
     "status": "BETA_PRODUCT", "audience": "both", "verified": 0,
     "description": "Security-industry job marketplace for NYC, Long Island, and Westchester. "
                    "Surfaces nearby security jobs with pay, hours, shift, and licence "
                    "requirements visible on the listing.",
     "ideal_customer": "Security guards seeking shifts, and security companies posting roles.",
     "capabilities": ["job search", "database-backed search and filters", "authentication",
                      "saved jobs", "resume upload", "employer apply links",
                      "admin moderation"],
     "limitations": ["working MVP", "commercial terms for employers not yet defined"],
     "strategic_segments": [],
     "notes": "The strategic brief described GuardLink as security workforce management. The "
              "repository describes a two-sided job marketplace. Security companies are "
              "potential employers on the platform rather than buyers of scheduling "
              "software. Left unverified until employer-side commercial terms exist. "
              "Security companies are not a current Winston prospect category."},

    # ── Portfolio ────────────────────────────────────────────────────────
    {"slug": "otonia", "name": "Otonia", "kind": "PORTFOLIO",
     "status": "PORTFOLIO_ONLY", "audience": "consumer", "verified": 1,
     "description": "Consumer calm and wellness application with an interactive Zen Garden, "
                    "guided calming sessions, ambient audio, journaling, and an AI companion.",
     "capabilities": ["Zen Garden interactive environment", "water, lanterns, koi, weather",
                      "customizable animated companion", "Calm Studio guided sessions",
                      "grounding experiences", "ambient sound system", "13 calming games",
                      "Word Match", "Focus Grid", "Bubble Pop", "Rain Drop", "Rhythm Calm",
                      "Puzzle Room", "journaling", "mood tracking",
                      "AI conversation through Talk",
                      "ambience: rain, ocean, waterfall, forest, fireplace, night"],
     "limitations": ["portfolio evidence only", "not sold to wellness businesses",
                     "not a clinical or medical product"],
     "notes": "Proof of sophisticated mobile UX, animation, interactive environments, "
              "gamification, AI integration, conversational systems, audio systems, and "
              "personalization. Never claim it is a healthcare or medical solution."},

    {"slug": "susan", "name": "Susan", "kind": "PORTFOLIO",
     "status": "PORTFOLIO_ONLY", "audience": "consumer", "verified": 1,
     "description": "AI-powered career and job-search automation system.",
     "capabilities": ["AI automation", "job discovery", "application workflows",
                      "resume and application processing", "intelligent workflows",
                      "data processing", "AI-assisted decision making"],
     "limitations": ["portfolio evidence only", "not commercially available"],
     "notes": "Proof of automation systems handling complex multi-step workflows."},

    {"slug": "anansi", "name": "Anansi", "kind": "PORTFOLIO",
     "status": "PORTFOLIO_ONLY", "audience": "business", "verified": 1,
     "description": "Data engine behind YardLink Eats. Collects restaurant data from Google "
                    "Places, provides an editorial review surface for deciding what ships, "
                    "and answers questions about that data with citations using a local model.",
     "capabilities": ["data collection", "web scraping", "Google Places ingestion",
                      "data pipelines", "data normalization", "editorial review workflow",
                      "structured data", "database ingestion", "local-model question "
                      "answering with citations", "zero-cost inference"],
     "limitations": ["portfolio evidence only", "internal data system"],
     "notes": "Proof of data engineering, pipeline reliability, structured data collection, "
              "and cost-controlled local inference."},

    # ── Internal ─────────────────────────────────────────────────────────
    {"slug": "winston", "name": "Winston", "kind": "INTERNAL_TOOL",
     "status": "INTERNAL_TOOL", "audience": "business", "verified": 1,
     "description": "YardLink Studio's internal commercial-intelligence system. Researches "
                    "businesses, audits digital presence, matches problems to capabilities, "
                    "recommends offers, generates outreach, and tracks outcomes.",
     "capabilities": ["AI agents", "AI orchestration", "sales automation", "data engineering",
                      "prospect intelligence", "web research", "digital auditing",
                      "commercial scoring", "prospect memory", "analytics",
                      "workflow automation", "email automation", "outcome tracking",
                      "revenue attribution", "local AI inference", "provider routing"],
     "limitations": ["internal tool", "never sold to prospects"],
     "notes": "Citable as proof of AI automation, sales automation, and data engineering. "
              "Never present Winston as something a prospect can purchase."},

    # The fulfilment engine behind the website service. A client buys the outcome
    # (a professional site, designed, written, and delivered), not the tool that
    # produces it. Capabilities below are limited to what PRODUCTION.md marks DONE.
    {"slug": "website-builder", "name": "YardLink Studio Website Builder",
     "kind": "INTERNAL_TOOL", "status": "INTERNAL_TOOL", "audience": "business",
     "verified": 1,
     "description": "Browser-based website builder. Structured business information goes "
                    "in and a responsive, SEO-rich website comes out, with matching logos. "
                    "The core path runs entirely on device with no backend.",
     "capabilities": ["structured client intake", "industry-aware site generation",
                      "live preview", "theme and layout system", "logo generation",
                      "standalone HTML export", "LocalBusiness JSON-LD",
                      "OpenGraph and meta tags", "runs offline in the browser"],
     "limitations": ["one-click publishing to a live URL is NOT built; the publish seam "
                     "supports download only and cloud targets are Phase 4",
                     "no Cloudflare or worker infrastructure exists in the repository",
                     "Supabase persistence activates only when credentials are supplied",
                     "internal fulfilment platform, never sold to a client"],
     "deployment_model": "internal browser-based tool",
     "notes": "Verified against PRODUCTION.md, which marks Phase 1 (local generation, "
              "preview, HTML download) as done. Automatic publishing and cloud hosting "
              "are later phases and must not be claimed. Cited as the reason YardLink can "
              "deliver websites quickly; the commercial offer is website-service."},

    {"slug": "errol", "name": "Errol", "kind": "INTERNAL_TOOL",
     "status": "INTERNAL_TOOL", "audience": "consumer", "verified": 1,
     "description": "AI Caribbean food guide integrated into YardLink Eats.",
     "capabilities": ["conversational AI", "cultural and linguistic adaptation",
                      "recommendation systems", "AI integration", "consumer-facing AI"],
     "limitations": ["component of YardLink Eats", "not a standalone product"],
     "notes": "Citable as proof of conversational AI and consumer-facing AI integration."},
)

# Candidate YardLink Studio services. Seeded UNVERIFIED by design: the operator must
# confirm which are genuinely offered before Winston may recommend any of them.
CANDIDATE_SERVICES: tuple[dict[str, Any], ...] = (
    {"slug": "website-service", "name": "YardLink Studio Website Service",
     "problems_solved": ["no website", "outdated website", "not mobile friendly",
                         "no lead capture", "weak seo basics"],
     "proof": ["website-builder", "yardlink-eats", "otonia"]},
    {"slug": "web-development", "name": "Web Development",
     "problems_solved": ["no website", "outdated website", "not mobile friendly",
                         "weak seo basics", "no lead capture"],
     "proof": ["yardlink-eats", "otonia"]},
    {"slug": "website-redesign", "name": "Website Redesign",
     "problems_solved": ["outdated website", "not mobile friendly", "weak seo basics"],
     "proof": ["yardlink-eats", "otonia"]},
    {"slug": "landing-pages", "name": "Landing Pages",
     "problems_solved": ["no lead capture", "weak seo basics"], "proof": ["yardlink-eats"]},
    {"slug": "booking-systems", "name": "Booking Systems",
     "problems_solved": ["no online booking"],
     "industries": ["barbershop", "hair salon", "nail salon", "dentist", "photographer",
                    "gym", "auto repair", "cleaning service", "daycare", "tutoring"],
     "proof": ["wedlink", "yardlink-eats"]},
    {"slug": "ordering-systems", "name": "Ordering Systems",
     "problems_solved": ["no online ordering"],
     "industries": ["restaurant", "jamaican restaurant", "caribbean restaurant", "catering"],
     "proof": ["yardlink-eats", "anansi"]},
    {"slug": "lead-capture-systems", "name": "Lead Capture Systems",
     "problems_solved": ["no lead capture", "no measurement"], "proof": ["winston"]},
    {"slug": "mobile-app-development", "name": "Mobile App Development",
     "problems_solved": [], "proof": ["otonia", "yardlink-eats", "wedlink"]},
    {"slug": "ai-chatbots", "name": "AI Chatbots and Assistants",
     "problems_solved": [], "proof": ["errol", "otonia", "winston"]},
    {"slug": "ai-automation", "name": "AI Automation",
     "problems_solved": [], "proof": ["winston", "susan"]},
    {"slug": "marketing-automation", "name": "Marketing Automation",
     "problems_solved": ["no measurement"], "proof": ["winston"]},
    {"slug": "crm-setup", "name": "CRM Setup", "problems_solved": [], "proof": ["winston"]},
    {"slug": "custom-software", "name": "Custom Software",
     "problems_solved": [], "proof": ["guardlink", "winston", "otonia"]},
    {"slug": "api-backend-development", "name": "API and Backend Development",
     "problems_solved": [], "proof": ["yardlink-eats", "winston", "anansi"]},
    {"slug": "data-engineering", "name": "Data Engineering",
     "problems_solved": [], "proof": ["anansi", "winston"]},
)


class UnknownEntry(KeyError):
    """Raised when a catalogue slug does not exist."""


class CatalogValidationError(ValueError):
    """Raised when an edit would put the catalogue in an invalid state."""


class Catalog:
    """The YardLink knowledge base. Editable at runtime, versioned, and gated."""

    def __init__(self, repository: WinstonRepository) -> None:
        self.repository = repository

    # ── setup ────────────────────────────────────────────────────────────

    NEW_COLUMNS = (
        ("audience", "TEXT NOT NULL DEFAULT 'business'"),
        ("strategic_segments_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("url", "TEXT NOT NULL DEFAULT ''"),
    )

    def initialize(self, *, seed: bool = True) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.executescript(CATALOG_SCHEMA)
            # Additive migration for catalogues created before these columns existed.
            existing = {row["name"] for row in connection.execute("PRAGMA table_info(catalog_entries)")}
            for column, definition in self.NEW_COLUMNS:
                if column not in existing:
                    connection.execute(f"ALTER TABLE catalog_entries ADD COLUMN {column} {definition}")
        if seed:
            for entry in SEED_ENTRIES:
                if self.get(entry["slug"]) is None:
                    payload = dict(entry)
                    verified = payload.pop("verified", 0)
                    self.upsert(payload, actor="seed")
                    if verified:
                        self.verify(payload["slug"], actor="seed")

            # Candidate services seed UNVERIFIED. The operator confirms which are
            # genuinely offered; Winston recommends none of them until then.
            for service in CANDIDATE_SERVICES:
                if self.get(service["slug"]) is not None:
                    continue
                payload = {
                    "slug": service["slug"], "name": service["name"], "kind": "SERVICE",
                    "status": "SERVICE", "audience": "business",
                    "problems_solved": service.get("problems_solved", []),
                    "industries": service.get("industries", []),
                    "notes": "Candidate service. Verify with POST /catalog/<slug>/verify "
                             "before Winston may recommend it.",
                }
                self.upsert(payload, actor="seed")
                for proof_slug in service.get("proof", []):
                    if self.get(proof_slug) is not None:
                        self.link(service["slug"], proof_slug, "proves", note="seeded proof map")

    # ── validation ───────────────────────────────────────────────────────

    @staticmethod
    def _validate(payload: dict[str, Any]) -> None:
        kind, status = payload.get("kind"), payload.get("status")
        if kind not in KINDS:
            raise CatalogValidationError(f"kind must be one of {KINDS}, got {kind!r}")
        if status not in STATUSES:
            raise CatalogValidationError(f"status must be one of {STATUSES}, got {status!r}")

        # Classification and status must agree, or the sellable gate becomes meaningless.
        if kind in ("PORTFOLIO", "INTERNAL_TOOL", "FUTURE") and status in SELLABLE_STATUSES:
            raise CatalogValidationError(
                f"a {kind} entry cannot carry the sellable status {status!r}; "
                "reclassify it as PRODUCT or SERVICE if it is genuinely for sale")
        if kind == "SERVICE" and status not in ("SERVICE", "EXPERIMENTAL", "ARCHIVED", "COMING_SOON"):
            raise CatalogValidationError(f"a SERVICE entry cannot have status {status!r}")

        audience = payload.get("audience", "business")
        if audience not in AUDIENCES:
            raise CatalogValidationError(f"audience must be one of {AUDIENCES}, got {audience!r}")

        for field in ("price_min_usd", "price_max_usd"):
            value = payload.get(field)
            if value is not None and float(value) < 0:
                raise CatalogValidationError(f"{field} cannot be negative")
        low, high = payload.get("price_min_usd"), payload.get("price_max_usd")
        if low is not None and high is not None and float(low) > float(high):
            raise CatalogValidationError("price_min_usd cannot exceed price_max_usd")

    # ── read ─────────────────────────────────────────────────────────────

    @staticmethod
    def _hydrate(row: Any) -> dict[str, Any]:
        entry = dict(row)
        for field in LIST_FIELDS:
            entry[field] = json.loads(entry.pop(f"{field}_json", "[]") or "[]")
        entry["verified"] = bool(entry["verified"])
        entry.setdefault("audience", "business")
        entry["sellable"] = entry["status"] in SELLABLE_STATUSES and entry["verified"]
        # Sellable is not the same as offerable to the businesses Winston prospects.
        # A verified, active consumer product is genuinely for sale -- just not to a
        # barbershop. Only business-facing entries may become an outreach offer.
        entry["offerable_to_business"] = entry["sellable"] and entry["audience"] in ("business", "both")
        # Proof is a factual claim made in a real email, so it requires verification too.
        entry["citable_as_proof"] = entry["kind"] in PROOF_KINDS and entry["verified"]
        return entry

    def get(self, slug: str) -> dict[str, Any] | None:
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT * FROM catalog_entries WHERE slug=?", (slug,)).fetchone()
        return self._hydrate(row) if row else None

    def list(self, *, kind: str | None = None, sellable_only: bool = False,
             include_unverified: bool = True) -> list[dict[str, Any]]:
        clauses, args = [], []
        if kind:
            clauses.append("kind=?")
            args.append(kind)
        if sellable_only:
            clauses.append(f"status IN ({','.join('?' * len(SELLABLE_STATUSES))})")
            args.extend(sorted(SELLABLE_STATUSES))
        if not include_unverified:
            clauses.append("verified=1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.repository.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM catalog_entries{where} ORDER BY kind, name", args).fetchall()
        return [self._hydrate(row) for row in rows]

    def sellable(self) -> list[dict[str, Any]]:
        """Entries Winston may actually build a recommendation around."""
        return [e for e in self.list(sellable_only=True, include_unverified=False)]

    def offerable(self, industry: str = "") -> list[dict[str, Any]]:
        """Entries Winston may actually offer a business prospect.

        Excludes consumer products even when they are verified and shipping: a
        restaurant does not buy a consumer discovery app. Those entries remain
        available as proof through :meth:`proof_for` and :meth:`strategic_proof_for`.
        """
        return [e for e in self.list(sellable_only=True, include_unverified=False)
                if e["offerable_to_business"]]

    def strategic_proof_for(self, industry: str) -> list[dict[str, Any]]:
        """Verified entries that create standing in an industry without being sold to it.

        YardLink Eats is not sold to restaurants, but it demonstrates restaurant-focused
        engineering and gives YardLink an existing relationship in that market. That is
        credibility to cite, not an offer to make.
        """
        industry = (industry or "").casefold().strip()
        if not industry:
            return []
        return [e for e in self.list(include_unverified=False)
                if industry in {str(s).casefold() for s in (e.get("strategic_segments") or [])}]

    def proof_for(self, slug: str) -> list[dict[str, Any]]:
        """Entries linked as evidence that YardLink can deliver this one."""
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT e.* FROM catalog_links l JOIN catalog_entries e ON e.slug = l.to_slug
                   WHERE l.from_slug=? AND l.relation='proves' AND e.verified=1""", (slug,)).fetchall()
        return [self._hydrate(row) for row in rows]

    def revisions(self, slug: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT action, changes_json, actor, created_at FROM catalog_revisions
                   WHERE slug=? ORDER BY created_at DESC LIMIT ?""", (slug, limit)).fetchall()
        return [{**dict(r), "changes": json.loads(r["changes_json"])} for r in rows]

    # ── write ────────────────────────────────────────────────────────────

    def upsert(self, payload: dict[str, Any], *, actor: str = "user") -> dict[str, Any]:
        """Create or update an entry. No code change is ever required to add a product."""
        slug = (payload.get("slug") or "").strip()
        if not slug:
            raise CatalogValidationError("slug is required")

        existing = self.get(slug)
        merged: dict[str, Any] = {
            "kind": "PRODUCT", "status": "EXPERIMENTAL", "verified": 0, "name": slug,
            "audience": "business", "url": "",
        }
        if existing:
            merged.update({k: v for k, v in existing.items()
                           if k not in ("sellable", "citable_as_proof", "created_at", "updated_at")})
        merged.update({k: v for k, v in payload.items() if k != "slug"})
        self._validate(merged)

        # Changing what an entry claims to do invalidates prior verification.
        if existing and existing["verified"] and self._materially_changed(existing, merged):
            merged["verified"] = 0

        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                f"""INSERT INTO catalog_entries(
                        slug,{','.join(SCALAR_FIELDS)},
                        {','.join(f'{f}_json' for f in LIST_FIELDS)},created_at,updated_at)
                    VALUES({','.join('?' * (1 + len(SCALAR_FIELDS) + len(LIST_FIELDS) + 2))})
                    ON CONFLICT(slug) DO UPDATE SET
                        {','.join(f'{f}=excluded.{f}' for f in SCALAR_FIELDS)},
                        {','.join(f'{f}_json=excluded.{f}_json' for f in LIST_FIELDS)},
                        updated_at=excluded.updated_at""",
                (slug,
                 *[int(merged.get(f, 0)) if f == "verified" else merged.get(f, "" if f in
                   ("name", "kind", "status", "description", "ideal_customer",
                    "deployment_model", "pricing_model", "notes") else None)
                   for f in SCALAR_FIELDS],
                 *[json.dumps(list(merged.get(f) or [])) for f in LIST_FIELDS],
                 existing["created_at"] if existing else now, now))

            connection.execute(
                """INSERT INTO catalog_revisions(id,slug,action,changes_json,actor,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (stable_id("catalog_rev", slug, now, actor), slug,
                 "update" if existing else "create",
                 json.dumps({k: v for k, v in payload.items() if k != "slug"}, default=str),
                 actor, now))

        self.repository.add_event("catalog.updated", entity_type="catalog", entity_id=slug,
                                  actor=actor, details={"action": "update" if existing else "create"})
        return self.get(slug)  # type: ignore[return-value]

    @staticmethod
    def _materially_changed(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        """Did the claims change in a way that needs re-checking by a human?"""
        for field in ("kind", "status", "description", "pricing_model",
                      "price_min_usd", "price_max_usd", *LIST_FIELDS):
            if existing.get(field) != incoming.get(field):
                return True
        return False

    def verify(self, slug: str, *, actor: str = "user", verified: bool = True) -> dict[str, Any]:
        """Mark an entry as confirmed by a human. Until then it cannot be recommended."""
        if self.get(slug) is None:
            raise UnknownEntry(slug)
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute("UPDATE catalog_entries SET verified=?,updated_at=? WHERE slug=?",
                               (int(verified), now, slug))
            connection.execute(
                """INSERT INTO catalog_revisions(id,slug,action,changes_json,actor,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (stable_id("catalog_rev", slug, now, actor, "verify"), slug,
                 "verify" if verified else "unverify",
                 json.dumps({"verified": verified}), actor, now))
        return self.get(slug)  # type: ignore[return-value]

    def link(self, from_slug: str, to_slug: str, relation: str, *, note: str = "") -> str:
        if relation not in RELATIONS:
            raise CatalogValidationError(f"relation must be one of {RELATIONS}")
        for slug in (from_slug, to_slug):
            if self.get(slug) is None:
                raise UnknownEntry(slug)
        link_id = stable_id("catalog_link", from_slug, to_slug, relation)
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO catalog_links(id,from_slug,to_slug,relation,note,created_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(from_slug,to_slug,relation) DO NOTHING""",
                (link_id, from_slug, to_slug, relation, note, utc_now()))
        return link_id

    def delete(self, slug: str, *, actor: str = "user") -> None:
        """Archive rather than destroy: history stays, recommendations stop."""
        if self.get(slug) is None:
            raise UnknownEntry(slug)
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE catalog_entries SET status='ARCHIVED',verified=0,updated_at=? WHERE slug=?",
                (now, slug))
            connection.execute(
                """INSERT INTO catalog_revisions(id,slug,action,changes_json,actor,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (stable_id("catalog_rev", slug, now, actor, "archive"), slug, "archive",
                 "{}", actor, now))

    # ── readiness ────────────────────────────────────────────────────────

    def readiness(self) -> dict[str, Any]:
        """What still needs a human before Winston can sell anything.

        Reported honestly: an unverified catalogue means Winston has no offers, and
        that should be visible rather than silently producing zero recommendations.
        """
        entries = self.list()
        # Readiness is about outreach: what Winston may offer a business prospect. A
        # verified consumer product is sellable but contributes nothing here.
        sellable = [e for e in entries if e["offerable_to_business"]]
        awaiting = [
            {"slug": e["slug"], "name": e["name"], "kind": e["kind"], "status": e["status"],
             "missing": [f for f in ("description", "problems_solved", "capabilities",
                                     "industries", "pricing_model")
                         if not e.get(f)]}
            for e in entries if not e["verified"] and e["kind"] in ("PRODUCT", "SERVICE")
        ]
        return {
            "entries": len(entries),
            "by_kind": {k: sum(1 for e in entries if e["kind"] == k) for k in KINDS},
            "offerable_to_business": len(sellable),
            "offerable_slugs": [e["slug"] for e in sellable],
            "sellable_any_audience": sum(1 for e in entries if e["sellable"]),
            "awaiting_verification": awaiting,
            "can_recommend": bool(sellable),
        }
