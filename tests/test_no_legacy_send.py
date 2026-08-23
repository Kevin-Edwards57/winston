"""Enforce exactly one production email-send path.

The legacy follow-up sender delivered mail straight from followups.json, skipping
suppression, idempotency, atomic claiming, audit logging, and human confirmation.
131 follow-ups went out that way. It was deleted on 2026-08-23, not disabled.

A second send path is the mechanism by which duplicate-send incidents happen, so
re-introducing one must fail the build.
"""
import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "winston_app.py"

REMOVED_SYMBOLS = ["check_and_send_followups", "followup_scheduler", "write_followup_email", "save_followups"]


class NoLegacySendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(APP.read_text())

    def _defined_functions(self):
        return {n.name for n in ast.walk(self.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def test_legacy_functions_do_not_exist(self):
        defined = self._defined_functions()
        for symbol in REMOVED_SYMBOLS:
            self.assertNotIn(symbol, defined, f"{symbol}() was reintroduced — it bypasses the state machine")

    def test_exactly_one_call_site_to_send_email_fn(self):
        """send_email_fn may be invoked from exactly one place: confirm_send()."""
        callers = []
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                        and inner.func.id == "send_email_fn":
                    callers.append(node.name)
        self.assertEqual(
            callers, ["confirm_send"],
            f"send_email_fn must be called only from confirm_send(); found callers: {callers}",
        )

    def test_smtp_is_used_in_exactly_one_function(self):
        senders = []
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr in {"send_message", "sendmail", "SMTP_SSL", "SMTP"}:
                    senders.append(node.name)
        self.assertEqual(
            sorted(set(senders)), ["send_email_fn"],
            f"SMTP must be reachable from send_email_fn only; found: {sorted(set(senders))}",
        )

    def test_no_scheduler_thread_started(self):
        source = APP.read_text()
        self.assertNotIn("target=followup_scheduler", source,
                         "The unsafe follow-up scheduler thread was restarted")

    def test_followups_route_reports_permanent_removal(self):
        import winston_app
        client = winston_app.app.test_client()
        response = client.post("/followups")
        self.assertEqual(response.status_code, 410, "Route should report 410 Gone, not merely disabled")
        self.assertFalse(response.get_json()["enabled"])


if __name__ == "__main__":
    unittest.main()
