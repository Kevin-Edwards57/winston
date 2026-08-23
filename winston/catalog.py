"""YardLink Studio knowledge base — what we sell, and what merely proves we can build.

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

LIST_FIELDS = ("industries", "problems_solved", "capabilities", "limitations", "integrations")

SCALAR_FIELDS = (
    "name", "kind", "status", "verified", "description", "ideal_customer",
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
    {"slug": "yardlink-eats", "name": "YardLink Eats", "kind": "PRODUCT",
     "status": "EXPERIMENTAL", "verified": 0,
     "notes": "Named by the operator as a product. Capabilities, status, pricing, and "
              "target industries are unconfirmed and must be filled in before Winston "
              "may recommend it."},
    {"slug": "wedlink", "name": "WedLink", "kind": "PRODUCT",
     "status": "EXPERIMENTAL", "verified": 0,
     "notes": "Named by the operator as a product, referenced in a wedding-venue example. "
              "Capabilities and pricing unconfirmed."},
    {"slug": "guardlink", "name": "GuardLink", "kind": "PRODUCT",
     "status": "EXPERIMENTAL", "verified": 0,
     "notes": "Named by the operator as a product, referenced in a security-company "
              "workforce-management example. Capabilities and pricing unconfirmed."},
    {"slug": "otonia", "name": "Otonia", "kind": "PORTFOLIO",
     "status": "PORTFOLIO_ONLY", "verified": 0,
     "notes": "Cited as proof of consumer mobile development and polished UX. Explicitly "
              "NOT to be pitched as a product."},
    {"slug": "susan", "name": "Susan", "kind": "PORTFOLIO",
     "status": "PORTFOLIO_ONLY", "verified": 0,
     "notes": "Separate job-application automation project. Portfolio evidence only."},
    {"slug": "winston", "name": "Winston", "kind": "INTERNAL_TOOL",
     "status": "INTERNAL_TOOL", "verified": 0,
     "notes": "YardLink's own commercial intelligence platform. May be cited as proof of "
              "AI automation and data engineering capability. Not sellable unless the "
              "operator explicitly reclassifies it."},
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

    def initialize(self, *, seed: bool = True) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.executescript(CATALOG_SCHEMA)
        if seed:
            for entry in SEED_ENTRIES:
                if self.get(entry["slug"]) is None:
                    self.upsert(dict(entry), actor="seed")

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
        entry["sellable"] = entry["status"] in SELLABLE_STATUSES and entry["verified"]
        entry["citable_as_proof"] = entry["kind"] in PROOF_KINDS
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

    def proof_for(self, slug: str) -> list[dict[str, Any]]:
        """Entries linked as evidence that YardLink can deliver this one."""
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT e.* FROM catalog_links l JOIN catalog_entries e ON e.slug = l.to_slug
                   WHERE l.from_slug=? AND l.relation='proves'""", (slug,)).fetchall()
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
        sellable = [e for e in entries if e["sellable"]]
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
            "sellable": len(sellable),
            "sellable_slugs": [e["slug"] for e in sellable],
            "awaiting_verification": awaiting,
            "can_recommend": bool(sellable),
        }
