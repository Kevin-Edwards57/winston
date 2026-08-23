"""SQLite persistence and transactional workflow primitives for Winston."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA_VERSION = 1
VALID_DRAFT_STAGES = ("draft", "reviewed", "approved", "queued", "confirmed", "sent", "rejected")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(value: str | None) -> str:
    return (value or "").strip().casefold()


def stable_id(namespace: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"winston:{namespace}:{material}"))


class WinstonRepository:
    """Small repository layer; every write is an explicit SQLite transaction."""

    def __init__(self, database_path: str | Path = "winston.db") -> None:
        self.database_path = Path(database_path)
        self._local = threading.local()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction(immediate=True) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migration_runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    report_json TEXT
                );
                CREATE TABLE IF NOT EXISTS legacy_import_records (
                    id TEXT PRIMARY KEY,
                    source_file TEXT NOT NULL,
                    source_index INTEGER NOT NULL,
                    record_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(source_file, source_index, record_hash)
                );
                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY,
                    place_id TEXT,
                    normalized_email TEXT,
                    email TEXT,
                    name TEXT NOT NULL DEFAULT '',
                    business_type TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    website TEXT NOT NULL DEFAULT '',
                    instagram TEXT NOT NULL DEFAULT '',
                    facebook TEXT NOT NULL DEFAULT '',
                    tiktok TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS contacts_place_id_unique
                    ON contacts(place_id) WHERE place_id IS NOT NULL AND place_id != '';
                CREATE UNIQUE INDEX IF NOT EXISTS contacts_email_unique
                    ON contacts(normalized_email) WHERE normalized_email IS NOT NULL AND normalized_email != '';
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    contact_id TEXT NOT NULL REFERENCES contacts(id),
                    channel TEXT NOT NULL DEFAULT 'email',
                    subject TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'draft' CHECK(stage IN ('draft','reviewed','approved','queued','confirmed','sent','rejected')),
                    version INTEGER NOT NULL DEFAULT 1,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS suppressions (
                    id TEXT PRIMARY KEY,
                    contact_id TEXT REFERENCES contacts(id),
                    normalized_email TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(normalized_email)
                );
                CREATE TABLE IF NOT EXISTS send_jobs (
                    id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL REFERENCES drafts(id),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('queued','confirmed','sending','sent','failed','cancelled')),
                    locked_at TEXT,
                    locked_by TEXT,
                    sent_at TEXT,
                    provider_message_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sent_messages (
                    id TEXT PRIMARY KEY,
                    contact_id TEXT REFERENCES contacts(id),
                    email TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    sent_at TEXT,
                    followup_sent INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    source_record_id TEXT,
                    UNIQUE(source, source_record_id)
                );
                CREATE TABLE IF NOT EXISTS activity_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    entity_type TEXT,
                    entity_id TEXT,
                    actor TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS activity_events_created_at ON activity_events(created_at DESC);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_usage (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS provider_usage_created_at ON provider_usage(created_at DESC);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings(key, value_json, updated_at) VALUES ('automatic_followups_enabled', 'false', ?)",
                (utc_now(),),
            )

    def record_legacy_row(self, source_file: str, source_index: int, payload: Any) -> tuple[str, bool]:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        record_id = stable_id("legacy", source_file, source_index, digest)
        with self.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO legacy_import_records
                   (id, source_file, source_index, record_hash, payload_json, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (record_id, source_file, source_index, digest, canonical, utc_now()),
            )
            return record_id, cursor.rowcount == 1

    def upsert_contact(self, payload: dict[str, Any], source: str) -> tuple[str, bool]:
        email = (payload.get("email") or "").strip()
        normalized = normalize_email(email)
        place_id = (payload.get("place_id") or "").strip()
        social = payload.get("social") if isinstance(payload.get("social"), dict) else {}
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            place_row = None
            email_row = None
            if place_id:
                place_row = connection.execute("SELECT * FROM contacts WHERE place_id = ?", (place_id,)).fetchone()
            if normalized:
                email_row = connection.execute("SELECT * FROM contacts WHERE normalized_email = ?", (normalized,)).fetchone()
            if place_row and email_row and place_row["id"] != email_row["id"]:
                # Two legacy identities converged. Preserve the Place-backed row,
                # repoint dependent records, and merge the email-backed row.
                winner_id, loser_id = place_row["id"], email_row["id"]
                connection.execute("UPDATE drafts SET contact_id=? WHERE contact_id=?", (winner_id, loser_id))
                connection.execute("UPDATE sent_messages SET contact_id=? WHERE contact_id=?", (winner_id, loser_id))
                connection.execute("UPDATE suppressions SET contact_id=? WHERE contact_id=?", (winner_id, loser_id))
                connection.execute("DELETE FROM contacts WHERE id=?", (loser_id,))
                place_row = connection.execute("SELECT * FROM contacts WHERE id=?", (winner_id,)).fetchone()
                # Fill any blank incoming fields from the merged legacy identity.
                payload = {**dict(email_row), **{key: value for key, value in payload.items() if value}}
            row = place_row or email_row
            values = {
                "place_id": place_id,
                "normalized_email": normalized,
                "email": email,
                "name": payload.get("name") or "",
                "business_type": payload.get("type") or payload.get("business_type") or "",
                "address": payload.get("address") or "",
                "phone": payload.get("phone") or "",
                "website": payload.get("website") or "",
                "instagram": payload.get("instagram") or social.get("instagram") or "",
                "facebook": payload.get("facebook") or social.get("facebook") or "",
                "tiktok": payload.get("tiktok") or social.get("tiktok") or "",
            }
            if row:
                merged = {key: values[key] or row[key] for key in values}
                # A contact's first stable Places identity wins; another business
                # sharing an email must not rewrite it and orphan the stable ID.
                merged["place_id"] = row["place_id"] or values["place_id"]
                connection.execute(
                    """UPDATE contacts SET place_id=?, normalized_email=?, email=?, name=?, business_type=?,
                       address=?, phone=?, website=?, instagram=?, facebook=?, tiktok=?, updated_at=? WHERE id=?""",
                    (*merged.values(), now, row["id"]),
                )
                return str(row["id"]), False
            contact_id = stable_id("contact", place_id or normalized or payload.get("name", ""), payload.get("address", ""))
            existing_id = connection.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
            if existing_id:
                merged = {key: values[key] or existing_id[key] for key in values}
                merged["place_id"] = existing_id["place_id"] or values["place_id"]
                connection.execute(
                    """UPDATE contacts SET place_id=?, normalized_email=?, email=?, name=?, business_type=?,
                       address=?, phone=?, website=?, instagram=?, facebook=?, tiktok=?, updated_at=? WHERE id=?""",
                    (*merged.values(), now, contact_id),
                )
                return contact_id, False
            connection.execute(
                """INSERT INTO contacts(id, place_id, normalized_email, email, name, business_type, address,
                   phone, website, instagram, facebook, tiktok, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (contact_id, *values.values(), source, now, now),
            )
            return contact_id, True

    def add_event(self, event_type: str, *, entity_type: str | None = None,
                  entity_id: str | None = None, actor: str = "system",
                  details: dict[str, Any] | None = None) -> str:
        event_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO activity_events(id,event_type,entity_type,entity_id,actor,details_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (event_id, event_type, entity_type, entity_id, actor,
                 json.dumps(details or {}, sort_keys=True), utc_now()),
            )
        return event_id

    def create_draft(self, contact_id: str, subject: str, body: str, channel: str = "email") -> str:
        if channel not in {"email", "instagram", "facebook"}:
            raise ValueError("Unsupported draft channel")
        draft_id = str(uuid.uuid4())
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO drafts(id,contact_id,channel,subject,body,stage,created_at,updated_at)
                   VALUES(?,?,?,?,?,'draft',?,?)""",
                (draft_id, contact_id, channel, subject.strip(), body.strip(), now, now),
            )
        self.add_event("draft.created", entity_type="draft", entity_id=draft_id)
        return draft_id

    def transition_draft(self, draft_id: str, target_stage: str, actor: str = "user") -> None:
        allowed = {
            "draft": {"reviewed", "rejected"}, "reviewed": {"draft", "approved", "rejected"},
            "approved": {"reviewed", "queued"}, "queued": {"approved", "confirmed"},
            "confirmed": {"queued", "sent"}, "sent": set(), "rejected": {"draft"},
        }
        if target_stage not in VALID_DRAFT_STAGES:
            raise ValueError("Invalid draft stage")
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT stage FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            if row is None:
                raise KeyError("Draft not found")
            if target_stage not in allowed[row["stage"]]:
                raise ValueError(f"Invalid transition: {row['stage']} -> {target_stage}")
            connection.execute("UPDATE drafts SET stage=?, updated_at=? WHERE id=?", (target_stage, utc_now(), draft_id))
        self.add_event(f"draft.{target_stage}", entity_type="draft", entity_id=draft_id, actor=actor)

    def queue_draft(self, draft_id: str) -> tuple[str, bool]:
        """Queue an approved draft once; repeated calls return the same job."""
        with self.transaction(immediate=True) as connection:
            draft = connection.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                raise KeyError("Draft not found")
            if draft["stage"] not in {"approved", "queued"}:
                raise ValueError("Only approved drafts can be queued")
            key = stable_id("send", draft_id, draft["version"])
            existing = connection.execute("SELECT id FROM send_jobs WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                return str(existing["id"]), False
            job_id = str(uuid.uuid4())
            now = utc_now()
            connection.execute(
                """INSERT INTO send_jobs(id,draft_id,idempotency_key,status,created_at,updated_at)
                   VALUES(?,?,?,'queued',?,?)""", (job_id, draft_id, key, now, now),
            )
            connection.execute("UPDATE drafts SET stage='queued',updated_at=? WHERE id=?", (now, draft_id))
        self.add_event("send.queued", entity_type="send_job", entity_id=job_id)
        return job_id, True

    def confirm_send(self, job_id: str) -> None:
        with self.transaction(immediate=True) as connection:
            job = connection.execute("SELECT status,draft_id FROM send_jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError("Send job not found")
            if job["status"] == "confirmed":
                return
            if job["status"] != "queued":
                raise ValueError("Only queued jobs can be confirmed")
            now = utc_now()
            connection.execute("UPDATE send_jobs SET status='confirmed',updated_at=? WHERE id=?", (now, job_id))
            connection.execute("UPDATE drafts SET stage='confirmed',updated_at=? WHERE id=?", (now, job["draft_id"]))
        self.add_event("send.confirmed", entity_type="send_job", entity_id=job_id, actor="user")

    def claim_send(self, job_id: str, worker_id: str) -> dict[str, Any] | None:
        """Atomically claim one confirmed job; returns None if already claimed/sent."""
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                """SELECT j.*,d.subject,d.body,d.contact_id,c.email,c.name
                   FROM send_jobs j JOIN drafts d ON d.id=j.draft_id JOIN contacts c ON c.id=d.contact_id
                   WHERE j.id=?""", (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Send job not found")
            if row["status"] != "confirmed":
                return None
            if connection.execute("SELECT 1 FROM suppressions WHERE normalized_email=?",
                                  (normalize_email(row["email"]),)).fetchone():
                connection.execute("UPDATE send_jobs SET status='cancelled',error='suppressed',updated_at=? WHERE id=?",
                                   (utc_now(), job_id))
                return None
            now = utc_now()
            changed = connection.execute(
                """UPDATE send_jobs SET status='sending',locked_at=?,locked_by=?,updated_at=?
                   WHERE id=? AND status='confirmed'""", (now, worker_id, now, job_id),
            ).rowcount
            return dict(row) if changed == 1 else None

    def complete_send(self, job_id: str, *, success: bool, error: str = "") -> None:
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT draft_id,status FROM send_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Send job not found")
            if row["status"] != "sending":
                raise ValueError("Send job is not claimed")
            now = utc_now()
            status = "sent" if success else "failed"
            connection.execute("UPDATE send_jobs SET status=?,sent_at=?,error=?,updated_at=? WHERE id=?",
                               (status, now if success else None, error[:500], now, job_id))
            if success:
                connection.execute("UPDATE drafts SET stage='sent',updated_at=? WHERE id=?", (now, row["draft_id"]))
        self.add_event(f"send.{status}", entity_type="send_job", entity_id=job_id,
                       details={"error": error[:200]} if error else {})

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self.read() as connection:
            row = connection.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            return dict(row) if row else None

    def suppress(self, email: str, reason: str, contact_id: str | None = None) -> str:
        normalized = normalize_email(email)
        if not normalized or "@" not in normalized:
            raise ValueError("A valid email is required")
        suppression_id = stable_id("suppression", normalized)
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO suppressions(id,contact_id,normalized_email,reason,created_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(normalized_email) DO UPDATE SET reason=excluded.reason""",
                (suppression_id, contact_id, normalized, reason.strip() or "do-not-contact", utc_now()),
            )
        self.add_event("contact.suppressed", entity_type="contact", entity_id=contact_id,
                       details={"reason": reason, "email_hash": hashlib.sha256(normalized.encode()).hexdigest()[:12]})
        return suppression_id

    def seed_suppressions_from_history(self, extra_emails: Iterable[str] = ()) -> dict[str, int]:
        """Suppress every address Winston has already contacted.

        Historical outreach predates the current safety model, so those recipients
        are protected by default rather than by remembering to check. Idempotent:
        existing suppressions keep their original reason.
        """
        seeded = skipped = already = 0
        with self.transaction(immediate=True) as connection:
            candidates: list[tuple[str, str | None, str]] = [
                (str(row["email"]), row["contact_id"], "prior-outreach:sent_messages")
                for row in connection.execute(
                    "SELECT email, contact_id FROM sent_messages WHERE email IS NOT NULL AND email != ''"
                )
            ]
            candidates += [(str(e), None, "prior-outreach:emailed.json") for e in extra_emails]

            for email, contact_id, reason in candidates:
                normalized = normalize_email(email)
                if not normalized or "@" not in normalized:
                    skipped += 1
                    continue
                changed = connection.execute(
                    """INSERT INTO suppressions(id,contact_id,normalized_email,reason,created_at)
                       VALUES(?,?,?,?,?) ON CONFLICT(normalized_email) DO NOTHING""",
                    (stable_id("suppression", normalized), contact_id, normalized, reason, utc_now()),
                ).rowcount
                if changed:
                    seeded += 1
                else:
                    already += 1

        result = {"seeded": seeded, "already_suppressed": already, "invalid": skipped}
        self.add_event("suppressions.seeded", details=result)
        return result

    def is_suppressed(self, email: str) -> bool:
        with self.read() as connection:
            row = connection.execute("SELECT 1 FROM suppressions WHERE normalized_email=?", (normalize_email(email),)).fetchone()
            return row is not None

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.read() as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def counts(self) -> dict[str, int]:
        tables = ("contacts", "legacy_import_records", "drafts", "sent_messages", "suppressions", "activity_events", "provider_usage")
        with self.read() as connection:
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}

    def record_provider_usage(self, *, provider: str, model: str, purpose: str,
                              success: bool, latency_ms: int, input_tokens: int = 0,
                              output_tokens: int = 0, estimated_cost_usd: float = 0,
                              error: str = "") -> str:
        usage_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO provider_usage
                   (id,provider,model,purpose,success,latency_ms,input_tokens,output_tokens,
                    estimated_cost_usd,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (usage_id, provider, model, purpose, int(success), max(0, latency_ms),
                 max(0, input_tokens), max(0, output_tokens), max(0, estimated_cost_usd),
                 error[:500], utc_now()),
            )
        return usage_id

    def provider_summary(self) -> dict[str, Any]:
        with self.read() as connection:
            rows = connection.execute(
                """SELECT provider,COUNT(*) calls,SUM(success) successes,
                   SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,
                   SUM(estimated_cost_usd) estimated_cost_usd,AVG(latency_ms) avg_latency_ms
                   FROM provider_usage GROUP BY provider ORDER BY provider"""
            ).fetchall()
        return {row["provider"]: dict(row) for row in rows}

    def workflow_counts(self) -> dict[str, int]:
        with self.read() as connection:
            rows = connection.execute("SELECT stage,COUNT(*) count FROM drafts GROUP BY stage").fetchall()
        counts = {stage: 0 for stage in VALID_DRAFT_STAGES}
        counts.update({row["stage"]: row["count"] for row in rows})
        return counts

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self.read() as connection:
            rows = connection.execute(
                "SELECT * FROM activity_events ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["details"] = json.loads(event.pop("details_json") or "{}")
            events.append(event)
        return events

    def draft_candidates(self, limit: int = 10) -> list[dict[str, Any]]:
        """Contacts safe to draft: emailable, unsuppressed, unsent, and not already drafted."""
        limit = max(1, min(int(limit), 50))
        with self.read() as connection:
            rows = connection.execute(
                """SELECT c.* FROM contacts c
                   WHERE c.normalized_email IS NOT NULL AND c.normalized_email != ''
                     AND NOT EXISTS (
                       SELECT 1 FROM suppressions s WHERE s.normalized_email=c.normalized_email
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM sent_messages m WHERE lower(trim(m.email))=c.normalized_email
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM drafts d WHERE d.contact_id=c.id AND d.stage!='rejected'
                     )
                   ORDER BY
                     CASE WHEN c.website!='' THEN 0 ELSE 1 END,
                     CASE WHEN c.phone!='' THEN 0 ELSE 1 END,
                     c.created_at ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
