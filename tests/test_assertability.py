"""Commercial assertability.

The rule this file protects: **observed is not inferred, and inferred is not a sales
claim.** It exists because a detector that could not distinguish "I did not see it" from
"it is not there" produced 13 opportunities of which 11 were wrong, and because the same
shape applies to every problem derived from something not being found.

A problem may be recorded, shown to a reviewer, and still be forbidden from entering an
email. That is the whole point.
"""
import unittest

from winston.fit import (
    ASSERTABLE_CONFIDENCE, CONFIRMED, INFERRED, Problem, derive_problems,
)
from winston.signals import extract_signals

STATIC_SITE = """<html><head><title>Old</title></head><body>
<table><tr><td>Menu</td></tr></table><p>Copyright 2013</p></body></html>"""
SHOPIFY = ('<html><head><title>S</title></head><body>'
           '<script src="https://cdn.shopify.com/s/files/x.js"></script></body></html>')


def signals_for(html, url="https://x.com"):
    return {s.name: {"value": s.value, "confidence": s.confidence, "evidence": s.evidence}
            for s in extract_signals(html, url)}


class AssertabilityTests(unittest.TestCase):
    def test_a_confirmed_high_confidence_problem_is_assertable(self):
        problem = Problem("x", "X", 0.8, "observed directly", 0.9, assertability=CONFIRMED)
        self.assertTrue(problem.commercially_assertable)

    def test_an_inferred_problem_is_never_assertable(self):
        """Even at high confidence. Inference is not observation."""
        problem = Problem("x", "X", 0.8, "derived from absence", 0.99, assertability=INFERRED)
        self.assertFalse(problem.commercially_assertable)

    def test_low_confidence_is_not_assertable_even_when_confirmed(self):
        problem = Problem("x", "X", 0.8, "weak", ASSERTABLE_CONFIDENCE - 0.01,
                          assertability=CONFIRMED)
        self.assertFalse(problem.commercially_assertable)

    def test_assertability_is_serialised(self):
        payload = Problem("x", "X", 0.8, "e", 0.9).as_dict()
        self.assertIn("assertability", payload)
        self.assertIn("commercially_assertable", payload)
        self.assertIn("limitations", payload)


class CapabilityGapTests(unittest.TestCase):
    """Gaps derived from absence are inferred, whatever their confidence."""

    def test_missing_booking_is_inferred_not_confirmed(self):
        problems = {p.code: p for p in derive_problems(signals_for(STATIC_SITE),
                                                       industry="barbershop")}
        booking = problems.get("no_online_booking")
        self.assertIsNotNone(booking)
        self.assertEqual(booking.assertability, INFERRED)
        self.assertFalse(booking.commercially_assertable)

    def test_missing_ordering_is_inferred(self):
        problems = {p.code: p for p in derive_problems(signals_for(STATIC_SITE),
                                                       industry="restaurant")}
        ordering = problems.get("no_online_ordering")
        self.assertIsNotNone(ordering)
        self.assertEqual(ordering.assertability, INFERRED)

    def test_capability_gaps_record_their_limitations(self):
        problems = {p.code: p for p in derive_problems(signals_for(STATIC_SITE),
                                                       industry="barbershop")}
        self.assertTrue(problems["no_online_booking"].limitations,
                        "an inferred gap must say what could be hiding the capability")

    def test_directly_observed_problems_stay_confirmed(self):
        """A missing h1 is in the markup. Nothing can be hiding it."""
        problems = {p.code: p for p in derive_problems(signals_for(STATIC_SITE),
                                                       industry="barbershop")}
        self.assertEqual(problems["weak_seo_basics"].assertability, CONFIRMED)
        self.assertTrue(problems["weak_seo_basics"].commercially_assertable)


class MeasurementTests(unittest.TestCase):
    def test_platform_analytics_produce_an_inferred_problem_not_a_confirmed_one(self):
        problems = {p.code: p for p in derive_problems(signals_for(SHOPIFY), industry="florist")}
        measurement = problems.get("no_measurement")
        if measurement:
            self.assertEqual(measurement.assertability, INFERRED)
            self.assertFalse(measurement.commercially_assertable)

    def test_confirmed_absence_is_assertable(self):
        problems = {p.code: p for p in derive_problems(signals_for(STATIC_SITE),
                                                       industry="florist")}
        measurement = problems.get("no_measurement")
        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.assertability, CONFIRMED)


class SalesBriefGateTests(unittest.TestCase):
    """The Writer may only see what Winston will stand behind."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock
        from winston.catalog import Catalog
        from winston.fit import FitEngine
        from winston.repository import WinstonRepository
        from winston.signals import SignalStore
        from winston.writer import Writer

        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "gate.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()
        self.signals = SignalStore(self.repo)
        self.signals.initialize()
        self.fit = FitEngine(self.repo, self.catalog, self.signals)
        ai = MagicMock()
        self.writer = Writer(self.repo, self.catalog, self.signals, self.fit, ai)

        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Cuts", "email": "c@example.com", "place_id": "p1",
             "type": "barbershop", "website": "http://c.com"}, "test")
        self.signals.record(self.contact_id, extract_signals(STATIC_SITE, "http://c.com"))
        run = self.signals.start_run(self.contact_id, "http://c.com")
        self.signals.complete_run(run, status="ok", pages=1, signals=9)
        self.catalog.verify("website-service")

    def tearDown(self):
        self.temp.cleanup()

    def test_assessment_keeps_both_kinds(self):
        result = self.fit.assess(self.contact_id)
        self.assertTrue(result.assertable_problems)
        self.assertTrue(result.inferred_problems)
        self.assertEqual(len(result.problems),
                         len(result.assertable_problems) + len(result.inferred_problems))

    def test_only_assertable_problems_reach_the_brief(self):
        brief = self.writer.build_brief(self.contact_id)
        codes = {p["code"] for p in brief["observed_problems"]}
        self.assertNotIn("no_online_booking", codes,
                         "an inferred gap reached the sales brief")
        for problem in brief["observed_problems"]:
            self.assertTrue(problem["commercially_assertable"])

    def test_withheld_problems_are_reported_with_their_reason(self):
        brief = self.writer.build_brief(self.contact_id)
        withheld = " ".join(brief["withheld_low_confidence"])
        self.assertIn("inferred", withheld,
                      "the reviewer must see why a problem was withheld")

    def test_offer_matching_ignores_inferred_problems(self):
        """A prospect cannot be matched on evidence Winston will not state."""
        result = self.fit.assess(self.contact_id)
        if result.recommended_service:
            solved = {s.casefold().replace(" ", "_")
                      for s in (self.catalog.get(result.recommended_service["slug"])
                                or {}).get("problems_solved", [])}
            assertable = {p.code for p in result.assertable_problems}
            self.assertTrue(solved & assertable,
                            "the matched offer must address an assertable problem")


if __name__ == "__main__":
    unittest.main()
