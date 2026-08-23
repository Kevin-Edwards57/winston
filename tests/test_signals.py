"""Digital signal extraction.

The tests that matter most here are the ones about *absence*. A scoring system that
treats "not found in the HTML" as "the business does not have it" invents opportunities
that are not real — and the whole point of this layer is to be the evidence underneath
recommendations, not a generator of confident guesses.

Both false-negative classes below were caught against live prospect sites, not imagined:
Wix and Squarespace businesses reporting "no contact form", and a barbershop reporting
"no SSL" because the stored URL was http:// and redirected.
"""
import tempfile
import unittest
from pathlib import Path

from winston.repository import WinstonRepository
from winston.signals import (
    SignalStore, extract_signals, merge_page_signals, research_site,
)

SERVER_RENDERED = """<!doctype html><html><head>
<title>Joe's Barbershop</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Cuts in BK">
<link rel=stylesheet href="/wp-content/themes/x/style.css">
<script src="https://www.googletagmanager.com/gtag/js?id=G-ABC"></script>
</head><body><h1>Joe's</h1>
<a href="https://booksy.com/en-us/123_joes">Book</a>
<form><input type="email"><input type="submit"></form>
<footer>&copy; 2019 Joe</footer></body></html>"""

WIX_SHELL = """<!doctype html><html><head><title>Wix Biz</title>
<meta name="viewport" content="width=device-width">
<script src="https://static.parastorage.com/services/main.js"></script>
</head><body><div id="SITE_CONTAINER"></div></body></html>"""

PLAIN_OLD_SITE = """<html><head><title>Old Diner</title></head>
<body><table><tr><td>Menu</td></tr></table><p>Copyright 2011</p></body></html>"""


def by_name(html, url=""):
    return {s.name: s for s in extract_signals(html, url)}


class ExtractionTests(unittest.TestCase):
    def test_detects_platforms_with_evidence(self):
        signals = by_name(SERVER_RENDERED, "https://joes.com")
        self.assertEqual(signals["cms"].value, "WordPress")
        self.assertEqual(signals["online_booking"].value, "Booksy")
        self.assertIn("booksy.com", signals["online_booking"].evidence)

    def test_every_signal_carries_evidence(self):
        for signal in extract_signals(SERVER_RENDERED, "https://joes.com"):
            self.assertTrue(signal.evidence, f"{signal.name} has no evidence")
            self.assertGreater(signal.confidence, 0.0)

    def test_confidence_reflects_specificity(self):
        """A vendor fingerprint is stronger evidence than a generic link path."""
        vendor = by_name('<a href="https://calendly.com/x">Book</a>')["online_booking"]
        generic = by_name('<a href="/book-now">Book</a>')["online_booking"]
        self.assertGreater(vendor.confidence, generic.confidence)

    def test_unfetchable_page_yields_no_signals(self):
        """A site that could not be read is unknown, not maximally broken."""
        self.assertEqual(extract_signals("", "https://x.com"), [])
        self.assertEqual(extract_signals("   ", "https://x.com"), [])

    # ── absence handling ─────────────────────────────────────────────────

    def test_client_rendered_pages_withhold_negatives(self):
        """Wix/Squarespace build the DOM in JS; static HTML cannot disprove a form."""
        signals = by_name(WIX_SHELL, "https://x.wixsite.com")
        self.assertEqual(signals["client_rendered"].value, "Wix")
        for withheld in ("has_contact_form", "has_analytics", "has_chat_widget"):
            self.assertNotIn(withheld, signals,
                             f"{withheld} must stay unknown on a client-rendered page")

    def test_server_rendered_pages_do_report_negatives(self):
        """The guard must not suppress genuine negatives on plain HTML."""
        signals = by_name(PLAIN_OLD_SITE, "http://old.com")
        self.assertIn("has_contact_form", signals)
        self.assertFalse(signals["has_contact_form"].value)
        self.assertFalse(signals["mobile_responsive"].value)

    def test_positive_detection_survives_client_rendering(self):
        """Withholding applies only to negatives; a real match still counts."""
        html = WIX_SHELL.replace("</body>", '<script src="https://embed.tawk.to/x"></script></body>')
        signals = by_name(html, "https://x.wixsite.com")
        self.assertTrue(signals["has_chat_widget"].value)

    def test_ssl_reflects_the_final_url(self):
        """A stored http:// that redirects to https:// must not read as insecure."""
        self.assertTrue(by_name(PLAIN_OLD_SITE, "https://old.com")["has_ssl"].value)
        self.assertFalse(by_name(PLAIN_OLD_SITE, "http://old.com")["has_ssl"].value)

    def test_staleness_is_measured_not_assumed(self):
        signals = by_name(SERVER_RENDERED, "https://joes.com")
        self.assertEqual(signals["copyright_year"].value, 2019)
        self.assertGreater(signals["years_since_copyright_update"].value, 0)

    def test_malformed_html_does_not_crash(self):
        for junk in ("<html><body><div><p>unclosed", "<<<>>>", "<html>" + "<div>" * 500):
            extract_signals(junk, "https://x.com")


