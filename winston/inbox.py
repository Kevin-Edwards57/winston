"""Inbox intelligence — turning what the mailbox says into commercial facts.

This is the half of the loop Winston never had. Outreach without reply detection
produces activity metrics; outreach with it produces training data.

Classification is deliberately tiered, cheapest first:

1. **Headers.** RFC 3834 ``Auto-Submitted``, ``X-Autoreply``, and null return-paths
   identify machine mail with certainty. No inference, no cost.
2. **Delivery-status parsing.** RFC 3463 status codes distinguish a hard bounce
   (5.x.x — suppress permanently) from a soft one (4.x.x — transient, do not suppress).
3. **Keywords.** Unsubscribe requests and obvious rejections are lexical.
4. **AI.** Only genuine human replies that survive the first three tiers get a model
   call, and even then via the zero-cost router.

The tiering is not premature optimization. Auto-replies and bounces are the bulk of
inbound volume on cold outreach; paying a model to read "Out of office until Monday"
is exactly the waste the AI router exists to prevent.
"""
from __future__ import annotations

import email
import email.utils
import imaplib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from typing import Any

from .commercial import CommercialLedger
from .repository import WinstonRepository, normalize_email, utc_now

# ── Header-level machine-mail signals (RFC 3834 and common vendor headers) ──
AUTO_HEADERS = (
    "auto-submitted", "x-autoreply", "x-autorespond", "x-auto-response-suppress",
    "precedence", "x-failed-recipients",
)
BOUNCE_SENDERS = re.compile(
    r"(mailer-daemon|postmaster|no-?reply|bounce|delivery|notification)@", re.I)

# RFC 3463 enhanced status codes: 5.x.x permanent, 4.x.x transient.
STATUS_CODE = re.compile(r"\b([45])\.(\d{1,3})\.(\d{1,3})\b")

HARD_BOUNCE_PHRASES = (
    "user unknown", "no such user", "does not exist", "unknown recipient",
    "address rejected", "mailbox unavailable", "recipient not found",
    "invalid recipient", "account has been disabled", "domain not found",
)
SOFT_BOUNCE_PHRASES = (
    "mailbox full", "over quota", "quota exceeded", "try again later",
    "temporarily deferred", "temporary failure", "greylisted",
)
UNSUBSCRIBE_PHRASES = (
    "unsubscribe", "remove me", "take me off", "stop emailing", "do not contact",
    "opt out", "opt-out", "stop contacting", "no longer wish to receive",
)
OUT_OF_OFFICE_PHRASES = (
    "out of office", "on vacation", "annual leave", "parental leave",
    "away from my desk", "currently out of the office", "automatic reply",
)
NEGATIVE_PHRASES = (
    "not interested", "no thanks", "no thank you", "we're all set", "we are all set",
    "already have", "not a fit", "not looking", "please stop", "spam",
)
POSITIVE_PHRASES = (
    "interested", "tell me more", "sounds good", "let's talk", "lets talk",
    "call me", "give me a call", "schedule", "book a time", "available",
    "how much", "what would it cost", "pricing", "send more info", "learn more",
    "yes please", "happy to chat", "would love to",
)


@dataclass
class Classification:
    """What a message is, and how confident we are that it is that."""
    kind: str                       # reply | bounce_hard | bounce_soft | auto_reply | unsubscribe
    sentiment: str = "unknown"      # only meaningful when kind == "reply"
    confidence: float = 0.0
    method: str = "heuristic"       # heuristic | header | status_code | ai
    evidence: list[str] = field(default_factory=list)


def _decode(part: Any) -> str:
    if part is None:
        return ""
    if isinstance(part, bytes):
        return part.decode("utf-8", errors="replace")
    return str(part)


def extract_body(message: Message, limit: int = 8000) -> str:
    """Best-effort plain-text body. Prefers text/plain, falls back to stripped HTML."""
    plain, html = [], []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        text = _decode(payload)
        (plain if content_type == "text/plain" else html).append(text)

    body = "\n".join(plain) if plain else re.sub(r"<[^>]+>", " ", "\n".join(html))
    return re.sub(r"\s+", " ", body).strip()[:limit]


