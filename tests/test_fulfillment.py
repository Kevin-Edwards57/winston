"""Winston to Website Builder handoff.

The Builder exposes no HTTP API: its app directory contains no route handlers and the
core path runs in the browser. So these tests assert the honest shape of the seam.
Winston produces an importable brief and records what a human reports, and nothing here
pretends a live connection exists.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from winston.catalog import Catalog
from winston.fit import FitEngine
from winston.fulfillment import (
    BUILDER_FULFILLED, FulfilmentBridge, INDUSTRY_SECTIONS, NotBuilderFulfilled,
    PROJECT_STATUSES,
)
from winston.repository import WinstonRepository
from winston.signals import SignalStore, extract_signals

STALE_SITE = """<html><head><title>Irie</title></head><body>
<table><tr><td>Menu</td></tr></table><p>Copyright 2013</p></body></html>"""


class FulfilmentBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "fulfil.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()
        self.signals = SignalStore(self.repo)
        self.signals.initialize()
        self.fit = FitEngine(self.repo, self.catalog, self.signals)
        self.bridge = FulfilmentBridge(self.repo, self.catalog, self.signals, self.fit)
        self.bridge.initialize()

        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Irie Jerk", "email": "irie@example.com", "place_id": "p1",
             "type": "jamaican restaurant", "address": "Brooklyn, NY",
             "website": "http://irie.com", "instagram": "@iriejerk"}, "test")
        self.signals.record(self.contact_id, extract_signals(STALE_SITE, "http://irie.com"))
        run = self.signals.start_run(self.contact_id, "http://irie.com")
        self.signals.complete_run(run, status="ok", pages=1, signals=9)

    def tearDown(self):
        self.temp.cleanup()


class HandoffTests(FulfilmentBase):
    def test_handoff_matches_the_builder_contract(self):
        """Every field must exist in the Builder's real SiteData type."""
        handoff = self.bridge.build_handoff(self.contact_id, "website-redesign")
        for field in ("businessName", "industry", "phone", "email", "address",
                      "seoTitle", "seoKeywords", "menu", "assetCount", "logoLabel"):
            self.assertIn(field, handoff.site_data)
        self.assertEqual(handoff.as_dict()["contract"], "lib/site-generator.ts :: SiteData")

    def test_verified_facts_carry_across(self):
        handoff = self.bridge.build_handoff(self.contact_id, "website-redesign")
        self.assertEqual(handoff.site_data["businessName"], "Irie Jerk")
        self.assertEqual(handoff.site_data["phone"], "")
        self.assertEqual(handoff.known_assets["existing_website"], "http://irie.com")

    def test_observed_problems_reach_the_builder(self):
        """A redesign brief is better with the diagnosis that sold it."""
        handoff = self.bridge.build_handoff(self.contact_id, "website-redesign")
        self.assertTrue(handoff.observed_problems)
        for problem in handoff.observed_problems:
            self.assertTrue(problem["evidence"])

    def test_unobservable_fields_are_listed_as_gaps_not_invented(self):
        handoff = self.bridge.build_handoff(self.contact_id, "website-redesign")
        self.assertEqual(handoff.site_data["story"], "")
        self.assertEqual(handoff.site_data["ownerName"], "")
        joined = " ".join(handoff.gaps).casefold()
        for expected in ("story", "menu", "logo", "photography"):
            self.assertIn(expected, joined)

    def test_menu_is_never_fabricated(self):
        """Winston does not read menus, so the field stays empty and is flagged."""
        handoff = self.bridge.build_handoff(self.contact_id, "website-redesign")
        self.assertEqual(handoff.site_data["menu"], [])
        self.assertTrue(any("menu" in gap.casefold() for gap in handoff.gaps))

    def test_sections_are_industry_aware(self):
        handoff = self.bridge.build_handoff(self.contact_id, "website-redesign")
        self.assertEqual(handoff.suggested_sections,
                         INDUSTRY_SECTIONS["jamaican restaurant"])
        self.assertIn("menu", handoff.suggested_sections)

    def test_non_builder_service_is_refused(self):
        """Only website work runs through the Builder."""
        with self.assertRaises(NotBuilderFulfilled):
            self.bridge.build_handoff(self.contact_id, "ai-automation")

    def test_unknown_contact_raises(self):
        with self.assertRaises(KeyError):
            self.bridge.build_handoff("nope", "website-redesign")

    def test_every_builder_service_can_produce_a_handoff(self):
        for slug in BUILDER_FULFILLED:
            with self.subTest(service=slug):
                self.bridge.build_handoff(self.contact_id, slug)


