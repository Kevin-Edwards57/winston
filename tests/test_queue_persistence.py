"""The review queue must survive a restart.

Drafts previously reached SQLite only when a human clicked Approve, so an
unreviewed queue existed solely in ``state["pending"]``. Restarting the process
discarded it — which is why the drafts table held 0 rows against 139 historical
sends. Persistent prospect memory cannot be built on a list in RAM.
"""
import tempfile
import unittest
from pathlib import Path

import winston_app
from winston.repository import WinstonRepository


class QueuePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_repo = winston_app.repository
        self.original_pending = winston_app.state["pending"]
        winston_app.repository = WinstonRepository(Path(self.temp.name) / "queue.db")
        winston_app.repository.initialize()
        winston_app.state["pending"] = []

    def tearDown(self):
        winston_app.repository = self.original_repo
        winston_app.state["pending"] = self.original_pending
        self.temp.cleanup()

    def _draft(self, name="Persist Co", email="persist@example.com", place_id="pp1"):
        business = {"name": name, "email": email, "place_id": place_id,
                    "address": "1 Main St", "type": "barbershop"}
        winston_app.persist_draft(business, f"Subject for {name}", "Draft body")
        return business

    def test_a_generated_draft_is_written_to_the_database_immediately(self):
        business = self._draft()
        stored = winston_app.repository.get_draft(business["draft_id"])
        self.assertIsNotNone(stored, "the draft must exist before anyone approves it")
        self.assertEqual(stored["stage"], "draft")

    def test_queue_is_restored_after_a_simulated_restart(self):
        business = self._draft()
        winston_app.state["pending"] = []           # process dies
        restored = winston_app.rehydrate_pending_queue()
        self.assertEqual(restored, 1)
        self.assertEqual(winston_app.state["pending"][0]["draft_id"], business["draft_id"])
        self.assertEqual(winston_app.state["pending"][0]["subject"], business["subject"])

    def test_rehydration_is_not_cumulative(self):
        self._draft()
        winston_app.rehydrate_pending_queue()
        winston_app.rehydrate_pending_queue()
        self.assertEqual(len(winston_app.state["pending"]), 1, "queue must not duplicate on re-entry")

    def test_suppressed_contacts_are_not_restored_into_the_queue(self):
        self._draft(email="blocked@example.com")
        winston_app.repository.suppress("blocked@example.com", "unsubscribed")
        winston_app.state["pending"] = []
        self.assertEqual(winston_app.rehydrate_pending_queue(), 0)

    def test_sent_drafts_are_not_restored(self):
        business = self._draft()
        draft_id = business["draft_id"]
        winston_app.repository.transition_draft(draft_id, "reviewed")
        winston_app.repository.transition_draft(draft_id, "approved")
        job_id, _ = winston_app.repository.queue_draft(draft_id)
        winston_app.repository.confirm_send(job_id)
        winston_app.repository.claim_send(job_id, "w")
        winston_app.repository.complete_send(job_id, success=True)

        winston_app.state["pending"] = []
        self.assertEqual(winston_app.rehydrate_pending_queue(), 0,
                         "an already-sent draft must never reappear in the review queue")

    def test_approved_but_unsent_drafts_are_restored(self):
        business = self._draft()
        winston_app.repository.transition_draft(business["draft_id"], "reviewed")
        winston_app.repository.transition_draft(business["draft_id"], "approved")
        winston_app.state["pending"] = []
        self.assertEqual(winston_app.rehydrate_pending_queue(), 1,
                         "work approved before a crash must not be lost")


if __name__ == "__main__":
    unittest.main()
