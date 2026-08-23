"""Outreach pipeline — the single production path from prospect to reviewable draft.

    research -> problems -> catalogue -> offer -> proof -> Writer -> Guardian -> draft

Before this existed there were two ways to produce an email: the legacy
``write_email()`` in ``winston_app.py``, which described YardLink from a hardcoded
string and knew nothing about the prospect, and the Writer, which knew everything but
was not wired to anything. Two generation paths is one too many, and the wrong one was
in production.

Every draft this creates persists its full reasoning chain in ``draft_intelligence``:
the observations and their evidence, the offer chosen and why, the proof selected and
its relevance score, and Guardian's complete verdict. A human reviewer can answer "why
is Winston sending this email to this business?" without reading source, and the Learner
can later ask which reasoning actually produced revenue.

Guardian's veto is enforced here rather than advised. A blocked draft is written to the
database for inspection but never enters the review queue, because a draft that should
not be sent should not be one click away from being approved.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .catalog import Catalog
from .fit import FitEngine
from .guardian import Guardian
from .repository import WinstonRepository, utc_now
from .signals import SignalStore
from .writer import Draft, Writer

PIPELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS draft_intelligence (
    draft_id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    status TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT '',
    approved INTEGER NOT NULL DEFAULT 0,
    brief_json TEXT NOT NULL DEFAULT '{}',
    guardian_json TEXT NOT NULL DEFAULT '{}',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS draft_intelligence_contact ON draft_intelligence(contact_id);
CREATE INDEX IF NOT EXISTS draft_intelligence_approved ON draft_intelligence(approved);
"""


@dataclass
class PipelineResult:
    """Outcome of one prospect passing through the pipeline."""
    contact_id: str
    status: str                      # queued | blocked | no_verified_offer | no_evidence | failed
    draft_id: str | None = None
    draft: Draft | None = None
    guardian: dict[str, Any] | None = None
    reason: str = ""

    @property
    def reviewable(self) -> bool:
        return self.status == "queued"

    def as_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id, "status": self.status,
            "draft_id": self.draft_id, "reviewable": self.reviewable,
            "reason": self.reason,
            "subject": self.draft.subject if self.draft else "",
            "body": self.draft.body if self.draft else "",
            "intent": self.draft.intent if self.draft else "",
            "brief": self.draft.brief if self.draft else {},
            "guardian": self.guardian or {},
        }


