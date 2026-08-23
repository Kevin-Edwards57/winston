"""Fulfilment handoff — where selling ends and building begins.

Winston finds and closes the work. The YardLink Studio Website Builder produces the
site. This module is the seam between them, and it is deliberately a seam rather than
an integration, because **the Website Builder currently exposes no HTTP API**. Its
`app/` directory contains no route handlers; the core path runs entirely in the browser
with no backend.

So Winston does not call it. Winston produces a handoff document shaped to the
Builder's real `SiteData` type (`lib/site-generator.ts`), which an operator imports.
Every field below exists in that type. Nothing is invented, and no endpoint is faked.

What Winston contributes that a blank intake form cannot: the business facts it already
verified during research, and the problems it observed. A redesign brief that opens with
"not mobile friendly, confidence 0.85, evidence: no viewport meta tag" is a better
starting point than an empty theme picker.

**Project status is operator-reported, not polled.** With no API there is nothing to
poll, and inventing a status feed would be exactly the fake integration worth avoiding.
The status field records what a human tells Winston, with the time they told it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .repository import WinstonRepository, stable_id, utc_now

# Services whose delivery runs through the Website Builder. Others are fulfilled
# by hand and produce no handoff.
BUILDER_FULFILLED = {
    "website-service", "website-redesign", "landing-pages", "web-development",
}

# Lifecycle of a sold engagement. Every transition is recorded by a human, because
# no system is currently able to report it automatically.
PROJECT_STATUSES = (
    "handoff_ready",    # Winston has produced the brief
    "intake_imported",  # brief loaded into the Builder
    "in_production",    # site being generated and refined
    "in_review",        # client reviewing
    "published",        # live
    "cancelled",
)

# Section orders the Builder composes per industry. Read from its industry-aware
# generator rather than chosen here, so Winston suggests rather than dictates.
INDUSTRY_SECTIONS: dict[str, list[str]] = {
    "restaurant": ["hero", "about", "menu", "gallery", "location", "hours", "cta", "footer"],
    "jamaican restaurant": ["hero", "about", "menu", "gallery", "location", "hours", "cta", "footer"],
    "caribbean restaurant": ["hero", "about", "menu", "gallery", "location", "hours", "cta", "footer"],
    "catering": ["hero", "about", "services", "gallery", "cta", "contact", "footer"],
    "barbershop": ["hero", "about", "services", "gallery", "booking", "location", "cta", "footer"],
    "hair salon": ["hero", "about", "services", "gallery", "booking", "location", "cta", "footer"],
    "nail salon": ["hero", "about", "services", "gallery", "booking", "location", "cta", "footer"],
    "dentist": ["hero", "services", "about", "credentials", "booking", "location", "cta", "footer"],
    "auto repair": ["hero", "services", "about", "location", "hours", "cta", "footer"],
    "photographer": ["hero", "gallery", "about", "services", "cta", "contact", "footer"],
    "gym": ["hero", "about", "services", "gallery", "hours", "cta", "location", "footer"],
}
DEFAULT_SECTIONS = ["hero", "about", "services", "gallery", "location", "cta", "footer"]

FULFILMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_projects (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    service_slug TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'handoff_ready',
    builder_reference TEXT NOT NULL DEFAULT '',
    published_url TEXT NOT NULL DEFAULT '',
    agreed_price_usd REAL,
    handoff_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    status_reported_by TEXT NOT NULL DEFAULT '',
    status_reported_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(contact_id, service_slug)
);
CREATE INDEX IF NOT EXISTS client_projects_status ON client_projects(status);
"""


class NotBuilderFulfilled(ValueError):
    """Raised when a service is not delivered through the Website Builder."""


