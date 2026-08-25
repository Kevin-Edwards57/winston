"""Commercial event model — the record of what outreach actually produced.

Winston sent 139 emails before this module existed and could not answer a single
commercial question about them: who replied, who met, who bought. That is the gap
between an outreach tool and a system that learns.

The model is event-sourced along the real commercial funnel:

    campaign -> message -> message_event -> reply -> meeting -> proposal -> deal -> revenue

Two principles hold throughout.

**Nothing is inferred.** A row exists only when something observably happened. The
backfill of historical sends records that they were sent and nothing more; it does
not invent delivery, opens, or replies. An absent row means "unknown", never "no".

**Everything carries provenance.** Every fact records where it came from, when it was
recorded, and — where a machine judged rather than observed — how confident that
judgement was. Training data with unknown origin is worse than no training data.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

from .repository import WinstonRepository, normalize_email, stable_id, utc_now

# Delivery lifecycle. Ordered loosely by how far a message got.
# Where a message came from, and therefore what it may be used for.
#
# The 139 rows migrated from followups.json were real emails the legacy script sent,
# but they carry no evidence chain: no observed problem, no offer, no proof, no price,
# no Guardian verdict. As training data they are 139 outcomes with no features, which
# is worse than no data because it looks like data. Eligibility is therefore a stored
# property enforced in queries, not a convention someone has to remember.
PROVENANCE_LEGACY = "legacy_backfill"
PROVENANCE_PRODUCTION = "winston_production"

# Only production sends carry the reasoning a learner would need.
LEARNER_ELIGIBLE_PROVENANCE = frozenset({PROVENANCE_PRODUCTION})

MESSAGE_EVENTS = (
    "queued", "sent", "delivered", "deferred", "bounced_soft", "bounced_hard",
    "complained", "unsubscribed", "opened", "clicked", "failed",
)

# Events that mean this address must never be contacted again.
SUPPRESSING_EVENTS = {"bounced_hard", "complained", "unsubscribed"}

REPLY_SENTIMENTS = ("positive", "neutral", "negative", "auto_reply", "out_of_office", "unknown")
DEAL_STATUSES = ("open", "won", "lost")
PROPOSAL_STATUSES = ("draft", "sent", "accepted", "rejected", "expired")

DEFAULT_LOSS_REASONS = (
    ("price", "Price too high", "commercial"),
    ("budget", "No budget available", "commercial"),
    ("timing", "Wrong timing", "timing"),
    ("no_response", "Went silent", "engagement"),
    ("not_a_fit", "Not a fit for YardLink services", "qualification"),
    ("competitor", "Chose a competitor", "competitive"),
    ("went_internal", "Building it in-house", "competitive"),
    ("unreachable", "Could not reach a decision maker", "engagement"),
    ("no_pain", "No problem worth solving", "qualification"),
    ("other", "Other", "other"),
)

COMMERCIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    objective TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'email',
    status TEXT NOT NULL DEFAULT 'active',
    started_at TEXT,
    ended_at TEXT,
    source TEXT NOT NULL DEFAULT 'winston',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One row per outbound message. draft_id/send_job_id tie it back to the
-- state machine; they are NULL for historical sends that predate it.
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    campaign_id TEXT REFERENCES campaigns(id),
    contact_id TEXT REFERENCES contacts(id),
    draft_id TEXT REFERENCES drafts(id),
    send_job_id TEXT REFERENCES send_jobs(id),
    channel TEXT NOT NULL DEFAULT 'email',
    direction TEXT NOT NULL DEFAULT 'outbound',
    to_email TEXT NOT NULL DEFAULT '',
    normalized_email TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    sent_at TEXT,
    source TEXT NOT NULL,
    source_record_id TEXT,
    provenance TEXT NOT NULL DEFAULT 'legacy_backfill',
    learner_eligible INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(source, source_record_id)
);
CREATE INDEX IF NOT EXISTS messages_contact ON messages(contact_id);
CREATE INDEX IF NOT EXISTS messages_provenance ON messages(provenance, learner_eligible);
CREATE INDEX IF NOT EXISTS messages_campaign ON messages(campaign_id);
CREATE INDEX IF NOT EXISTS messages_email ON messages(normalized_email);
CREATE INDEX IF NOT EXISTS messages_sent_at ON messages(sent_at DESC);

-- Delivery lifecycle. Append-only; the same event never lands twice.
CREATE TABLE IF NOT EXISTS message_events (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(message_id, event_type, occurred_at)
);
CREATE INDEX IF NOT EXISTS message_events_message ON message_events(message_id);
CREATE INDEX IF NOT EXISTS message_events_type ON message_events(event_type, occurred_at DESC);

-- Inbound replies. sentiment is a judgement, so it carries its own provenance.
CREATE TABLE IF NOT EXISTS replies (
    id TEXT PRIMARY KEY,
    message_id TEXT REFERENCES messages(id),
    contact_id TEXT REFERENCES contacts(id),
    campaign_id TEXT REFERENCES campaigns(id),
    from_email TEXT NOT NULL DEFAULT '',
    normalized_email TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    sentiment TEXT NOT NULL DEFAULT 'unknown',
    is_positive INTEGER NOT NULL DEFAULT 0,
    classified_by TEXT NOT NULL DEFAULT 'unclassified',
    classification_confidence REAL,
    source TEXT NOT NULL,
    source_record_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source, source_record_id)
);
CREATE INDEX IF NOT EXISTS replies_contact ON replies(contact_id);
CREATE INDEX IF NOT EXISTS replies_received ON replies(received_at DESC);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    contact_id TEXT REFERENCES contacts(id),
    campaign_id TEXT REFERENCES campaigns(id),
    reply_id TEXT REFERENCES replies(id),
    scheduled_for TEXT,
    occurred INTEGER NOT NULL DEFAULT 0,
    occurred_at TEXT,
    outcome TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meetings_contact ON meetings(contact_id);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    contact_id TEXT REFERENCES contacts(id),
    campaign_id TEXT REFERENCES campaigns(id),
    meeting_id TEXT REFERENCES meetings(id),
    offer_summary TEXT NOT NULL DEFAULT '',
    amount_usd REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL DEFAULT 'draft',
    sent_at TEXT,
    responded_at TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_contact ON proposals(contact_id);

CREATE TABLE IF NOT EXISTS loss_reasons (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id TEXT PRIMARY KEY,
    contact_id TEXT REFERENCES contacts(id),
    campaign_id TEXT REFERENCES campaigns(id),
    proposal_id TEXT REFERENCES proposals(id),
    status TEXT NOT NULL DEFAULT 'open',
    amount_usd REAL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    loss_reason_id TEXT REFERENCES loss_reasons(id),
    loss_notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS deals_contact ON deals(contact_id);
CREATE INDEX IF NOT EXISTS deals_status ON deals(status);

-- Revenue is event-sourced: deposits, milestones, and recurring payments all
-- land here, so a deal's realised value is a SUM rather than a single field
-- someone has to remember to update.
CREATE TABLE IF NOT EXISTS revenue_events (
    id TEXT PRIMARY KEY,
    deal_id TEXT REFERENCES deals(id),
    contact_id TEXT REFERENCES contacts(id),
    amount_usd REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'payment',
    occurred_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    source_record_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source, source_record_id)
);
CREATE INDEX IF NOT EXISTS revenue_events_deal ON revenue_events(deal_id);
CREATE INDEX IF NOT EXISTS revenue_events_occurred ON revenue_events(occurred_at DESC);
"""


