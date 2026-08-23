"""Commercial event model: outcomes persist, and unknowns stay unknown."""
import tempfile
import unittest
from pathlib import Path

from winston.commercial import CommercialLedger
from winston.repository import WinstonRepository


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "ledger.db")
        self.repo.initialize()
        self.ledger = CommercialLedger(self.repo)
        self.ledger.initialize()
        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Acme", "email": "acme@example.com", "place_id": "p-acme"}, "test")
        self.campaign_id = self.ledger.ensure_campaign("q3", "Q3 push")

    def tearDown(self):
        self.temp.cleanup()

    def _message(self, email="acme@example.com", record_id="m1"):
        return self.ledger.record_message(
            contact_id=self.contact_id, to_email=email, subject="s", body="b",
            campaign_id=self.campaign_id, sent_at="2026-08-01T00:00:00+00:00",
            source="test", source_record_id=record_id)

    # ── persistence ──────────────────────────────────────────────────────

    def test_outcomes_persist_across_repository_instances(self):
        message_id = self._message()
        self.ledger.record_message_event(message_id, "delivered")
        self.ledger.record_reply(
            from_email="acme@example.com", subject="re", body="interested",
            received_at="2026-08-02T00:00:00+00:00", message_id=message_id,
            contact_id=self.contact_id, campaign_id=self.campaign_id, sentiment="positive")

        reopened = CommercialLedger(WinstonRepository(self.repo.database_path))
        history = reopened.contact_history(self.contact_id)
        self.assertEqual(len(history["messages"]), 1)
        self.assertEqual(len(history["replies"]), 1)
        self.assertEqual(history["replies"][0]["sentiment"], "positive")

    def test_full_funnel_to_revenue(self):
        message_id = self._message()
        self.ledger.record_message_event(message_id, "delivered")
        reply_id = self.ledger.record_reply(
            from_email="acme@example.com", subject="re", body="interested",
            received_at="2026-08-02T00:00:00+00:00", message_id=message_id,
            contact_id=self.contact_id, campaign_id=self.campaign_id, sentiment="positive")
        self.ledger.record_meeting(self.contact_id, occurred=True,
                                   occurred_at="2026-08-05T00:00:00+00:00",
                                   campaign_id=self.campaign_id, reply_id=reply_id)
        proposal_id = self.ledger.record_proposal(
            self.contact_id, amount_usd=1200.0, offer_summary="Site + chatbot",
            campaign_id=self.campaign_id)
        deal_id = self.ledger.open_deal(self.contact_id, amount_usd=1200.0,
                                        campaign_id=self.campaign_id, proposal_id=proposal_id)
        self.ledger.close_deal(deal_id, won=True, amount_usd=1000.0)
        self.ledger.record_revenue(deal_id, 500.0, kind="deposit", source_record_id="r1")
        self.ledger.record_revenue(deal_id, 500.0, kind="final", source_record_id="r2")

        self.repo.set_setting("inbox_last_scanned_at", "2026-08-10T00:00:00+00:00")
        funnel = self.ledger.funnel()
        self.assertEqual(funnel["sent"], 1)
        self.assertEqual(funnel["replies"], 1)
        self.assertEqual(funnel["positive_replies"], 1)
        self.assertEqual(funnel["meetings"], 1)
        self.assertEqual(funnel["deals_won"], 1)
        self.assertEqual(funnel["revenue_usd"], 1000.0)

    # ── honesty ──────────────────────────────────────────────────────────

    def test_rates_are_unknown_until_the_inbox_is_scanned(self):
        """A 0% reply rate and 'we never checked' must not look identical."""
        self._message()
        funnel = self.ledger.funnel()
        self.assertEqual(funnel["sent"], 1)
        self.assertFalse(funnel["reply_tracking_enabled"])
        self.assertIsNone(funnel["reply_rate"], "unmeasured must report None, never 0.0")
        self.assertIsNone(funnel["close_rate"])

    def test_rates_become_numbers_once_scanning_has_run(self):
        self._message()
        self.repo.set_setting("inbox_last_scanned_at", "2026-08-10T00:00:00+00:00")
        funnel = self.ledger.funnel()
        self.assertTrue(funnel["reply_tracking_enabled"])
        self.assertEqual(funnel["reply_rate"], 0.0, "a measured zero is a real zero")

    def test_backfill_invents_no_outcomes(self):
        with self.repo.transaction() as c:
            c.execute("""INSERT INTO sent_messages(id,contact_id,email,subject,body,sent_at,source,source_record_id)
                         VALUES('s1',?,'acme@example.com','s','b','2026-01-01T00:00:00+00:00','test','s1')""",
                      (self.contact_id,))
        result = self.ledger.backfill_from_sent_messages()
        self.assertEqual(result["messages_imported"], 1)
        funnel = self.ledger.funnel()
        self.assertEqual(funnel["replies"], 0)
        self.assertFalse(funnel["delivery_tracked"], "backfill must not claim delivery it never observed")

    # ── integrity ────────────────────────────────────────────────────────

    def test_backfill_is_idempotent(self):
        with self.repo.transaction() as c:
            c.execute("""INSERT INTO sent_messages(id,contact_id,email,subject,body,sent_at,source,source_record_id)
                         VALUES('s1',?,'acme@example.com','s','b','2026-01-01T00:00:00+00:00','test','s1')""",
                      (self.contact_id,))
        self.ledger.backfill_from_sent_messages()
        self.ledger.backfill_from_sent_messages()
        self.assertEqual(self.ledger.funnel()["sent"], 1)

    def test_hard_bounce_suppresses_the_recipient(self):
        message_id = self._message()
        self.ledger.record_message_event(message_id, "bounced_hard",
                                         detail={"code": "5.1.1"}, source="imap")
        self.assertTrue(self.repo.is_suppressed("acme@example.com"))

    def test_soft_bounce_does_not_suppress(self):
        """A full mailbox is transient; permanently suppressing would destroy a lead."""
        message_id = self._message()
        self.ledger.record_message_event(message_id, "bounced_soft", detail={"code": "4.2.2"})
        self.assertFalse(self.repo.is_suppressed("acme@example.com"))

    def test_a_lost_deal_must_state_why(self):
        deal_id = self.ledger.open_deal(self.contact_id, amount_usd=900.0)
        with self.assertRaises(ValueError):
            self.ledger.close_deal(deal_id, won=False)
        self.ledger.close_deal(deal_id, won=False, loss_reason_code="price")

    def test_a_deal_cannot_be_closed_twice(self):
        deal_id = self.ledger.open_deal(self.contact_id, amount_usd=900.0)
        self.ledger.close_deal(deal_id, won=True)
        with self.assertRaises(ValueError):
            self.ledger.close_deal(deal_id, won=True)

    def test_unknown_event_type_is_rejected(self):
        message_id = self._message()
        with self.assertRaises(ValueError):
            self.ledger.record_message_event(message_id, "definitely_opened_probably")


if __name__ == "__main__":
    unittest.main()