def classify(message: Message, body: str) -> Classification:
    """Tier 1-3 classification. Returns a Classification; AI is never called here."""
    subject = _decode(message.get("Subject", ""))
    sender = _decode(message.get("From", ""))
    haystack = f"{subject}\n{body}".casefold()

    # Tier 1 — headers are authoritative for machine mail.
    auto_submitted = (message.get("Auto-Submitted", "") or "").casefold()
    if auto_submitted and auto_submitted != "no":
        kind = "auto_reply"
        if any(p in haystack for p in OUT_OF_OFFICE_PHRASES):
            return Classification(kind, "out_of_office", 0.98, "header", ["Auto-Submitted"])
        return Classification(kind, "auto_reply", 0.95, "header", ["Auto-Submitted"])

    for header in AUTO_HEADERS:
        if message.get(header):
            if header == "precedence" and _decode(message.get(header, "")).casefold() not in ("bulk", "auto_reply", "junk"):
                continue
            return Classification("auto_reply", "auto_reply", 0.9, "header", [header])

    # Tier 2 — delivery status notifications.
    is_dsn = (message.get_content_type() == "multipart/report"
              or BOUNCE_SENDERS.search(sender or "")
              or "delivery status notification" in haystack
              or "undeliverable" in haystack)
    if is_dsn:
        match = STATUS_CODE.search(body) or STATUS_CODE.search(subject)
        if match:
            code = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
            if match.group(1) == "5":
                return Classification("bounce_hard", "unknown", 0.97, "status_code", [code])
            return Classification("bounce_soft", "unknown", 0.95, "status_code", [code])
        if any(p in haystack for p in HARD_BOUNCE_PHRASES):
            return Classification("bounce_hard", "unknown", 0.85, "heuristic", ["phrase"])
        if any(p in haystack for p in SOFT_BOUNCE_PHRASES):
            return Classification("bounce_soft", "unknown", 0.85, "heuristic", ["phrase"])
        return Classification("bounce_soft", "unknown", 0.6, "heuristic", ["dsn-shape"])

    # Tier 3 — lexical signals on human mail.
    if any(p in haystack for p in UNSUBSCRIBE_PHRASES):
        return Classification("unsubscribe", "negative", 0.9, "heuristic", ["unsubscribe-phrase"])
    if any(p in haystack for p in OUT_OF_OFFICE_PHRASES):
        return Classification("auto_reply", "out_of_office", 0.85, "heuristic", ["ooo-phrase"])

    negatives = [p for p in NEGATIVE_PHRASES if p in haystack]
    positives = [p for p in POSITIVE_PHRASES if p in haystack]
    if negatives and not positives:
        return Classification("reply", "negative", 0.8, "heuristic", negatives[:3])
    if positives and not negatives:
        return Classification("reply", "positive", 0.75, "heuristic", positives[:3])

    # Genuine human reply with mixed or no lexical signal — worth an AI call.
    return Classification("reply", "unknown", 0.0, "heuristic", ["needs-ai"])


AI_PROMPT = """Classify this reply to a cold B2B sales email.

Reply:
---
{body}
---

Answer with exactly one word:
positive  - they want to talk, asked a question, requested pricing, or showed interest
negative  - they declined, are not interested, or asked to stop
neutral   - acknowledgement, forwarding, or unclear intent

One word only."""


def classify_sentiment_with_ai(ai_service: Any, body: str) -> tuple[str, float]:
    """Tier 4. Returns (sentiment, confidence); falls back to neutral on any failure."""
    try:
        result = ai_service.generate(
            AI_PROMPT.format(body=body[:2000]), max_tokens=10, purpose="reply_classification")
        answer = (result.text or "").strip().casefold()
        for candidate in ("positive", "negative", "neutral"):
            if candidate in answer:
                return candidate, 0.7
        return "neutral", 0.3
    except Exception:
        return "neutral", 0.0