class CommercialLedger:
    """Records and reads the commercial funnel.

    Every write is idempotent on a natural key, so replaying an inbox scan or
    re-running a backfill converges instead of duplicating.
    """

    def __init__(self, repository: WinstonRepository) -> None:
        self.repository = repository

    # ── setup ────────────────────────────────────────────────────────────

    # Additive migration for ledgers created before provenance existed.
    NEW_MESSAGE_COLUMNS = (
        ("provenance", "TEXT NOT NULL DEFAULT 'legacy_backfill'"),
        ("learner_eligible", "INTEGER NOT NULL DEFAULT 0"),
    )

    def initialize(self) -> None:
        with self.repository.transaction(immediate=True) as connection:
            # Columns first: the schema script indexes them, and CREATE TABLE
            # IF NOT EXISTS will not add a column to a table that already exists.
            existing = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
            if existing:
                for column, definition in self.NEW_MESSAGE_COLUMNS:
                    if column not in existing:
                        connection.execute(
                            f"ALTER TABLE messages ADD COLUMN {column} {definition}")
                # Anything already present predates the production pipeline.
                connection.execute(
                    "UPDATE messages SET provenance=?, learner_eligible=0 "
                    "WHERE source LIKE 'backfill:%'", (PROVENANCE_LEGACY,))
            connection.executescript(COMMERCIAL_SCHEMA)
            for code, label, category in DEFAULT_LOSS_REASONS:
                connection.execute(
                    """INSERT INTO loss_reasons(id,code,label,category,created_at)
                       VALUES(?,?,?,?,?) ON CONFLICT(code) DO NOTHING""",
                    (stable_id("loss_reason", code), code, label, category, utc_now()),
                )

    # ── campaigns ────────────────────────────────────────────────────────

    def ensure_campaign(self, slug: str, name: str = "", *, objective: str = "",
                        channel: str = "email", source: str = "winston") -> str:
        campaign_id = stable_id("campaign", slug)
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO campaigns(id,name,slug,objective,channel,status,started_at,
                                         source,created_at,updated_at)
                   VALUES(?,?,?,?,?,'active',?,?,?,?)
                   ON CONFLICT(slug) DO NOTHING""",
                (campaign_id, name or slug, slug, objective, channel, now, source, now, now),
            )
            row = connection.execute("SELECT id FROM campaigns WHERE slug=?", (slug,)).fetchone()
        return str(row["id"])

    # ── messages ─────────────────────────────────────────────────────────

    def record_message(self, *, contact_id: str | None, to_email: str, subject: str, body: str,
                       campaign_id: str | None = None, draft_id: str | None = None,
                       send_job_id: str | None = None, channel: str = "email",
                       sent_at: str | None = None, source: str = "winston",
                       source_record_id: str | None = None,
                       provenance: str = PROVENANCE_LEGACY) -> str:
        """Record an outbound message. Returns the existing id when already recorded.

        Provenance defaults to legacy, so a caller that does not think about it creates
        a record excluded from learning. An absent value must never mean eligible.
        """
        source_record_id = source_record_id or send_job_id or str(uuid.uuid4())
        message_id = stable_id("message", source, source_record_id)
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO messages(id,campaign_id,contact_id,draft_id,send_job_id,channel,
                                        direction,to_email,normalized_email,subject,body,sent_at,
                                        source,source_record_id,provenance,learner_eligible,
                                        created_at)
                   VALUES(?,?,?,?,?,?,'outbound',?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source, source_record_id) DO NOTHING""",
                (message_id, campaign_id, contact_id, draft_id, send_job_id, channel,
                 to_email, normalize_email(to_email), subject, body, sent_at,
                 source, source_record_id, provenance,
                 int(provenance in LEARNER_ELIGIBLE_PROVENANCE), now),
            )
            row = connection.execute(
                "SELECT id FROM messages WHERE source=? AND source_record_id=?",
                (source, source_record_id),
            ).fetchone()
        return str(row["id"])

    def record_message_event(self, message_id: str, event_type: str, *, occurred_at: str | None = None,
                             detail: dict[str, Any] | None = None, source: str = "winston") -> str:
        """Append a delivery event. Hard bounces, complaints, and unsubscribes suppress."""
        if event_type not in MESSAGE_EVENTS:
            raise ValueError(f"Unknown message event: {event_type}")
        occurred_at = occurred_at or utc_now()
        event_id = stable_id("message_event", message_id, event_type, occurred_at)
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO message_events(id,message_id,event_type,occurred_at,detail_json,source,created_at)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(message_id,event_type,occurred_at) DO NOTHING""",
                (event_id, message_id, event_type, occurred_at,
                 json.dumps(detail or {}, sort_keys=True), source, utc_now()),
            )
            row = connection.execute(
                "SELECT normalized_email, contact_id FROM messages WHERE id=?", (message_id,)
            ).fetchone()

        # Suppression happens outside the event insert so it gets its own audit trail.
        if event_type in SUPPRESSING_EVENTS and row and row["normalized_email"]:
            self.repository.suppress(row["normalized_email"], f"delivery:{event_type}", row["contact_id"])
        return event_id

    # ── replies ──────────────────────────────────────────────────────────

    def record_reply(self, *, from_email: str, subject: str, body: str, received_at: str,
                     message_id: str | None = None, contact_id: str | None = None,
                     campaign_id: str | None = None, sentiment: str = "unknown",
                     classified_by: str = "unclassified", confidence: float | None = None,
                     source: str = "imap", source_record_id: str | None = None) -> str:
        if sentiment not in REPLY_SENTIMENTS:
            raise ValueError(f"Unknown sentiment: {sentiment}")
        source_record_id = source_record_id or stable_id("reply", from_email, subject, received_at)
        reply_id = stable_id("reply_row", source, source_record_id)
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO replies(id,message_id,contact_id,campaign_id,from_email,normalized_email,
                                       subject,body,received_at,sentiment,is_positive,classified_by,
                                       classification_confidence,source,source_record_id,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source, source_record_id) DO NOTHING""",
                (reply_id, message_id, contact_id, campaign_id, from_email, normalize_email(from_email),
                 subject, body, received_at, sentiment, 1 if sentiment == "positive" else 0,
                 classified_by, confidence, source, source_record_id, utc_now()),
            )
            row = connection.execute(
                "SELECT id FROM replies WHERE source=? AND source_record_id=?",
                (source, source_record_id),
            ).fetchone()
        return str(row["id"])

    # ── funnel beyond the reply ──────────────────────────────────────────

    def record_meeting(self, contact_id: str, *, scheduled_for: str | None = None,
                       occurred: bool = False, occurred_at: str | None = None,
                       campaign_id: str | None = None, reply_id: str | None = None,
                       outcome: str = "", notes: str = "", source: str = "manual") -> str:
        meeting_id = stable_id("meeting", contact_id, scheduled_for or occurred_at or utc_now())
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO meetings(id,contact_id,campaign_id,reply_id,scheduled_for,occurred,
                                        occurred_at,outcome,notes,source,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       occurred=excluded.occurred, occurred_at=excluded.occurred_at,
                       outcome=excluded.outcome, notes=excluded.notes, updated_at=excluded.updated_at""",
                (meeting_id, contact_id, campaign_id, reply_id, scheduled_for, int(occurred),
                 occurred_at, outcome, notes, source, now, now),
            )
        return meeting_id

    def record_proposal(self, contact_id: str, *, amount_usd: float | None, offer_summary: str = "",
                        status: str = "sent", campaign_id: str | None = None,
                        meeting_id: str | None = None, sent_at: str | None = None,
                        source: str = "manual") -> str:
        if status not in PROPOSAL_STATUSES:
            raise ValueError(f"Unknown proposal status: {status}")
        proposal_id = stable_id("proposal", contact_id, offer_summary, str(amount_usd))
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO proposals(id,contact_id,campaign_id,meeting_id,offer_summary,amount_usd,
                                         currency,status,sent_at,source,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,'USD',?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       status=excluded.status, amount_usd=excluded.amount_usd,
                       updated_at=excluded.updated_at""",
                (proposal_id, contact_id, campaign_id, meeting_id, offer_summary, amount_usd,
                 status, sent_at or now, source, now, now),
            )
        return proposal_id

    def open_deal(self, contact_id: str, *, amount_usd: float | None = None,
                  campaign_id: str | None = None, proposal_id: str | None = None,
                  source: str = "manual") -> str:
        deal_id = stable_id("deal", contact_id, proposal_id or "")
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO deals(id,contact_id,campaign_id,proposal_id,status,amount_usd,
                                     opened_at,source,created_at,updated_at)
                   VALUES(?,?,?,?,'open',?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                (deal_id, contact_id, campaign_id, proposal_id, amount_usd, now, source, now, now),
            )
        return deal_id

    def close_deal(self, deal_id: str, *, won: bool, amount_usd: float | None = None,
                   loss_reason_code: str | None = None, loss_notes: str = "") -> None:
        """Close a deal. A loss must say why — unexplained losses teach nothing."""
        if not won and not loss_reason_code:
            raise ValueError("A lost deal requires a loss_reason_code")
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            loss_id = None
            if loss_reason_code:
                row = connection.execute(
                    "SELECT id FROM loss_reasons WHERE code=?", (loss_reason_code,)).fetchone()
                if row is None:
                    raise ValueError(f"Unknown loss reason: {loss_reason_code}")
                loss_id = row["id"]
            changed = connection.execute(
                """UPDATE deals SET status=?, amount_usd=COALESCE(?,amount_usd), closed_at=?,
                                    loss_reason_id=?, loss_notes=?, updated_at=?
                   WHERE id=? AND status='open'""",
                ("won" if won else "lost", amount_usd, now, loss_id, loss_notes, now, deal_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Deal not found or already closed")
        self.repository.add_event("deal.closed", entity_type="deal", entity_id=deal_id,
                                  details={"won": won, "loss_reason": loss_reason_code})

    def record_revenue(self, deal_id: str, amount_usd: float, *, kind: str = "payment",
                       occurred_at: str | None = None, contact_id: str | None = None,
                       notes: str = "", source: str = "manual",
                       source_record_id: str | None = None) -> str:
        occurred_at = occurred_at or utc_now()
        source_record_id = source_record_id or stable_id("revenue", deal_id, kind, occurred_at, str(amount_usd))
        revenue_id = stable_id("revenue_row", source, source_record_id)
        with self.repository.transaction(immediate=True) as connection:
            if contact_id is None:
                row = connection.execute("SELECT contact_id FROM deals WHERE id=?", (deal_id,)).fetchone()
                contact_id = row["contact_id"] if row else None
            connection.execute(
                """INSERT INTO revenue_events(id,deal_id,contact_id,amount_usd,kind,occurred_at,
                                              notes,source,source_record_id,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_record_id) DO NOTHING""",
                (revenue_id, deal_id, contact_id, float(amount_usd), kind, occurred_at,
                 notes, source, source_record_id, utc_now()),
            )
        return revenue_id

    # ── reads ────────────────────────────────────────────────────────────

    def funnel(self, campaign_id: str | None = None) -> dict[str, Any]:
        """The commercial funnel. Absent evidence reads as unknown, not zero."""
        scoped = bool(campaign_id)
        args: tuple = (campaign_id,) if scoped else ()

        with self.repository.read() as c:
            def count(table: str, condition: str = "") -> int:
                clauses = [condition] if condition else []
                if scoped:
                    clauses.append("campaign_id=?")
                where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                return c.execute(f"SELECT COUNT(*) n FROM {table}{where}", args).fetchone()["n"]

            def delivery(kind: str) -> int:
                sql = ("SELECT COUNT(DISTINCT e.message_id) n FROM message_events e "
                       "JOIN messages m ON m.id=e.message_id WHERE e.event_type=?")
                if scoped:
                    sql += " AND m.campaign_id=?"
                return c.execute(sql, (kind, *args)).fetchone()["n"]

            sent = count("messages")
            replies = count("replies")
            positive = count("replies", "is_positive=1")
            meetings = count("meetings", "occurred=1")
            proposals = count("proposals", "status!='draft'")
            won = count("deals", "status='won'")
            lost = count("deals", "status='lost'")
            delivered = delivery("delivered")
            bounced = delivery("bounced_hard") + delivery("bounced_soft")
            revenue = c.execute(
                "SELECT COALESCE(SUM(r.amount_usd),0) t FROM revenue_events r"
                + (" JOIN deals d ON d.id=r.deal_id WHERE d.campaign_id=?" if scoped else ""),
                args).fetchone()["t"]
        # A rate of 0.0 and "we never looked" are different claims, and conflating them
        # is how a system convinces itself a campaign failed when it was simply never
        # measured. Inbox scanning is what makes replies observable, so until it has run,
        # every reply-derived rate reports None rather than a zero nobody earned.
        inbox_scanned = self.repository.get_setting("inbox_last_scanned_at") is not None
        delivery_tracked = (delivered + bounced) > 0

        def rate(numerator: int, observable: bool) -> float | None:
            return round(numerator / sent, 4) if sent and observable else None

        return {
            "sent": sent,
            "delivered": delivered,
            "bounced": bounced,
            "delivery_tracked": delivery_tracked,
            "replies": replies,
            "positive_replies": positive,
            "reply_tracking_enabled": inbox_scanned,
            "meetings": meetings,
            "proposals": proposals,
            "deals_won": won,
            "deals_lost": lost,
            "revenue_usd": round(float(revenue), 2),
            "delivery_rate": rate(delivered, delivery_tracked),
            "reply_rate": rate(replies, inbox_scanned),
            "positive_reply_rate": rate(positive, inbox_scanned),
            "meeting_rate": rate(meetings, inbox_scanned),
            "close_rate": rate(won, inbox_scanned),
            "revenue_per_message": round(float(revenue) / sent, 2) if sent else None,
        }

    def contact_history(self, contact_id: str) -> dict[str, Any]:
        """Everything the ledger knows about one business, in time order."""
        with self.repository.read() as c:
            def rows(sql: str) -> list[dict[str, Any]]:
                return [dict(r) for r in c.execute(sql, (contact_id,))]

            return {
                "messages": rows("SELECT id,subject,sent_at,channel,source FROM messages "
                                 "WHERE contact_id=? ORDER BY sent_at"),
                "replies": rows("SELECT id,subject,received_at,sentiment,classified_by,"
                                "classification_confidence FROM replies WHERE contact_id=? "
                                "ORDER BY received_at"),
                "meetings": rows("SELECT id,scheduled_for,occurred,occurred_at,outcome FROM meetings "
                                 "WHERE contact_id=? ORDER BY COALESCE(occurred_at,scheduled_for)"),
                "proposals": rows("SELECT id,offer_summary,amount_usd,status,sent_at FROM proposals "
                                  "WHERE contact_id=? ORDER BY sent_at"),
                "deals": rows("SELECT id,status,amount_usd,opened_at,closed_at,loss_notes FROM deals "
                              "WHERE contact_id=? ORDER BY opened_at"),
                "revenue": rows("SELECT id,amount_usd,kind,occurred_at FROM revenue_events "
                                "WHERE contact_id=? ORDER BY occurred_at"),
            }

    def learning_dataset(self) -> list[dict[str, Any]]:
        """Messages a learner may train on. Enforced in SQL, not by convention.

        A message qualifies only when it was produced by the production pipeline and
        still carries the draft it came from. Legacy rows fail both conditions.
        """
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT m.id, m.contact_id, m.campaign_id, m.draft_id, m.subject,
                          m.sent_at, m.provenance
                   FROM messages m
                   WHERE m.learner_eligible = 1
                     AND m.provenance = ?
                     AND m.draft_id IS NOT NULL
                   ORDER BY m.sent_at""", (PROVENANCE_PRODUCTION,)).fetchall()
        return [dict(row) for row in rows]

    def dataset_readiness(self) -> dict[str, Any]:
        """Whether there is enough real outcome data to learn anything."""
        with self.repository.read() as connection:
            eligible = connection.execute(
                "SELECT COUNT(*) n FROM messages WHERE learner_eligible=1").fetchone()["n"]
            excluded = connection.execute(
                "SELECT COUNT(*) n FROM messages WHERE learner_eligible=0").fetchone()["n"]
            replies = connection.execute("SELECT COUNT(*) n FROM replies").fetchone()["n"]
            deals = connection.execute("SELECT COUNT(*) n FROM deals").fetchone()["n"]
        return {
            "learner_eligible_messages": eligible,
            "excluded_legacy_messages": excluded,
            "replies": replies, "closed_deals": deals,
            "ml_status": "INSUFFICIENT_DATA" if eligible < 30 or replies == 0 else "REVIEW",
            "reason": (f"{eligible} eligible message(s), {replies} reply/replies, "
                       f"{deals} closed deal(s). {excluded} legacy record(s) excluded "
                       "because they carry no evidence chain."),
        }

    # ── backfill ─────────────────────────────────────────────────────────

    def backfill_from_sent_messages(self, campaign_slug: str = "legacy-2026") -> dict[str, int]:
        """Import historical sends into the ledger.

        Records only what is known: the message existed and was sent. No delivery,
        open, or reply is invented — those stay unknown, which is the honest state.
        """
        campaign_id = self.ensure_campaign(
            campaign_slug, "Legacy outreach (pre-ledger)",
            objective="Historical sends imported for baseline measurement",
            source="backfill",
        )
        with self.repository.read() as c:
            historical = [dict(r) for r in c.execute(
                "SELECT id,contact_id,email,subject,body,sent_at FROM sent_messages ORDER BY sent_at"
            )]

        imported = events = 0
        for row in historical:
            message_id = self.record_message(
                contact_id=row["contact_id"], to_email=row["email"] or "",
                subject=row["subject"] or "", body=row["body"] or "",
                campaign_id=campaign_id, sent_at=row["sent_at"],
                source="backfill:sent_messages", source_record_id=str(row["id"]),
                provenance=PROVENANCE_LEGACY,
            )
            imported += 1
            if row["sent_at"]:
                self.record_message_event(message_id, "sent", occurred_at=row["sent_at"],
                                          detail={"backfilled": True}, source="backfill:sent_messages")
                events += 1

        result = {"messages_imported": imported, "events_recorded": events,
                  "campaign_id": campaign_id, "source_rows": len(historical)}
        self.repository.add_event("ledger.backfilled", details={
            k: v for k, v in result.items() if k != "campaign_id"})
        return result
