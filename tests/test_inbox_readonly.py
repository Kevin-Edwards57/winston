"""Read-only guarantees for mailbox verification.

Pointing Winston at a live personal mailbox is only safe if "read-only" means
read-only against the mailbox *and* the database. These are the invariants that
make a verification scan safe to run before any campaign exists.
"""
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

from winston.commercial import CommercialLedger
from winston.inbox import InboxScanner, classify, extract_body
from winston.repository import WinstonRepository


def build(subject, body, sender="owner@example.com", headers=None):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = "winston@example.com"
    message["Date"] = "Mon, 24 Aug 2026 09:00:00 +0000"
    message["Message-ID"] = f"<{abs(hash(subject))}@example.com>"
    for key, value in (headers or {}).items():
        message[key] = value
    message.set_content(body)
    return message


class ReadOnlyScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "inbox.db")
        self.repo.initialize()
        self.ledger = CommercialLedger(self.repo)
        self.ledger.initialize()
        self.scanner = InboxScanner(self.repo, self.ledger, ai_service=None)

        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Acme", "email": "owner@example.com", "place_id": "p1"}, "test")
        self.ledger.record_message(
            contact_id=self.contact_id, to_email="owner@example.com",
            subject="s", body="b", source="test", source_record_id="m1")

    def tearDown(self):
        self.temp.cleanup()

    def _connection(self, messages):
        connection = MagicMock()
        connection.select.return_value = ("OK", [b"1"])
        connection.search.return_value = ("OK", [b" ".join(
            str(i).encode() for i in range(1, len(messages) + 1))])
        connection.fetch.side_effect = [
            ("OK", [(b"1", m.as_bytes())]) for m in messages]
        return connection

    def _counts(self):
        c = self.repo.connect()
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("replies", "suppressions", "message_events")}

    def _scan(self, messages, **kwargs):
        with patch.object(self.scanner, "_connect",
                          return_value=self._connection(messages)):
            return self.scanner.scan(**kwargs)

    # ── the mailbox ──────────────────────────────────────────────────────

    def test_scan_opens_the_mailbox_readonly(self):
        connection = self._connection([build("Re: hi", "interested")])
        with patch.object(self.scanner, "_connect", return_value=connection):
            self.scanner.scan(persist=False)
        self.assertTrue(connection.select.call_args.kwargs.get("readonly"))

    def test_fetch_uses_peek_so_messages_stay_unread(self):
        """RFC822 implicitly sets the Seen flag; BODY.PEEK does not."""
        connection = self._connection([build("Re: hi", "interested")])
        with patch.object(self.scanner, "_connect", return_value=connection):
            self.scanner.scan(persist=False)
        fetched = connection.fetch.call_args[0][1]
        self.assertIn("PEEK", fetched)
        self.assertNotIn("RFC822", fetched)

    def test_scan_never_mutates_the_mailbox(self):
        connection = self._connection([build("Re: hi", "interested")])
        with patch.object(self.scanner, "_connect", return_value=connection):
            self.scanner.scan(persist=False)
        connection.store.assert_not_called()
        connection.expunge.assert_not_called()
        connection.copy.assert_not_called()

    # ── the database ─────────────────────────────────────────────────────

    def test_verification_scan_writes_nothing(self):
        before = self._counts()
        self._scan([build("Re: hi", "interested, how much?"),
                    build("Out of office", "I am on annual leave"),
                    build("unsubscribe", "please remove me")], persist=False)
        self.assertEqual(self._counts(), before, "a verification scan must not persist")

    def test_verification_scan_does_not_enable_reply_tracking(self):
        self._scan([build("Re: hi", "interested")], persist=False)
        self.assertIsNone(self.repo.get_setting("inbox_last_scanned_at"),
                          "proving the mailbox is reachable is not tracking replies")

    def test_verification_scan_does_not_suppress(self):
        """An unsubscribe seen during verification must not suppress anyone."""
        self._scan([build("unsubscribe", "remove me from your list")], persist=False)
        self.assertFalse(self.repo.is_suppressed("owner@example.com"))

    def test_a_persisting_scan_does_write(self):
        """The read-only mode must be a real difference, not a no-op."""
        self._scan([build("Re: hi", "interested, how much?")], persist=True)
        self.assertEqual(self._counts()["replies"], 1)
        self.assertIsNotNone(self.repo.get_setting("inbox_last_scanned_at"))

    def test_classification_still_runs_when_not_persisting(self):
        summary = self._scan([
            build("Re: hi", "interested, what would it cost?"),
            build("Out of office", "I am away", headers={"Auto-Submitted": "auto-replied"}),
        ], persist=False)
        self.assertEqual(summary["scanned"], 2)
        self.assertEqual(summary["auto_replies"], 1)
        self.assertFalse(summary["persisted"])

    # ── correlation ──────────────────────────────────────────────────────

    def test_a_reply_correlates_to_a_known_contact(self):
        contact_id, message_id = self.scanner._match_contact("owner@example.com")
        self.assertEqual(contact_id, self.contact_id)
        self.assertIsNotNone(message_id)

    def test_an_unknown_sender_does_not_correlate(self):
        contact_id, message_id = self.scanner._match_contact("stranger@example.com")
        self.assertIsNone(contact_id)
        self.assertIsNone(message_id)

    def test_unmatched_mail_is_counted_not_guessed(self):
        summary = self._scan([build("Newsletter", "this week in tech",
                                    sender="news@unknown.example.com")], persist=False)
        self.assertEqual(summary["unmatched"], 1)


if __name__ == "__main__":
    unittest.main()
