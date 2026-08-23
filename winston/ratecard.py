"""Rate card — operator assumptions, labelled as such.

The single most dangerous thing this module could do is let a starting guess harden
into an authoritative number. "Website Builder Basic: $500 to $750" is where YardLink
has decided to begin. It is not what the market pays, not what converts best, and not
derived from a single completed engagement, because none exist yet.

So provenance is structural. Every price and effort value carries a
:class:`Basis` that travels with it into the pricing engine, the API, and the review
screen. A number sourced from `OPERATOR_ASSUMPTION` is rendered differently from one
sourced from `HISTORICAL`, and nothing in the system can quietly promote the first into
the second. Only :meth:`RateCard.calibrate_from_outcomes` may raise a basis, and it
refuses when there are too few closed engagements to support the claim.

The intended progression:

    OPERATOR_ASSUMPTION  ->  starting point, today
    OBSERVED             ->  quoted in real proposals, outcomes not yet known
    HISTORICAL           ->  enough won and lost deals to describe what happens
    CALIBRATED           ->  prices adjusted from measured close rates and margins

Starter values seed **disabled**. Having a rate for a service is not the same as
offering that service, and the pricing engine will not quote a disabled entry.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .repository import WinstonRepository, utc_now


class Basis(str, Enum):
    """Where a number came from. Never upgraded implicitly."""
    OPERATOR_ASSUMPTION = "operator_assumption"
    OBSERVED = "observed"
    HISTORICAL = "historical"
    CALIBRATED = "calibrated"

    @property
    def label(self) -> str:
        return {
            "operator_assumption": "Operator assumption, not market data",
            "observed": "Quoted in real proposals, outcomes not yet known",
            "historical": "Derived from completed engagements",
            "calibrated": "Adjusted from measured close rates and margins",
        }[self.value]

    @property
    def is_evidence_backed(self) -> bool:
        return self in (Basis.HISTORICAL, Basis.CALIBRATED)


# Minimum closed engagements before a basis may be raised above assumption.
CALIBRATION_MINIMUM = 8

RATECARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_card (
    slug TEXT PRIMARY KEY REFERENCES catalog_entries(slug),
    enabled INTEGER NOT NULL DEFAULT 0,
    price_floor_usd REAL,
    price_target_usd REAL,
    price_premium_usd REAL,
    minimum_engagement_usd REAL,
    recurring_monthly_usd REAL,
    effort_hours_min REAL,
    effort_hours_max REAL,
    price_basis TEXT NOT NULL DEFAULT 'operator_assumption',
    effort_basis TEXT NOT NULL DEFAULT 'operator_assumption',
    sample_size INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT 'seed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Every edit, so a number's history is inspectable rather than assumed.
CREATE TABLE IF NOT EXISTS rate_card_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    changes_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT 'operator',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_card_revisions_slug ON rate_card_revisions(slug, created_at DESC);
"""