class ProjectTests(FulfilmentBase):
    def test_project_freezes_the_brief(self):
        project = self.bridge.create_project(self.contact_id, "website-redesign",
                                             agreed_price_usd=1100)
        self.assertEqual(project["status"], "handoff_ready")
        self.assertEqual(project["agreed_price_usd"], 1100)
        self.assertTrue(project["handoff"]["site_data"]["businessName"])

    def test_duplicate_engagements_are_blocked(self):
        self.bridge.create_project(self.contact_id, "website-redesign")
        with self.assertRaises(ValueError):
            self.bridge.create_project(self.contact_id, "website-redesign")

    def test_status_transitions_are_recorded_with_an_actor(self):
        project = self.bridge.create_project(self.contact_id, "website-redesign")
        updated = self.bridge.update_status(project["id"], "in_production", actor="kevin")
        self.assertEqual(updated["status"], "in_production")
        self.assertEqual(updated["status_reported_by"], "kevin")
        self.assertTrue(updated["status_reported_at"])

    def test_status_is_labelled_operator_reported(self):
        """With no API there is nothing to poll, and the data says so."""
        project = self.bridge.create_project(self.contact_id, "website-redesign")
        self.assertEqual(self.bridge.get_project(project["id"])["status_source"],
                         "operator-reported")

    def test_publishing_requires_a_real_url(self):
        project = self.bridge.create_project(self.contact_id, "website-redesign")
        with self.assertRaises(ValueError):
            self.bridge.update_status(project["id"], "published")
        updated = self.bridge.update_status(project["id"], "published",
                                            published_url="https://iriejerk.com")
        self.assertEqual(updated["published_url"], "https://iriejerk.com")

    def test_invalid_status_is_rejected(self):
        project = self.bridge.create_project(self.contact_id, "website-redesign")
        with self.assertRaises(ValueError):
            self.bridge.update_status(project["id"], "probably_done")

    def test_unknown_project_raises(self):
        with self.assertRaises(KeyError):
            self.bridge.update_status("nope", "in_production")

    def test_status_report_declares_no_builder_api(self):
        """The absence of an API is stated, not implied by silence."""
        status = self.bridge.status()
        self.assertFalse(status["builder_api_available"])
        self.assertIn("no HTTP API", status["integration_note"])

    def test_all_statuses_are_enumerated(self):
        status = self.bridge.status()
        self.assertEqual(set(status["by_status"]), set(PROJECT_STATUSES))


class NoFakeApiTests(unittest.TestCase):
    """Winston must not pretend to call a Builder endpoint that does not exist."""

    def test_module_makes_no_http_calls_to_the_builder(self):
        source = (Path(__file__).resolve().parent.parent
                  / "winston" / "fulfillment.py").read_text()
        for forbidden in ("requests.post", "requests.get", "httpx", "urlopen",
                          "/api/projects"):
            self.assertNotIn(forbidden, source,
                             f"{forbidden!r} implies an API the Builder does not expose")

    def test_contract_document_exists(self):
        doc = (Path(__file__).resolve().parent.parent
               / "docs" / "FULFILMENT_CONTRACT.md")
        self.assertTrue(doc.exists())
        text = doc.read_text()
        self.assertIn("no HTTP API", text)
        self.assertIn("SiteData", text)


if __name__ == "__main__":
    unittest.main()
