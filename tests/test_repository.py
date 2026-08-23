import json
import tempfile
import unittest
from pathlib import Path

from winston.migration import migrate_json
from winston.repository import WinstonRepository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = WinstonRepository(self.root / "test.db")
        self.repo.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_contacts_merge_without_losing_legacy_rows(self):
        payload = {"name": "One", "email": "Info@Example.com", "phone": "111"}
        first_id, created = self.repo.upsert_contact(payload, "test")
        second_id, created_again = self.repo.upsert_contact(
            {"name": "One Updated", "email": "info@example.com", "website": "https://example.com"}, "test"
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first_id, second_id)
        self.assertEqual(self.repo.counts()["contacts"], 1)

    def test_migration_is_idempotent_and_preserves_source_rows(self):
        fixtures = {
            "contacts.json": [{"name": "A", "email": "a@example.com"}, {"name": "A2", "email": "A@example.com"}],
            "emailed.json": ["a@example.com", "a@example.com"],
            "followups.json": [], "social_leads.json": [], "stats.json": {"emails_sent": 2},
        }
        for filename, value in fixtures.items():
            (self.root / filename).write_text(json.dumps(value))
        first = migrate_json(self.repo, self.root, backup=False, report_path=self.root / "first.json")
        second = migrate_json(self.repo, self.root, backup=False, report_path=self.root / "second.json")
        self.assertEqual(first["results"]["legacy_import_records"], 5)
        self.assertEqual(second["results"]["legacy_import_records"], 5)
        self.assertTrue(all(source["new_imports"] == 0 for source in second["sources"].values()))
        self.assertEqual(first["duplicates"]["emailed_addresses"]["a@example.com"], 2)

    def test_workflow_requires_confirmation_and_is_idempotent(self):
        contact_id, _ = self.repo.upsert_contact({"name": "A", "email": "a@example.com"}, "test")
        draft_id = self.repo.create_draft(contact_id, "Subject", "Body")
        self.repo.transition_draft(draft_id, "reviewed")
        self.repo.transition_draft(draft_id, "approved")
        job_id, created = self.repo.queue_draft(draft_id)
        same_job_id, created_again = self.repo.queue_draft(draft_id)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(job_id, same_job_id)
        self.assertIsNone(self.repo.claim_send(job_id, "worker-before-confirmation"))
        self.repo.confirm_send(job_id)
        self.assertIsNotNone(self.repo.claim_send(job_id, "worker-one"))
        self.assertIsNone(self.repo.claim_send(job_id, "worker-two"))

    def test_suppression_blocks_claimed_send(self):
        contact_id, _ = self.repo.upsert_contact({"name": "A", "email": "a@example.com"}, "test")
        draft_id = self.repo.create_draft(contact_id, "Subject", "Body")
        self.repo.transition_draft(draft_id, "reviewed")
        self.repo.transition_draft(draft_id, "approved")
        job_id, _ = self.repo.queue_draft(draft_id)
        self.repo.confirm_send(job_id)
        self.repo.suppress("A@example.com", "do-not-contact", contact_id)
        self.assertTrue(self.repo.is_suppressed("a@example.com"))
        self.assertIsNone(self.repo.claim_send(job_id, "worker"))

    def test_followups_default_to_disabled(self):
        self.assertFalse(self.repo.get_setting("automatic_followups_enabled", True))

    def test_existing_contact_candidates_exclude_drafted_and_suppressed(self):
        first_id, _ = self.repo.upsert_contact({"name": "Draft me", "email": "draft@example.com", "website": "https://example.com"}, "test")
        second_id, _ = self.repo.upsert_contact({"name": "Blocked", "email": "blocked@example.com"}, "test")
        candidates = self.repo.draft_candidates(10)
        self.assertEqual([row["id"] for row in candidates], [first_id, second_id])
        self.repo.create_draft(first_id, "Subject", "Body")
        self.repo.suppress("blocked@example.com", "do-not-contact", second_id)
        self.assertEqual(self.repo.draft_candidates(10), [])


if __name__ == "__main__":
    unittest.main()
