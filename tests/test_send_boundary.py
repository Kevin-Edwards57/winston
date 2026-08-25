"""The send boundary, and what may be learned from.

Two invariants that a Phase 1 audit found were assumed rather than enforced.

**Guardian must have reviewed the exact text being sent.** It previously ran only at
generation, which guaranteed "Guardian approved a draft" and not "Guardian approved the
message that went out". The review screen lets a human edit an approved draft, so an old
PASS could authorise text Guardian had never seen.

**Legacy sends must never become training data.** The 139 rows migrated from
followups.json were real emails, but they carry no observed problem, offer, proof, price
or verdict. As training data they are outcomes with no features, which is worse than no
data because it looks like data.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import winston_app
from winston.commercial import (
    PROVENANCE_LEGACY, PROVENANCE_PRODUCTION, CommercialLedger)
from winston.guardian import GuardianResult
from winston.repository import WinstonRepository


class SendBoundaryTests(unittest.TestCase):
    """No email reaches SMTP unless a verdict covers that exact body."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = winston_app.repository
        winston_app.repository = WinstonRepository(Path(self.temp.name) / "boundary.db")
        winston_app.repository.initialize()
        self.subject = "Quick note on your site"
        self.body = "I noticed your site has no enquiry form. Would that be useful?"

    def tearDown(self):
        winston_app.repository = self.original
        self.temp.cleanup()

    def _verdict(self, subject=None, body=None, approved=True):
        verdict = GuardianResult(approved=approved)
        verdict.reviewed_digest = winston_app._body_digest(
            subject if subject is not None else self.subject,
            body if body is not None else self.body)
        if not approved:
            verdict.issues.append({"rule": "no_em_dash", "detail": "d", "excerpt": ""})
        return verdict

    def _send(self, verdict, body=None):
        with patch.object(winston_app, "WINSTON_DRY_RUN", False), \
             patch.object(winston_app, "SEND_MIN_INTERVAL_S", 0), \
             patch.object(winston_app.smtplib, "SMTP_SSL") as smtp:
            result = winston_app.send_email_fn(
                "target@example.com", "Biz",
                body if body is not None else self.body, self.subject,
                guardian_verdict=verdict)
        return result, smtp

    def test_missing_verdict_blocks_smtp(self):
        """A caller that forgets the verdict must not send."""
        result, smtp = self._send(None)
        self.assertFalse(result)
        smtp.assert_not_called()

    def test_blocked_verdict_blocks_smtp(self):
        result, smtp = self._send(self._verdict(approved=False))
        self.assertFalse(result)
        smtp.assert_not_called()

    def test_edited_body_invalidates_an_approved_verdict(self):
        """The central case: approved, then edited, then sent."""
        verdict = self._verdict()                      # covers the original body
        edited = self.body + " We guarantee results."  # human edits after approval
        result, smtp = self._send(verdict, body=edited)
        self.assertFalse(result, "a verdict must not outlive an edit")
        smtp.assert_not_called()

    def test_em_dash_added_after_approval_is_blocked(self):
        verdict = self._verdict()
        result, smtp = self._send(verdict, body="Your site has no form — call us.")
        self.assertFalse(result)
        smtp.assert_not_called()

    def test_unedited_body_is_allowed_through(self):
        result, smtp = self._send(self._verdict())
        self.assertTrue(result)
        smtp.assert_called_once()

    def test_the_body_smtp_receives_is_the_body_guardian_reviewed(self):
        verdict = self._verdict()
        with patch.object(winston_app, "WINSTON_DRY_RUN", False), \
             patch.object(winston_app, "SEND_MIN_INTERVAL_S", 0), \
             patch.object(winston_app.smtplib, "SMTP_SSL") as smtp:
            winston_app.send_email_fn("target@example.com", "Biz", self.body,
                                      self.subject, guardian_verdict=verdict)
            sent = smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
        transmitted = sent.get_payload()[0].get_payload()
        self.assertEqual(winston_app._body_digest(sent["Subject"], transmitted),
                         verdict.reviewed_digest)

    def test_suppression_still_wins_over_a_valid_verdict(self):
        winston_app.repository.suppress("target@example.com", "unsubscribed")
        result, smtp = self._send(self._verdict())
        self.assertFalse(result)
        smtp.assert_not_called()

    def test_dry_run_still_blocks_smtp_with_a_valid_verdict(self):
        with patch.object(winston_app, "WINSTON_DRY_RUN", True), \
             patch.object(winston_app.smtplib, "SMTP_SSL") as smtp:
            result = winston_app.send_email_fn("t@example.com", "B", self.body,
                                               self.subject,
                                               guardian_verdict=self._verdict())
        self.assertTrue(result)
        smtp.assert_not_called()

    def test_no_bypass_exists_in_the_source(self):
        """Matched on whole identifiers.

        A substring check flags ``_enforce_send_limits`` for containing "force_send",
        which is the same false positive the pricing guard's word boundaries exist to
        avoid.
        """
        import ast
        tree = ast.parse(Path(winston_app.__file__).read_text())
        identifiers = {n.name for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        identifiers |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        identifiers |= {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
        for bypass in ("force_send", "forceSend", "send_anyway", "sendAnyway",
                       "skip_guardian", "bypass_guardian", "override_guardian"):
            self.assertNotIn(bypass, identifiers)


class LegacyProvenanceTests(unittest.TestCase):
    """Legacy sends stay queryable and stay out of every dataset."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "prov.db")
        self.repo.initialize()
        self.ledger = CommercialLedger(self.repo)
        self.ledger.initialize()
        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Acme", "email": "acme@example.com", "place_id": "p1"}, "test")

    def tearDown(self):
        self.temp.cleanup()

    def _message(self, record_id, provenance, draft_id=None):
        return self.ledger.record_message(
            contact_id=self.contact_id, to_email="acme@example.com", subject="s",
            body="b", draft_id=draft_id, source="test", source_record_id=record_id,
            provenance=provenance)

    def test_legacy_messages_are_not_learner_eligible(self):
        self._message("legacy-1", PROVENANCE_LEGACY)
        self.assertEqual(self.ledger.learning_dataset(), [])

    def test_absent_provenance_defaults_to_ineligible(self):
        """A caller that does not think about provenance must not create training data."""
        self.ledger.record_message(
            contact_id=self.contact_id, to_email="acme@example.com", subject="s",
            body="b", source="test", source_record_id="unspecified")
        self.assertEqual(self.ledger.learning_dataset(), [])

    def test_production_message_with_a_draft_is_eligible(self):
        draft_id = self.repo.create_draft(self.contact_id, "s", "b")
        self._message("prod-1", PROVENANCE_PRODUCTION, draft_id=draft_id)
        dataset = self.ledger.learning_dataset()
        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset[0]["provenance"], PROVENANCE_PRODUCTION)

    def test_production_message_without_a_draft_is_excluded(self):
        """Eligibility needs the evidence chain, not just the label."""
        self._message("prod-2", PROVENANCE_PRODUCTION, draft_id=None)
        self.assertEqual(self.ledger.learning_dataset(), [])

    def test_legacy_records_remain_queryable(self):
        self._message("legacy-2", PROVENANCE_LEGACY)
        count = self.repo.connect().execute(
            "SELECT COUNT(*) n FROM messages").fetchone()["n"]
        self.assertEqual(count, 1, "history is preserved, only excluded from learning")

    def test_backfill_marks_everything_legacy(self):
        with self.repo.transaction() as connection:
            connection.execute(
                """INSERT INTO sent_messages(id,contact_id,email,subject,body,sent_at,
                                             source,source_record_id)
                   VALUES('s1',?,'acme@example.com','s','b','2026-01-01T00:00:00+00:00',
                          'test','s1')""", (self.contact_id,))
        self.ledger.backfill_from_sent_messages()
        self.assertEqual(self.ledger.learning_dataset(), [])
        readiness = self.ledger.dataset_readiness()
        self.assertEqual(readiness["learner_eligible_messages"], 0)
        self.assertGreater(readiness["excluded_legacy_messages"], 0)

    def test_ml_status_reports_insufficient_data(self):
        readiness = self.ledger.dataset_readiness()
        self.assertEqual(readiness["ml_status"], "INSUFFICIENT_DATA")
        self.assertIn("evidence chain", readiness["reason"])


if __name__ == "__main__":
    unittest.main()
