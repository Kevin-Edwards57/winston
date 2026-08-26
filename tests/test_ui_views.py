"""The UI must not expose raw JSON as a workflow, or invent metrics.

Two sidebar items used to call window.open on an API endpoint, dumping unstyled
JSON at the operator. These tests exist so that never returns, and so the views
built on top of the newer backend stay wired.
"""
import re
import unittest
from pathlib import Path

import winston_app

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "templates" / "dashboard.html").read_text()
SCRIPT = (ROOT / "static" / "command-center.js").read_text()

# Comments describe what was removed and necessarily quote it. Scanning raw source
# flags the tombstone rather than a live call, so checks run against code only.
CODE = re.sub(r"/\*.*?\*/", "", SCRIPT, flags=re.S)
CODE = re.sub(r"^\s*//.*$", "", CODE, flags=re.M)

VIEWS = ("editorial", "sent", "social", "blocked", "pricing", "catalog", "providers",
         "research", "projects", "agents", "ops")


class NavigationTests(unittest.TestCase):
    def test_no_api_endpoint_is_opened_as_a_user_workflow(self):
        """window.open on an API route is how raw JSON reached the operator."""
        offenders = re.findall(r"window\.open\(\s*['\"`](/[^'\"`]+)", CODE)
        self.assertEqual(offenders, [], f"raw API responses opened as UI: {offenders}")

    def test_every_nav_item_maps_to_a_real_view(self):
        declared = set(re.findall(r'data-view="([a-z]+)"', TEMPLATE))
        for view in declared:
            if view in ("overview", "leads", "activity"):
                continue  # these act on the editorial view rather than owning one
            self.assertIn(f'id="view-{view}"', TEMPLATE, f"{view} has no view container")

    def test_all_views_have_containers(self):
        for view in VIEWS:
            self.assertIn(f'id="view-{view}"', TEMPLATE, f"missing container for {view}")

    def test_router_knows_every_view(self):
        declared = re.search(r"const VIEWS\s*=\s*\[([^\]]+)\]", SCRIPT)
        self.assertIsNotNone(declared)
        names = set(re.findall(r"'([a-z]+)'", declared.group(1)))
        self.assertEqual(names, set(VIEWS))

    def test_each_view_has_a_loader(self):
        for view in VIEWS:
            if view == "editorial":
                continue
            self.assertIn(f"function load{view.capitalize()}", SCRIPT,
                          f"{view} view has no data loader")


class HonestyTests(unittest.TestCase):
    def test_contact_completeness_vanity_metric_is_gone(self):
        """Counting whether an email exists is not an opportunity score."""
        self.assertNotIn("Contact Completeness", TEMPLATE)
        self.assertNotIn("contactCompleteness(lead);$('completeness-score')", SCRIPT)

    def test_opportunity_comes_from_the_fit_engine(self):
        self.assertIn("/fit", SCRIPT)
        self.assertIn("COMMERCIAL_OPPORTUNITY", SCRIPT)

    def test_unresearched_prospects_show_unknown_not_zero(self):
        self.assertIn("Opportunity is unknown, not zero", SCRIPT)

    def test_pricing_view_labels_assumptions(self):
        self.assertIn("assumption", SCRIPT)
        self.assertIn("evidence_backed", SCRIPT)

    def test_guardian_view_offers_no_bypass_control(self):
        """Look for a control, not the word.

        The Agent Center describes Guardian as having "no bypass", so scanning for
        the bare word flags the documentation rather than a mechanism. What matters
        is that no identifier or label offers to send a blocked draft.
        """
        control_patterns = (
            r"\bbypass\s*[=:(]",          # a bypass variable, property or call
            r"\bforceSend\b",
            r"send\s*anyway",
            r"override\s*guardian",
            r"skip\s*guardian",
        )
        for pattern in control_patterns:
            match = re.search(pattern, CODE, re.IGNORECASE)
            self.assertIsNone(match, f"a Guardian bypass control appeared: {pattern!r}")

    def test_blocked_view_has_no_send_action(self):
        """The blocked list renders rules and reasons, never an action to proceed."""
        loader = re.search(r"async function loadBlocked\(\)\{.*?\n\}", CODE, re.S)
        self.assertIsNotNone(loader)
        body = loader.group(0)
        for forbidden in ("confirmSend", "approve(", "/send-jobs"):
            self.assertNotIn(forbidden, body,
                             "the blocked view must not offer a path to sending")


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = winston_app.app.test_client()

    def test_every_view_backing_route_responds(self):
        for route in ("/sent", "/social_leads", "/drafts/blocked", "/ratecard",
                      "/pricing", "/catalog", "/providers", "/costs", "/health"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_dashboard_renders_all_views(self):
        page = self.client.get("/").get_data(as_text=True)
        for view in VIEWS:
            self.assertIn(f'id="view-{view}"', page)

    def test_static_assets_are_cache_busted(self):
        """Editing the dashboard JS must not leave a stale copy in the browser."""
        page = self.client.get("/").get_data(as_text=True)
        self.assertRegex(page, r"command-center\.js\?v=\d+")
        self.assertRegex(page, r"command-center\.css\?v=\d+")

    def test_research_batch_is_bounded(self):
        """1,395 sequential fetches is not something to start by accident."""
        self.assertEqual(self.client.post("/research/batch", json={"limit": 0}).status_code, 400)
        self.assertEqual(self.client.post("/research/batch", json={"limit": 5000}).status_code, 400)

    def test_research_progress_reports_coverage(self):
        payload = self.client.get("/research/progress").get_json()
        self.assertIn("coverage", payload)
        self.assertIn("unresearched", payload["coverage"])

    def test_agent_center_lists_only_implemented_roles_as_active(self):
        """A function is not an agent because it has a good name.

        This previously inspected a hardcoded array in the frontend. The Agent Center
        now renders whatever /agents returns, and that endpoint derives status from the
        database, so the invariant belongs against the API rather than the script.
        """
        payload = self.client.get("/agents").get_json()
        for agent in payload["agents"]:
            if agent["operational"]:
                self.assertTrue(agent["implemented"],
                                f"{agent['name']} is operational with no implementation")
        unbuilt = [a for a in payload["agents"] if not a["operational"]]
        self.assertTrue(unbuilt, "unbuilt roles must be shown, not hidden")
        for agent in unbuilt:
            self.assertTrue(agent["status_reason"],
                            f"{agent['name']} is not operational without saying why")

    def test_agent_center_renders_from_the_api_not_a_constant(self):
        """A hardcoded list drifts from the code it claims to describe."""
        self.assertIn("api('/agents')", SCRIPT)
        self.assertNotIn("const AGENTS=[", SCRIPT)

    def test_ml_is_not_presented_as_an_agent(self):
        payload = self.client.get("/agents").get_json()
        self.assertNotIn("ML", {a["name"] for a in payload["agents"]})
        self.assertEqual(payload["ml"]["status"], "insufficient_data")

    def test_command_palette_exists(self):
        self.assertIn("openPalette", SCRIPT)
        self.assertIn("COMMANDS", SCRIPT)

    def test_projects_route_declares_no_builder_api(self):
        payload = self.client.get("/projects").get_json()
        self.assertFalse(payload["status"]["builder_api_available"])

    def test_costs_route_reports_zero_and_no_spend_capability(self):
        payload = self.client.get("/costs").get_json()
        self.assertEqual(payload["ai_cost"]["month_to_date_usd"], 0.0)
        self.assertEqual(payload["spend_capable_providers"], [])


if __name__ == "__main__":
    unittest.main()
