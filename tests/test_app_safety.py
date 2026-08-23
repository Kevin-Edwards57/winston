import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import winston_app
from winston.repository import WinstonRepository


class AppSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_repository = winston_app.repository
        winston_app.repository = WinstonRepository(Path(self.temp.name) / "app.db")
        winston_app.repository.initialize()
        self.original_pending = winston_app.state["pending"]
        self.original_status = winston_app.state["status"]
        winston_app.state["pending"] = [{
            "name": "Safety Test", "email": "safe@example.com", "draft": "Draft body",
            "subject": "Draft subject", "place_id": "test-place",
        }]
        self.client = winston_app.app.test_client()

    def tearDown(self):
        winston_app.repository = self.original_repository
        winston_app.state["pending"] = self.original_pending
        winston_app.state["status"] = self.original_status
        self.temp.cleanup()

    def test_approve_never_sends(self):
        with patch.object(winston_app, "send_email_fn") as sender:
            response = self.client.post("/approve", json={"index": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["stage"], "approved")
        sender.assert_not_called()

    def test_followup_route_is_permanently_removed(self):
        with patch.object(winston_app, "send_email_fn") as sender:
            response = self.client.post("/followups")
        self.assertEqual(response.status_code, 410)
        self.assertFalse(response.get_json()["enabled"])
        sender.assert_not_called()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_command_center_renders_external_assets(self):
        response = self.client.get("/")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Editorial Review", page)
        self.assertIn("command-center.css", page)
        self.assertIn("command-center.js", page)
        self.assertNotIn("approve_all", page)

    def test_dashboard_metrics_are_real(self):
        response = self.client.get("/api/dashboard")
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["metrics"]["drafts_ready"], 1)
        self.assertFalse(data["automatic_followups"])
        self.assertEqual(data["ai"]["mode"], "zero-cost")

    def test_skip_removes_only_selected_lead(self):
        response = self.client.post("/skip", json={"index": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(winston_app.state["pending"], [])

    def test_existing_contact_batch_validates_limit(self):
        response = self.client.post("/draft-existing", json={"limit": 0})
        self.assertEqual(response.status_code, 400)

    @patch("winston_app.threading.Thread")
    def test_existing_contact_batch_starts_bounded_background_job(self, thread):
        response = self.client.post("/draft-existing", json={"limit": 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "drafting_existing")
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