# Starting points supplied by the operator. Seeded DISABLED: a rate existing does not
# mean YardLink offers the service. Every one is an assumption.
STARTER_RATE_CARD: tuple[dict[str, Any], ...] = (
    {"slug": "website-service", "price_floor_usd": 750, "price_target_usd": 950,
     "price_premium_usd": 1200, "effort_hours_min": 8, "effort_hours_max": 15,
     "notes": "Standard website build"},
    {"slug": "website-redesign", "price_floor_usd": 750, "price_target_usd": 1100,
     "price_premium_usd": 1500, "effort_hours_min": 8, "effort_hours_max": 18},
    {"slug": "landing-pages", "price_floor_usd": 300, "price_target_usd": 450,
     "price_premium_usd": 600, "effort_hours_min": 3, "effort_hours_max": 6},
    {"slug": "booking-systems", "price_floor_usd": 500, "price_target_usd": 750,
     "price_premium_usd": 1000, "effort_hours_min": 5, "effort_hours_max": 10},
    {"slug": "ordering-systems", "price_floor_usd": 600, "price_target_usd": 900,
     "price_premium_usd": 1200, "effort_hours_min": 6, "effort_hours_max": 12},
    {"slug": "lead-capture-systems", "price_floor_usd": 300, "price_target_usd": 500,
     "price_premium_usd": 750, "effort_hours_min": 3, "effort_hours_max": 8},
    {"slug": "web-development", "price_floor_usd": 1000, "price_target_usd": 1750,
     "price_premium_usd": 2500, "effort_hours_min": 12, "effort_hours_max": 30,
     "notes": "Custom build beyond the standard website service"},
    {"slug": "ai-chatbots", "price_floor_usd": 500, "price_target_usd": 950,
     "price_premium_usd": 1500, "effort_hours_min": 6, "effort_hours_max": 18},
    {"slug": "ai-automation", "price_floor_usd": 750, "price_target_usd": 1500,
     "price_premium_usd": 2500, "effort_hours_min": 8, "effort_hours_max": 30},
    {"slug": "marketing-automation", "price_floor_usd": 750, "price_target_usd": 1250,
     "price_premium_usd": 2000, "effort_hours_min": 8, "effort_hours_max": 25},
    {"slug": "crm-setup", "price_floor_usd": 750, "price_target_usd": 1250,
     "price_premium_usd": 2000, "effort_hours_min": 8, "effort_hours_max": 25},
    {"slug": "custom-software", "price_floor_usd": 1500, "price_target_usd": 3000,
     "price_premium_usd": 5000, "effort_hours_min": 20, "effort_hours_max": 60},
    {"slug": "mobile-app-development", "price_floor_usd": 2500, "price_target_usd": 5000,
     "price_premium_usd": 8000, "effort_hours_min": 40, "effort_hours_max": 120},
    {"slug": "api-backend-development", "price_floor_usd": 750, "price_target_usd": 1750,
     "price_premium_usd": 3000, "effort_hours_min": 8, "effort_hours_max": 35},
    {"slug": "data-engineering", "price_floor_usd": 1000, "price_target_usd": 2500,
     "price_premium_usd": 5000, "effort_hours_min": 15, "effort_hours_max": 60},
)

PRICE_FIELDS = ("price_floor_usd", "price_target_usd", "price_premium_usd",
                "minimum_engagement_usd", "recurring_monthly_usd")
EFFORT_FIELDS = ("effort_hours_min", "effort_hours_max")


@dataclass
class RateEntry:
    """One service's commercial parameters, with provenance attached."""
    slug: str
    enabled: bool
    price_floor_usd: float | None
    price_target_usd: float | None
    price_premium_usd: float | None
    effort_hours_min: float | None
    effort_hours_max: float | None
    price_basis: Basis
    effort_basis: Basis
    sample_size: int
    minimum_engagement_usd: float | None = None
    recurring_monthly_usd: float | None = None
    notes: str = ""

    @property
    def has_price(self) -> bool:
        return self.price_target_usd is not None

    @property
    def has_effort(self) -> bool:
        return bool(self.effort_hours_min and self.effort_hours_max)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug, "enabled": self.enabled,
            "price_floor_usd": self.price_floor_usd,
            "price_target_usd": self.price_target_usd,
            "price_premium_usd": self.price_premium_usd,
            "minimum_engagement_usd": self.minimum_engagement_usd,
            "recurring_monthly_usd": self.recurring_monthly_usd,
            "effort_hours_min": self.effort_hours_min,
            "effort_hours_max": self.effort_hours_max,
            "price_basis": self.price_basis.value,
            "price_basis_label": self.price_basis.label,
            "effort_basis": self.effort_basis.value,
            "effort_basis_label": self.effort_basis.label,
            "evidence_backed": self.price_basis.is_evidence_backed,
            "sample_size": self.sample_size,
            "notes": self.notes,
        }


