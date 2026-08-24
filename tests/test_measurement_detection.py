"""Measurement detection.

A 50-prospect experiment produced 13 "no analytics" findings. Eleven were wrong. One
was a Shopify store, which measures by construction. Another had Google Tag Manager
loading analytics after the document. A third carried a UTM parameter added by Google
Business Profile, not by the business.

The lesson is narrow and important: **absence of evidence is not evidence of absence.**
A detector that reads static HTML cannot see analytics loaded by a tag manager, gated
behind a consent banner, injected by a platform, or rendered client-side. Treating an
unseen tag as an absent one manufactured commercial opportunities that were not there.

So measurement is four states, and only one of them may become something to sell.
"""
import unittest

from winston.fit import derive_problems
from winston.signals import extract_signals


def state_of(html, url="https://example.com"):
    signals = {s.name: s for s in extract_signals(html, url)}
    return signals.get("measurement_state")


def signals_dict(html, url="https://example.com"):
    return {s.name: {"value": s.value, "confidence": s.confidence, "evidence": s.evidence}
            for s in extract_signals(html, url)}


class DetectionStateTests(unittest.TestCase):
    def test_clear_ga4_is_detected(self):
        state = state_of('<html><head><script src="https://www.googletagmanager.com/gtag/js?id=G-A">'
                         '</script></head><body>x</body></html>')
        self.assertEqual(state.value, "detected")
        self.assertGreater(state.confidence, 0.8)

    def test_tag_manager_is_detected(self):
        state = state_of('<html><head><script src="https://www.googletagmanager.com/gtm.js?id=GTM-X">'
                         '</script></head><body>x</body></html>')
        self.assertEqual(state.value, "detected")

    def test_meta_pixel_is_detected(self):
        state = state_of('<html><head><script src="https://connect.facebook.net/en_US/fbevents.js">'
                         '</script></head><body>x</body></html>')
        self.assertEqual(state.value, "detected")

    def test_shopify_without_a_visible_script_is_not_an_absence(self):
        """A Shopify store has analytics whether or not a tag appears in markup."""
        state = state_of('<html><head><title>S</title></head><body>'
                         '<script src="https://cdn.shopify.com/s/files/x.js"></script></body></html>')
        self.assertEqual(state.value, "not_detected")
        self.assertLess(state.confidence, 0.5, "a platform-supplied absence must be low confidence")

    def test_consent_manager_defers_analytics(self):
        state = state_of('<html><head><script src="https://consent.cookiebot.com/uc.js">'
                         '</script></head><body>x</body></html>')
        self.assertEqual(state.value, "not_detected")

    def test_client_rendered_page_is_not_an_absence(self):
        state = state_of('<html><head><script src="https://static.parastorage.com/x.js"></script>'
                         '</head><body><div id="SITE_CONTAINER"></div></body></html>')
        self.assertEqual(state.value, "not_detected")

    def test_many_scripts_prevent_a_confirmed_absence(self):
        scripts = "".join(f'<script src="https://cdn{i}.example.com/a.js"></script>' for i in range(20))
        state = state_of(f"<html><head>{scripts}</head><body>x</body></html>")
        self.assertEqual(state.value, "not_detected")

    def test_genuinely_static_site_is_a_confirmed_absence(self):
        """Server-rendered, no platform, no tag manager, few scripts. Worth asserting."""
        state = state_of('<html><head><title>Old</title></head><body><p>Call us</p></body></html>')
        self.assertEqual(state.value, "confirmed_absence")
        self.assertGreater(state.confidence, 0.6)

    def test_unfetchable_site_yields_no_state_at_all(self):
        self.assertEqual(extract_signals("", "https://x.com"), [])

    def test_limitations_are_recorded_when_they_apply(self):
        signals = {s.name: s for s in extract_signals(
            '<html><head><script src="https://cdn.shopify.com/s/files/x.js"></script></head>'
            '<body>x</body></html>', "https://x.com")}
        self.assertIn("measurement_limitations", signals)
        self.assertTrue(signals["measurement_limitations"].value)


class CommercialRuleTests(unittest.TestCase):
    """Only a confirmed absence may become something to sell."""

    def test_not_detected_never_becomes_a_problem(self):
        for html in (
            '<html><head><script src="https://cdn.shopify.com/s/files/x.js"></script></head><body>x</body></html>',
            '<html><head><script src="https://consent.cookiebot.com/uc.js"></script></head><body>x</body></html>',
            '<html><head><script src="https://static.parastorage.com/x.js"></script></head><body><div id="SITE_CONTAINER"></div></body></html>',
        ):
            with self.subTest(html=html[:50]):
                problems = {p.code: p for p in derive_problems(signals_dict(html), industry="florist")}
                measurement = problems.get("no_measurement")
                # It may be recorded for the reviewer, but never stated to a prospect.
                if measurement:
                    self.assertFalse(measurement.commercially_assertable,
                                     "an unseen tag became a sales claim")

    def test_confirmed_absence_does_become_an_assertable_problem(self):
        signals = signals_dict('<html><head><title>Old</title></head><body><p>Call</p></body></html>')
        problems = {p.code: p for p in derive_problems(signals, industry="florist")}
        self.assertIn("no_measurement", problems)
        self.assertTrue(problems["no_measurement"].commercially_assertable)

    def test_detected_never_becomes_a_problem(self):
        signals = signals_dict('<html><head><script src="https://www.googletagmanager.com/gtag/js?id=G-A">'
                               '</script></head><body>x</body></html>')
        codes = {p.code for p in derive_problems(signals, industry="florist")}
        self.assertNotIn("no_measurement", codes)

    def test_unresearched_prospect_has_no_measurement_problem(self):
        codes = {p.code for p in derive_problems({}, industry="florist")}
        self.assertNotIn("no_measurement", codes)

    def test_utm_parameter_is_not_treated_as_proof_either_way(self):
        """A UTM tag may be added by Google Business Profile, not by the business."""
        url = "http://x.com/?utm_source=google&utm_campaign=gbp_website_link"
        state = state_of('<html><head><title>T</title></head><body><p>Hi</p></body></html>', url)
        self.assertEqual(state.value, "confirmed_absence",
                         "a UTM parameter must not by itself imply measurement exists")


if __name__ == "__main__":
    unittest.main()