class InboxScanner:
    """Reads the mailbox and records what it finds as commercial events."""

    def __init__(self, repository: WinstonRepository, ledger: CommercialLedger,
                 ai_service: Any = None, *, host: str = "imap.gmail.com") -> None:
        self.repository = repository
        self.ledger = ledger
        self.ai_service = ai_service
        self.host = host

    # ── connection ───────────────────────────────────────────────────────

    def _connect(self) -> imaplib.IMAP4_SSL:
        address = os.getenv("GMAIL_ADDRESS", "")
        password = os.getenv("GMAIL_APP_PASSWORD", "")
        if not address or not password:
            raise RuntimeError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set to scan the inbox")
        connection = imaplib.IMAP4_SSL(self.host)
        connection.login(address, password)
        return connection

    # ── matching ─────────────────────────────────────────────────────────

    def _match_contact(self, from_email: str) -> tuple[str | None, str | None]:
        """Resolve an inbound address to (contact_id, most_recent_message_id)."""
        normalized = normalize_email(from_email)
        if not normalized:
            return None, None
        with self.repository.read() as c:
            contact = c.execute(
                "SELECT id FROM contacts WHERE normalized_email=?", (normalized,)).fetchone()
            message = c.execute(
                "SELECT id FROM messages WHERE normalized_email=? ORDER BY sent_at DESC LIMIT 1",
                (normalized,)).fetchone()
        return (contact["id"] if contact else None, message["id"] if message else None)

    # ── the scan ─────────────────────────────────────────────────────────

    def scan(self, *, mailbox: str = "INBOX", limit: int = 200,
             mark_seen: bool = False, persist: bool = True,
             search: str = "UNSEEN") -> dict[str, Any]:
        """Scan for replies and delivery notifications, recording each as an event.

        Read-only against the mailbox by default: nothing is deleted, and messages
        stay unread unless ``mark_seen`` is set.

        ``persist=False`` additionally makes the scan read-only against the *database*.
        Classification still runs, so the results are real, but no reply, suppression,
        delivery event or setting is written. That is what makes it safe to point at a
        live personal mailbox to find out whether the integration works at all.
        """
        self._persist = persist
        summary = {
            "scanned": 0, "replies": 0, "positive": 0, "negative": 0, "neutral": 0,
            "auto_replies": 0, "hard_bounces": 0, "soft_bounces": 0,
            "unsubscribes": 0, "unmatched": 0, "ai_calls": 0, "errors": 0,
        }

        connection = self._connect()
        try:
            connection.select(mailbox, readonly=not mark_seen)
            status, data = connection.search(None, search)
            if status != "OK":
                raise RuntimeError(f"IMAP search failed: {status}")
            ids = data[0].split()[-limit:]

            for message_id_bytes in ids:
                try:
                    self._process_one(connection, message_id_bytes, summary)
                except Exception:
                    summary["errors"] += 1
        finally:
            try:
                connection.close()
            except Exception:
                pass
            connection.logout()

        # Marks reply data as observable. A verification scan must not flip this:
        # proving the mailbox is reachable is not the same as tracking replies.
        if persist:
            self.repository.set_setting("inbox_last_scanned_at", utc_now())
            self.repository.add_event("inbox.scanned", details=summary)
        summary["persisted"] = persist
        return summary

    # ── persistence, gated ───────────────────────────────────────────────
    # Every database write in this module goes through these, so a verification
    # scan cannot mutate state by forgetting one call site.

    def _write_record_message_event(self, *args: Any, **kwargs: Any) -> None:
        if getattr(self, "_persist", True):
            self.ledger.record_message_event(*args, **kwargs)

    def _write_suppress(self, *args: Any, **kwargs: Any) -> None:
        if getattr(self, "_persist", True):
            self.repository.suppress(*args, **kwargs)

    def _write_record_reply(self, *args: Any, **kwargs: Any) -> None:
        if getattr(self, "_persist", True):
            self.ledger.record_reply(*args, **kwargs)

    def _process_one(self, connection: imaplib.IMAP4_SSL, uid: bytes,
                     summary: dict[str, Any]) -> None:
        # BODY.PEEK[] rather than RFC822. RFC822 implicitly sets the \Seen flag, and
        # relying on readonly select to suppress that puts the guarantee in the server's
        # hands rather than in the request. PEEK states the intent explicitly.
        status, payload = connection.fetch(uid, "(BODY.PEEK[])")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            summary["errors"] += 1
            return

        message = email.message_from_bytes(payload[0][1])
        summary["scanned"] += 1

        _, from_email = email.utils.parseaddr(_decode(message.get("From", "")))
        subject = _decode(message.get("Subject", ""))
        body = extract_body(message)
        received_at = self._received_at(message)
        rfc_message_id = _decode(message.get("Message-ID", "")) or f"uid-{uid.decode()}"

        verdict = classify(message, body)

        # Bounces reference the original recipient, which for a DSN is not the sender.
        target_email = from_email
        if verdict.kind.startswith("bounce"):
            target_email = self._original_recipient(message, body) or from_email

        contact_id, message_row_id = self._match_contact(target_email)
        if contact_id is None and message_row_id is None:
            summary["unmatched"] += 1

        if verdict.kind in ("bounce_hard", "bounce_soft"):
            key = "hard_bounces" if verdict.kind == "bounce_hard" else "soft_bounces"
            summary[key] += 1
            if message_row_id:
                # record_message_event suppresses on hard bounce.
                self._write_record_message_event(
                    message_row_id, verdict.kind, occurred_at=received_at,
                    detail={"evidence": verdict.evidence, "method": verdict.method},
                    source="imap")
            elif verdict.kind == "bounce_hard" and target_email:
                self._write_suppress(target_email, "delivery:bounced_hard", contact_id)
            return

        if verdict.kind == "unsubscribe":
            summary["unsubscribes"] += 1
            if target_email:
                self._write_suppress(target_email, "unsubscribe:requested", contact_id)
            if message_row_id:
                self._write_record_message_event(
                    message_row_id, "unsubscribed", occurred_at=received_at,
                    detail={"evidence": verdict.evidence}, source="imap")
            self._write_record_reply(
                from_email=from_email, subject=subject, body=body, received_at=received_at,
                message_id=message_row_id, contact_id=contact_id, sentiment="negative",
                classified_by=f"inbox:{verdict.method}", confidence=verdict.confidence,
                source="imap", source_record_id=rfc_message_id)
            return

        if verdict.kind == "auto_reply":
            summary["auto_replies"] += 1
            self._write_record_reply(
                from_email=from_email, subject=subject, body=body, received_at=received_at,
                message_id=message_row_id, contact_id=contact_id,
                sentiment=verdict.sentiment if verdict.sentiment in ("out_of_office", "auto_reply") else "auto_reply",
                classified_by=f"inbox:{verdict.method}", confidence=verdict.confidence,
                source="imap", source_record_id=rfc_message_id)
            return

        # Genuine human reply. Escalate to AI only when heuristics were inconclusive.
        sentiment, confidence, method = verdict.sentiment, verdict.confidence, verdict.method
        if sentiment == "unknown" and self.ai_service is not None:
            sentiment, confidence = classify_sentiment_with_ai(self.ai_service, body)
            method = "ai"
            summary["ai_calls"] += 1
        elif sentiment == "unknown":
            sentiment, confidence, method = "neutral", 0.0, "unclassified"

        summary["replies"] += 1
        summary[{"positive": "positive", "negative": "negative"}.get(sentiment, "neutral")] += 1

        self._write_record_reply(
            from_email=from_email, subject=subject, body=body, received_at=received_at,
            message_id=message_row_id, contact_id=contact_id, sentiment=sentiment,
            classified_by=f"inbox:{method}", confidence=confidence,
            source="imap", source_record_id=rfc_message_id)

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _received_at(message: Message) -> str:
        raw = message.get("Date")
        if raw:
            try:
                parsed = email.utils.parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc).isoformat()
            except Exception:
                pass
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _original_recipient(message: Message, body: str) -> str | None:
        """Pull the failed recipient out of a DSN so the right address is suppressed."""
        for header in ("X-Failed-Recipients", "Original-Recipient", "Final-Recipient"):
            value = _decode(message.get(header, ""))
            if value:
                _, address = email.utils.parseaddr(value.split(";")[-1].strip())
                if address:
                    return address

        for part in message.walk():
            if part.get_content_type() == "message/delivery-status":
                text = _decode(part.get_payload(decode=True)) or ""
                match = re.search(r"Final-Recipient:.*?;\s*([^\s<>]+@[^\s<>]+)", text, re.I)
                if match:
                    return match.group(1)

        match = re.search(r"Final-Recipient:.*?;\s*([^\s<>]+@[^\s<>]+)", body, re.I)
        return match.group(1) if match else None