class RateCard:
    """Operator-editable commercial parameters with inspectable provenance."""

    def __init__(self, repository: WinstonRepository, catalog: Any) -> None:
        self.repository = repository
        self.catalog = catalog

    def initialize(self, *, seed: bool = True) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.executescript(RATECARD_SCHEMA)
        if seed:
            for entry in STARTER_RATE_CARD:
                if self.catalog.get(entry["slug"]) and self.get(entry["slug"]) is None:
                    # Seeded disabled and explicitly labelled an assumption.
                    self.upsert(dict(entry), actor="seed", enable=False)

    # ── read ─────────────────────────────────────────────────────────────

    @staticmethod
    def _hydrate(row: Any) -> RateEntry:
        record = dict(row)
        return RateEntry(
            slug=record["slug"], enabled=bool(record["enabled"]),
            price_floor_usd=record["price_floor_usd"],
            price_target_usd=record["price_target_usd"],
            price_premium_usd=record["price_premium_usd"],
            minimum_engagement_usd=record["minimum_engagement_usd"],
            recurring_monthly_usd=record["recurring_monthly_usd"],
            effort_hours_min=record["effort_hours_min"],
            effort_hours_max=record["effort_hours_max"],
            price_basis=Basis(record["price_basis"]),
            effort_basis=Basis(record["effort_basis"]),
            sample_size=record["sample_size"], notes=record["notes"])

    def get(self, slug: str) -> RateEntry | None:
        with self.repository.read() as connection:
            row = connection.execute("SELECT * FROM rate_card WHERE slug=?", (slug,)).fetchone()
        return self._hydrate(row) if row else None

    def list(self, *, enabled_only: bool = False) -> list[RateEntry]:
        clause = " WHERE enabled=1" if enabled_only else ""
        with self.repository.read() as connection:
            rows = connection.execute(f"SELECT * FROM rate_card{clause} ORDER BY slug").fetchall()
        return [self._hydrate(row) for row in rows]

    def revisions(self, slug: str, limit: int = 25) -> list[dict[str, Any]]:
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT changes_json, actor, created_at FROM rate_card_revisions
                   WHERE slug=? ORDER BY created_at DESC LIMIT ?""", (slug, limit)).fetchall()
        return [{"changes": json.loads(r["changes_json"]), "actor": r["actor"],
                 "created_at": r["created_at"]} for r in rows]

    # ── write ────────────────────────────────────────────────────────────

    def upsert(self, payload: dict[str, Any], *, actor: str = "operator",
               enable: bool | None = None) -> RateEntry:
        """Create or edit a rate. Edited numbers revert to operator assumption.

        An operator overriding a calibrated price is asserting a judgement rather than
        reporting a measurement, so the basis drops. Claiming otherwise would let a
        hand-typed number inherit the authority of real data.
        """
        slug = (payload.get("slug") or "").strip()
        if not slug:
            raise ValueError("slug is required")
        if self.catalog.get(slug) is None:
            raise KeyError(f"No catalogue entry for {slug!r}")

        existing = self.get(slug)
        merged: dict[str, Any] = existing.as_dict() if existing else {}
        merged.update({k: v for k, v in payload.items() if k != "slug"})

        floor = merged.get("price_floor_usd")
        target = merged.get("price_target_usd")
        premium = merged.get("price_premium_usd")
        values = [v for v in (floor, target, premium) if v is not None]
        if any(v < 0 for v in values):
            raise ValueError("prices cannot be negative")
        if floor is not None and target is not None and floor > target:
            raise ValueError("price_floor_usd cannot exceed price_target_usd")
        if target is not None and premium is not None and target > premium:
            raise ValueError("price_target_usd cannot exceed price_premium_usd")

        low, high = merged.get("effort_hours_min"), merged.get("effort_hours_max")
        if low is not None and high is not None and float(low) > float(high):
            raise ValueError("effort_hours_min cannot exceed effort_hours_max")

        price_touched = any(f in payload for f in PRICE_FIELDS)
        effort_touched = any(f in payload for f in EFFORT_FIELDS)
        price_basis = (Basis.OPERATOR_ASSUMPTION.value if price_touched
                       else merged.get("price_basis", Basis.OPERATOR_ASSUMPTION.value))
        effort_basis = (Basis.OPERATOR_ASSUMPTION.value if effort_touched
                        else merged.get("effort_basis", Basis.OPERATOR_ASSUMPTION.value))
        enabled = merged.get("enabled", False) if enable is None else enable

        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO rate_card(slug,enabled,price_floor_usd,price_target_usd,
                       price_premium_usd,minimum_engagement_usd,recurring_monthly_usd,
                       effort_hours_min,effort_hours_max,price_basis,effort_basis,
                       sample_size,notes,updated_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(slug) DO UPDATE SET
                       enabled=excluded.enabled,
                       price_floor_usd=excluded.price_floor_usd,
                       price_target_usd=excluded.price_target_usd,
                       price_premium_usd=excluded.price_premium_usd,
                       minimum_engagement_usd=excluded.minimum_engagement_usd,
                       recurring_monthly_usd=excluded.recurring_monthly_usd,
                       effort_hours_min=excluded.effort_hours_min,
                       effort_hours_max=excluded.effort_hours_max,
                       price_basis=excluded.price_basis,
                       effort_basis=excluded.effort_basis,
                       notes=excluded.notes, updated_by=excluded.updated_by,
                       updated_at=excluded.updated_at""",
                (slug, int(bool(enabled)), floor, target, premium,
                 merged.get("minimum_engagement_usd"), merged.get("recurring_monthly_usd"),
                 low, high, price_basis, effort_basis, merged.get("sample_size", 0),
                 merged.get("notes", ""), actor,
                 existing and now or now, now))
            connection.execute(
                "INSERT INTO rate_card_revisions(slug,changes_json,actor,created_at) VALUES(?,?,?,?)",
                (slug, json.dumps({k: v for k, v in payload.items() if k != "slug"}, default=str),
                 actor, now))
        return self.get(slug)  # type: ignore[return-value]

    def enable(self, slug: str, *, enabled: bool = True, actor: str = "operator") -> RateEntry:
        """Activating a rate is separate from having one.

        The pricing engine will not quote a disabled service even when a price exists,
        because a starting number is not a decision to sell.
        """
        entry = self.get(slug)
        if entry is None:
            raise KeyError(f"No rate card entry for {slug!r}")
        if enabled and not (entry.has_price and entry.has_effort):
            raise ValueError(
                f"{slug!r} needs both a target price and an effort range before it can "
                "be enabled")
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute("UPDATE rate_card SET enabled=?,updated_at=? WHERE slug=?",
                               (int(enabled), now, slug))
            connection.execute(
                "INSERT INTO rate_card_revisions(slug,changes_json,actor,created_at) VALUES(?,?,?,?)",
                (slug, json.dumps({"enabled": enabled}), actor, now))
        return self.get(slug)  # type: ignore[return-value]

    def calibrate_from_outcomes(self, slug: str) -> dict[str, Any]:
        """Raise a basis only when enough closed engagements exist to justify it.

        The only path from assumption to evidence. It currently refuses for every
        service, because Winston has recorded no won or lost deals at all.
        """
        with self.repository.read() as connection:
            try:
                row = connection.execute(
                    """SELECT COUNT(*) n, AVG(d.amount_usd) avg_amount
                       FROM deals d JOIN proposals p ON p.id = d.proposal_id
                       WHERE d.status='won' AND p.offer_summary LIKE ?""",
                    (f"%{slug}%",)).fetchone()
            except Exception:
                row = None
        sample = (row["n"] if row else 0) or 0
        if sample < CALIBRATION_MINIMUM:
            return {
                "slug": slug, "calibrated": False, "sample_size": sample,
                "required": CALIBRATION_MINIMUM,
                "reason": f"{sample} closed engagement(s) recorded; {CALIBRATION_MINIMUM} "
                          "are required before a price can claim to be evidence backed.",
            }
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE rate_card SET price_basis=?,sample_size=?,updated_at=? WHERE slug=?""",
                (Basis.HISTORICAL.value, sample, now, slug))
        return {"slug": slug, "calibrated": True, "sample_size": sample,
                "observed_average_usd": round(float(row["avg_amount"] or 0), 2)}

    # ── reporting ────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Deliberately blunt about what is assumption versus evidence."""
        entries = self.list()
        enabled = [e for e in entries if e.enabled]
        evidence_backed = [e for e in entries if e.price_basis.is_evidence_backed]
        return {
            "entries": len(entries),
            "enabled": len(enabled),
            "enabled_slugs": [e.slug for e in enabled],
            "evidence_backed": len(evidence_backed),
            "all_prices_are_assumptions": not evidence_backed,
            "by_basis": {
                basis.value: sum(1 for e in entries if e.price_basis is basis)
                for basis in Basis
            },
            "warning": (
                "Every price is an operator assumption. None is derived from a completed "
                "engagement, because Winston has recorded no won or lost deals."
                if not evidence_backed else None),
        }