class MergeTests(unittest.TestCase):
    def test_capability_found_on_any_page_counts(self):
        home = extract_signals("<html><head><title>H</title></head><body></body></html>", "https://x.com")
        contact = extract_signals('<a href="https://calendly.com/x">Book</a>', "https://x.com/contact")
        merged = {s.name: s.value for s in merge_page_signals([("h", home), ("c", contact)])}
        self.assertEqual(merged["online_booking"], "Calendly")

    def test_a_positive_observation_beats_an_earlier_negative(self):
        home = extract_signals(PLAIN_OLD_SITE, "https://x.com")
        contact = extract_signals("<html><body><form><input type=email></form></body></html>",
                                  "https://x.com/contact")
        merged = {s.name: s.value for s in merge_page_signals([("h", home), ("c", contact)])}
        self.assertTrue(merged["has_contact_form"],
                        "a form on the contact page outranks its absence on the homepage")


class SignalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "signals.db")
        self.repo.initialize()
        self.store = SignalStore(self.repo)
        self.store.initialize()
        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Acme", "email": "a@example.com", "place_id": "p1"}, "test")

    def tearDown(self):
        self.temp.cleanup()

    def test_signals_persist_with_provenance(self):
        self.store.record(self.contact_id, extract_signals(SERVER_RENDERED, "https://joes.com"))
        stored = self.store.for_contact(self.contact_id)
        self.assertEqual(stored["cms"]["value"], "WordPress")
        self.assertIn("wp-content", stored["cms"]["evidence"])
        self.assertEqual(stored["cms"]["source_url"], "https://joes.com")
        self.assertTrue(stored["cms"]["observed_at"])

    def test_recording_twice_updates_rather_than_duplicates(self):
        self.store.record(self.contact_id, extract_signals(SERVER_RENDERED, "https://joes.com"))
        first = len(self.store.for_contact(self.contact_id))
        self.store.record(self.contact_id, extract_signals(SERVER_RENDERED, "https://joes.com"))
        self.assertEqual(len(self.store.for_contact(self.contact_id)), first)

    def test_newer_observation_replaces_older(self):
        self.store.record(self.contact_id, extract_signals(PLAIN_OLD_SITE, "http://x.com"))
        self.assertFalse(self.store.for_contact(self.contact_id)["mobile_responsive"]["value"])
        self.store.record(self.contact_id, extract_signals(SERVER_RENDERED, "https://x.com"))
        self.assertTrue(self.store.for_contact(self.contact_id)["mobile_responsive"]["value"])

    def test_research_runs_distinguish_never_looked_from_found_nothing(self):
        self.assertIsNone(self.store.last_researched(self.contact_id))
        run_id = self.store.start_run(self.contact_id, "https://x.com")
        self.store.complete_run(run_id, status="ok", pages=1, signals=0)
        self.assertIsNotNone(self.store.last_researched(self.contact_id),
                             "a completed run with zero signals is still a run")

    def test_failed_run_is_not_counted_as_researched(self):
        run_id = self.store.start_run(self.contact_id, "https://x.com")
        self.store.complete_run(run_id, status="unreachable", error="timeout")
        self.assertIsNone(self.store.last_researched(self.contact_id))

    def test_coverage_reports_unresearched_honestly(self):
        coverage = self.store.coverage()
        self.assertEqual(coverage["researched"], 0)
        self.assertEqual(coverage["unresearched"], coverage["contacts"])
        self.store.record(self.contact_id, extract_signals(SERVER_RENDERED, "https://x.com"))
        self.assertEqual(self.store.coverage()["researched"], 1)


class ResearchPipelineTests(unittest.TestCase):
    def test_unreachable_site_reports_status_not_signals(self):
        from unittest.mock import patch
        with patch("winston.signals.fetch_page", return_value=("", "https://x.com")):
            signals, pages, status = research_site("https://x.com")
        self.assertEqual(signals, [])
        self.assertEqual(pages, 0)
        self.assertEqual(status, "unreachable")

    def test_pipeline_stops_at_max_pages(self):
        from unittest.mock import patch
        with patch("winston.signals.fetch_page",
                   return_value=(SERVER_RENDERED, "https://x.com")) as fetch:
            research_site("https://x.com", max_pages=2)
        self.assertLessEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
