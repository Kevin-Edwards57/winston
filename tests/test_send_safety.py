"""Guards standing in front of SMTP: dry-run, suppression, rate limiting, idempotency."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import winston_app
from winston.repository import WinstonRepository


class SendGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = winston_app.repository
        winston_app.repository = WinstonRepository(Path(self.temp.name) / "guard.db")
        winston_app.repository.initialize()

    def tearDown(self):
        winston_app.repository = self.original
        self.temp.cleanup()

    def test_dry_run_is_the_default(self):
        """A fresh checkout must never deliver real mail."""
        self.assertTrue(winston_app.WINSTON_DRY_RUN,
                        "WINSTON_DRY_RUN must default to True — real sending is opt-out")

    def test_dry_run_never_opens_smtp(self):
        with patch.object(winston_app, "WINSTON_DRY_RUN", True), \
             patch.object(winston_app.smtplib, "SMTP_SSL") as smtp:
            result = winston_app.send_email_fn("nobody@example.com", "Biz", "body", "subj")
        self.assertTrue(result)
        smtp.assert_not_called()

    def test_suppressed_recipient_is_blocked_even_in_live_mode(self):
        winston_app.repository.suppress("blocked@example.com", "test")
        with patch.object(winston_app, "WINSTON_DRY_RUN", False), \
             patch.object(winston_app.smtplib, "SMTP_SSL") as smtp:
            result = winston_app.send_email_fn("blocked@example.com", "Biz", "body", "subj")
        self.assertFalse(result)
        smtp.assert_not_called()

    def test_suppression_check_is_case_insensitive(self):
        winston_app.repository.suppress("Mixed@Example.COM", "test")
        with patch.object(winston_app, "WINSTON_DRY_RUN", False), \
             patch.object(winston_app.smtplib, "SMTP_SSL") as smtp:
            self.assertFalse(winston_app.send_email_fn("mixed@example.com", "Biz", "b", "s"))
        smtp.assert_not_called()

    def test_daily_cap_blocks_further_sends(self):
        with patch.object(winston_app, "WINSTON_DRY_RUN", False), \
             patch.object(winston_app, "SEND_MAX_PER_DAY", 0), \
             patch.object(winston_app, "SEND_MIN_INTERVAL_S", 0), \
             patch.object(winston_app.smtplib, "SMTP_SSL") as smtp:
            result = winston_app.send_email_fn("fresh@example.com", "Biz", "body", "subj")
        self.assertFalse(result)
        smtp.assert_not_called()


class SendJobIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "jobs.db")
        self.repo.initialize()
        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Test Co", "email": "target@example.com", "place_id": "p1"}, "test")
        self.draft_id = self.repo.create_draft(self.contact_id, "Subject", "Body")
        self.repo.transition_draft(self.draft_id, "reviewed")
        self.repo.transition_draft(self.draft_id, "approved")

    def tearDown(self):
        self.temp.cleanup()

    def test_queueing_twice_returns_the_same_job(self):
        first, created_first = self.repo.queue_draft(self.draft_id)
        second, created_second = self.repo.queue_draft(self.draft_id)
        self.assertEqual(first, second)
        self.assertTrue(created_first)
        self.assertFalse(created_second, "Re-queueing must not create a second send job")

    def test_a_job_cannot_be_claimed_twice(self):
        job_id, _ = self.repo.queue_draft(self.draft_id)
        self.repo.confirm_send(job_id)
        self.assertIsNotNone(self.repo.claim_send(job_id, "worker-a"))
        self.assertIsNone(self.repo.claim_send(job_id, "worker-b"),
                          "A second worker must never claim the same job")

    def test_unconfirmed_job_cannot_be_claimed(self):
        job_id, _ = self.repo.queue_draft(self.draft_id)
        self.assertIsNone(self.repo.claim_send(job_id, "worker"),
                          "Human confirmation is required before a job becomes claimable")

    def test_suppressed_contact_cancels_the_job_at_claim_time(self):
        job_id, _ = self.repo.queue_draft(self.draft_id)
        self.repo.confirm_send(job_id)
        self.repo.suppress("target@example.com", "unsubscribed")
        self.assertIsNone(self.repo.claim_send(job_id, "worker"))
        status = self.repo.connect().execute(
            "SELECT status FROM send_jobs WHERE id=?", (job_id,)).fetchone()["status"]
        self.assertEqual(status, "cancelled")

    def test_approval_alone_does_not_create_a_send_job(self):
        jobs = self.repo.connect().execute("SELECT COUNT(*) c FROM send_jobs").fetchone()["c"]
        self.assertEqual(jobs, 0, "Approving a draft must not queue it for sending")


class SuppressionSeedingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "seed.db")
        self.repo.initialize()
        with self.repo.transaction() as c:
            for i, email in enumerate(["a@example.com", "b@example.com", "A@EXAMPLE.com"]):
                c.execute(
                    """INSERT INTO sent_messages(id,contact_id,email,subject,body,sent_at,source,source_record_id)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (f"m{i}", None, email, "s", "b", "2026-01-01T00:00:00+00:00", "test", f"r{i}"))

    def tearDown(self):
        self.temp.cleanup()

    def test_seeding_suppresses_every_prior_recipient(self):
        result = self.repo.seed_suppressions_from_history()
        self.assertEqual(result["seeded"], 2, "Case variants must collapse to one suppression")
        self.assertTrue(self.repo.is_suppressed("a@example.com"))
        self.assertTrue(self.repo.is_suppressed("b@example.com"))

    def test_seeding_is_idempotent(self):
        self.repo.seed_suppressions_from_history()
        second = self.repo.seed_suppressions_from_history()
        self.assertEqual(second["seeded"], 0)

    def test_extra_addresses_are_included(self):
        self.repo.seed_suppressions_from_history(["legacy@example.com"])
        self.assertTrue(self.repo.is_suppressed("legacy@example.com"))


if __name__ == "__main__":
    unittest.main()