@dataclass
class Handoff:
    """A brief the Builder can import, plus the sales context behind it."""
    contact_id: str
    service_slug: str
    site_data: dict[str, Any]
    suggested_sections: list[str]
    observed_problems: list[dict[str, Any]]
    known_assets: dict[str, Any]
    sales_context: dict[str, Any]
    gaps: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "service_slug": self.service_slug,
            "site_data": self.site_data,
            "suggested_sections": self.suggested_sections,
            "observed_problems": self.observed_problems,
            "known_assets": self.known_assets,
            "sales_context": self.sales_context,
            "gaps": self.gaps,
            "generated_at": utc_now(),
            "target": "YardLink Studio Website Builder",
            "contract": "lib/site-generator.ts :: SiteData",
        }


class FulfilmentBridge:
    """Produces Builder-shaped briefs and tracks sold engagements."""

    def __init__(self, repository: WinstonRepository, catalog: Any,
                 signal_store: Any, fit_engine: Any) -> None:
        self.repository = repository
        self.catalog = catalog
        self.signals = signal_store
        self.fit = fit_engine

    def initialize(self) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.executescript(FULFILMENT_SCHEMA)

    # ── handoff ──────────────────────────────────────────────────────────

    def build_handoff(self, contact_id: str, service_slug: str) -> Handoff:
        """Assemble a brief from what Winston verified while selling.

        Fields Winston could not observe are left empty and listed under ``gaps``,
        so the operator knows what to collect rather than discovering blanks later.
        """
        if service_slug not in BUILDER_FULFILLED:
            raise NotBuilderFulfilled(
                f"{service_slug!r} is not delivered through the Website Builder. "
                f"Builder-fulfilled services: {sorted(BUILDER_FULFILLED)}")

        with self.repository.read() as connection:
            contact = connection.execute(
                "SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if contact is None:
            raise KeyError(f"Unknown contact {contact_id}")
        contact = dict(contact)

        industry = (contact.get("business_type") or "").casefold().strip()
        signals = self.signals.for_contact(contact_id)
        assessment = self.fit.assess(contact_id)

        # Only what Winston actually observed. Empty is honest; invented is not.
        site_data = {
            "businessName": contact.get("name") or "",
            "industry": industry,
            "tagline": "",
            "phone": contact.get("phone") or "",
            "email": contact.get("email") or "",
            "address": contact.get("address") or "",
            "story": "",
            "ownerName": "",
            "ownerTitle": "",
            "ownerBio": "",
            "team": "",
            "accolades": "",
            "serviceArea": contact.get("address") or "",
            "seoTitle": f"{contact.get('name') or 'Business'} | {industry.title()}" if industry else "",
            "seoDescription": "",
            "seoKeywords": ", ".join(filter(None, [industry, "New York"])),
            "aiEnabled": False,
            "aiName": "",
            "aiTone": "",
            "menu": [],
            "assetCount": 0,
            "logoLabel": contact.get("name") or "",
        }

        gaps = [field for field in ("tagline", "story", "ownerName", "seoDescription")
                if not site_data[field]]
        if industry in ("restaurant", "jamaican restaurant", "caribbean restaurant", "catering"):
            gaps.append("menu (Winston does not extract menu items)")
        gaps.append("brand colours and logo file")
        gaps.append("photography")

        known_assets = {
            "existing_website": contact.get("website") or "",
            "instagram": contact.get("instagram") or "",
            "facebook": contact.get("facebook") or "",
            "tiktok": contact.get("tiktok") or "",
            "existing_platform": (signals.get("cms") or {}).get("value") or "none observed",
        }

        offer = assessment.recommended_service or assessment.recommended_product
        sales_context = {
            "recommended_service": offer,
            "scores": assessment.as_dict()["scores"],
            "proof_cited": [p.get("slug") for p in assessment.proof],
            "reasons": assessment.reasons[:5],
        }

        return Handoff(
            contact_id=contact_id, service_slug=service_slug, site_data=site_data,
            suggested_sections=INDUSTRY_SECTIONS.get(industry, DEFAULT_SECTIONS),
            observed_problems=[p.as_dict() for p in assessment.problems],
            known_assets=known_assets, sales_context=sales_context, gaps=gaps)

    # ── projects ─────────────────────────────────────────────────────────

    def create_project(self, contact_id: str, service_slug: str, *,
                       agreed_price_usd: float | None = None,
                       actor: str = "operator") -> dict[str, Any]:
        """Record a sold engagement and freeze the brief that goes with it."""
        handoff = self.build_handoff(contact_id, service_slug)
        project_id = stable_id("project", contact_id, service_slug)
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT id FROM client_projects WHERE contact_id=? AND service_slug=?",
                (contact_id, service_slug)).fetchone()
            if existing:
                raise ValueError(
                    f"A {service_slug} project already exists for this client. "
                    "Duplicate engagements are blocked.")
            connection.execute(
                """INSERT INTO client_projects(id,contact_id,service_slug,status,
                       agreed_price_usd,handoff_json,status_reported_by,
                       status_reported_at,created_at,updated_at)
                   VALUES(?,?,?,'handoff_ready',?,?,?,?,?,?)""",
                (project_id, contact_id, service_slug, agreed_price_usd,
                 json.dumps(handoff.as_dict(), default=str), actor, now, now, now))
        self.repository.add_event("project.created", entity_type="project",
                                  entity_id=project_id, actor=actor,
                                  details={"service": service_slug})
        return self.get_project(project_id)  # type: ignore[return-value]

    def update_status(self, project_id: str, status: str, *,
                      builder_reference: str = "", published_url: str = "",
                      notes: str = "", actor: str = "operator") -> dict[str, Any]:
        """Record what a human reports.

        There is no polling here because there is nothing to poll. Presenting an
        operator-typed status as though it were observed would be a fake integration.
        """
        if status not in PROJECT_STATUSES:
            raise ValueError(f"status must be one of {PROJECT_STATUSES}")
        if status == "published" and not published_url:
            raise ValueError("a published project needs its live URL")
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            changed = connection.execute(
                """UPDATE client_projects
                   SET status=?, builder_reference=COALESCE(NULLIF(?,''),builder_reference),
                       published_url=COALESCE(NULLIF(?,''),published_url),
                       notes=COALESCE(NULLIF(?,''),notes),
                       status_reported_by=?, status_reported_at=?, updated_at=?
                   WHERE id=?""",
                (status, builder_reference, published_url, notes, actor, now, now, project_id)
            ).rowcount
            if changed != 1:
                raise KeyError(f"Unknown project {project_id}")
        self.repository.add_event("project.status", entity_type="project",
                                  entity_id=project_id, actor=actor,
                                  details={"status": status})
        return self.get_project(project_id)  # type: ignore[return-value]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.repository.read() as connection:
            row = connection.execute(
                """SELECT p.*, c.name AS business, c.website
                   FROM client_projects p JOIN contacts c ON c.id = p.contact_id
                   WHERE p.id=?""", (project_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["handoff"] = json.loads(record.pop("handoff_json") or "{}")
        record["status_source"] = "operator-reported"
        return record

    def projects(self) -> list[dict[str, Any]]:
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT p.id,p.contact_id,p.service_slug,p.status,p.builder_reference,
                          p.published_url,p.agreed_price_usd,p.status_reported_at,
                          c.name AS business, c.business_type
                   FROM client_projects p JOIN contacts c ON c.id = p.contact_id
                   ORDER BY p.updated_at DESC""").fetchall()
        return [dict(row) for row in rows]

    def status(self) -> dict[str, Any]:
        projects = self.projects()
        published = [p for p in projects if p["status"] == "published"]
        return {
            "projects": len(projects),
            "by_status": {s: sum(1 for p in projects if p["status"] == s)
                          for s in PROJECT_STATUSES},
            "published": len(published),
            "published_urls": [p["published_url"] for p in published if p["published_url"]],
            "builder_api_available": False,
            "integration_note": (
                "The Website Builder exposes no HTTP API; its app directory contains no "
                "route handlers and the core path runs in the browser. Winston produces "
                "an importable brief and records operator-reported status. See "
                "docs/FULFILMENT_CONTRACT.md for what the Builder would need to expose "
                "for a live connection."),
        }