class OutreachPipeline:
    """The only production route from a prospect to a reviewable draft."""

    def __init__(self, repository: WinstonRepository, catalog: Catalog,
                 signal_store: SignalStore, fit_engine: FitEngine,
                 writer: Writer, guardian: Guardian) -> None:
        self.repository = repository
        self.catalog = catalog
        self.signals = signal_store
        self.fit = fit_engine
        self.writer = writer
        self.guardian = guardian

    def initialize(self) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.executescript(PIPELINE_SCHEMA)

    # ── generation ───────────────────────────────────────────────────────

    def generate(self, contact_id: str) -> PipelineResult:
        """Run one prospect end to end. Persists a draft only if Guardian approves."""
        with self.repository.read() as connection:
            contact_row = connection.execute(
                "SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if contact_row is None:
            raise KeyError(f"Unknown contact {contact_id}")
        contact = dict(contact_row)

        draft = self.writer.write(contact_id)

        # The Writer declining is a legitimate outcome, not an error to route around.
        if draft.status != "drafted":
            self._record(None, contact_id, draft, None, approved=False)
            return PipelineResult(contact_id, draft.status, draft=draft, reason=draft.error)

        verdict = self.guardian.review(subject=draft.subject, body=draft.body,
                                       contact=contact, brief=draft.brief)

        if not verdict.approved:
            # Persisted for inspection, but never queued. A rejected draft must not sit
            # one click away from approval.
            self._record(None, contact_id, draft, verdict.as_dict(), approved=False)
            reasons = "; ".join(i["rule"] for i in verdict.issues)
            self.repository.add_event(
                "draft.blocked", entity_type="contact", entity_id=contact_id,
                details={"rules": [i["rule"] for i in verdict.issues]})
            return PipelineResult(contact_id, "blocked", draft=draft,
                                  guardian=verdict.as_dict(),
                                  reason=f"Guardian blocked: {reasons}")

        draft_id = self.repository.create_draft(contact_id, draft.subject, draft.body)
        self._record(draft_id, contact_id, draft, verdict.as_dict(), approved=True)
        self.repository.add_event("draft.created", entity_type="draft", entity_id=draft_id,
                                  details={"intent": draft.intent,
                                           "offer": (draft.brief.get("recommended_service")
                                                     or {}).get("slug", "")})
        return PipelineResult(contact_id, "queued", draft_id=draft_id, draft=draft,
                              guardian=verdict.as_dict())

    def _record(self, draft_id: str | None, contact_id: str, draft: Draft,
                verdict: dict[str, Any] | None, *, approved: bool) -> None:
        """Preserve the full reasoning chain, whether or not the draft survived."""
        from uuid import uuid4
        row_id = draft_id or f"blocked-{uuid4()}"
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO draft_intelligence(
                       draft_id,contact_id,status,intent,approved,brief_json,guardian_json,
                       provider,model,latency_ms,input_tokens,output_tokens,
                       estimated_cost_usd,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(draft_id) DO UPDATE SET
                       status=excluded.status, approved=excluded.approved,
                       brief_json=excluded.brief_json, guardian_json=excluded.guardian_json""",
                (row_id, contact_id, draft.status, draft.intent, int(approved),
                 json.dumps(draft.brief, default=str), json.dumps(verdict or {}, default=str),
                 draft.provider, draft.model, draft.latency_ms, draft.input_tokens,
                 draft.output_tokens, draft.estimated_cost_usd, utc_now()))

    # ── reads ────────────────────────────────────────────────────────────

    def intelligence_for(self, draft_id: str) -> dict[str, Any] | None:
        """Everything behind one draft, for the review screen."""
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT * FROM draft_intelligence WHERE draft_id=?", (draft_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["brief"] = json.loads(record.pop("brief_json") or "{}")
        record["guardian"] = json.loads(record.pop("guardian_json") or "{}")
        record["approved"] = bool(record["approved"])
        return record

    def blocked(self, limit: int = 50) -> list[dict[str, Any]]:
        """Drafts Guardian refused, so the reasons are visible rather than silent."""
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT d.draft_id,d.contact_id,d.status,d.guardian_json,d.created_at,
                          c.name,c.email
                   FROM draft_intelligence d JOIN contacts c ON c.id=d.contact_id
                   WHERE d.approved=0 ORDER BY d.created_at DESC LIMIT ?""",
                (limit,)).fetchall()
        blocked = []
        for row in rows:
            verdict = json.loads(row["guardian_json"] or "{}")
            blocked.append({
                "draft_id": row["draft_id"], "contact_id": row["contact_id"],
                "business": row["name"], "status": row["status"],
                "created_at": row["created_at"],
                "issues": [i["rule"] for i in verdict.get("issues", [])],
            })
        return blocked

    def stats(self) -> dict[str, Any]:
        with self.repository.read() as connection:
            rows = connection.execute(
                "SELECT status, approved, COUNT(*) n FROM draft_intelligence "
                "GROUP BY status, approved").fetchall()
            cost = connection.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd),0) c FROM draft_intelligence"
            ).fetchone()["c"]
        by_status: dict[str, int] = {}
        approved = blocked = 0
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + row["n"]
            if row["approved"]:
                approved += row["n"]
            else:
                blocked += row["n"]
        return {"by_status": by_status, "approved": approved, "blocked": blocked,
                "total": approved + blocked, "ai_cost_usd": round(float(cost), 4)}
