"""Pricing engine.

The class of test that matters most here is structural: protected characteristics must
be *unable* to reach the pricing arithmetic, not merely absent from it by convention.
Those tests enumerate the terms explicitly and fail the build if any becomes acceptable.

The second theme is refusal. An engine that always produces a number will produce wrong
numbers, and a wrong price is a commercial commitment YardLink has to honour.
"""
import tempfile
import unittest
from pathlib import Path

from winston.catalog import Catalog
from winston.pricing import (
    ALLOWED_PRICING_VARIABLES, DISCOUNT_REASONS, PROTECTED_TERMS,
    DisallowedPricingVariable, NoPricingBasis, PricingEngine,
    ProtectedCharacteristicError, assert_no_protected_characteristics,
    validate_pricing_inputs,
)
from winston.repository import WinstonRepository


class PricingBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "pricing.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()
        self.engine = PricingEngine(self.repo, self.catalog)
        self.problems = [
            {"code": "no_website", "confidence": 0.9},
            {"code": "no_lead_capture", "confidence": 0.8},
        ]

    def tearDown(self):
        self.temp.cleanup()

    def _ready(self, *, low=20, high=40, rate=85):
        self.engine.configure(hourly_rate_usd=rate, min_margin=0.35)
        self.catalog.upsert({"slug": "website-service",
                             "effort_hours_min": low, "effort_hours_max": high})
        self.catalog.verify("website-service")
        return self.catalog.get("website-service")


class ProtectedCharacteristicTests(unittest.TestCase):
    """These must never start passing."""

    def test_every_protected_term_is_rejected(self):
        for term in PROTECTED_TERMS:
            with self.assertRaises(ProtectedCharacteristicError,
                                   msg=f"{term!r} reached the pricing engine"):
                assert_no_protected_characteristics({"complexity": term})

    def test_protected_term_in_a_key_is_rejected(self):
        for key in ("ethnicity", "nationality", "religion", "gender", "race"):
            with self.assertRaises(ProtectedCharacteristicError):
                assert_no_protected_characteristics({key: "anything"})

    def test_cultural_identity_cannot_be_a_pricing_variable(self):
        """Winston knows these markets. It must not price on them."""
        for value in ("caribbean", "jamaican", "guyanese", "haitian", "trinidadian"):
            with self.assertRaises(ProtectedCharacteristicError):
                validate_pricing_inputs({"service_tier": value})

    def test_proxy_variables_are_rejected(self):
        """Indirect proxies are the same discrimination with extra steps."""
        for proxy in ("neighborhood_demographic", "surname", "language_spoken",
                      "zip_demographic"):
            with self.assertRaises((ProtectedCharacteristicError, DisallowedPricingVariable)):
                validate_pricing_inputs({proxy: "x"})

    def test_no_allowlisted_variable_is_itself_protected(self):
        """Matched with the same word-boundary rules the guard uses.

        A naive substring check flags page_count for containing "age", which is
        exactly the false positive the guard's word boundaries exist to avoid.
        """
        for variable in ALLOWED_PRICING_VARIABLES:
            with self.subTest(variable=variable):
                assert_no_protected_characteristics({variable: ""})

    def test_ordinary_commercial_words_are_not_blocked(self):
        for benign in ({"page_count": 12}, {"service_tier": "agency retainer"},
                       {"existing_platform": "Blackbaud CMS"}):
            validate_pricing_inputs(benign)

    def test_unlisted_variables_are_rejected(self):
        with self.assertRaises(DisallowedPricingVariable):
            validate_pricing_inputs({"owner_appears_wealthy": True})

    def test_legitimate_commercial_variables_pass(self):
        validate_pricing_inputs({
            "effort_hours": 30, "integrations": 2, "complexity": "high",
            "locations": 3, "urgency": "standard", "existing_platform": "WordPress",
        })


class RefusalTests(PricingBase):
    def test_refuses_without_a_configured_rate(self):
        self.catalog.upsert({"slug": "website-service",
                             "effort_hours_min": 20, "effort_hours_max": 40})
        with self.assertRaises(NoPricingBasis):
            self.engine.quote(offer=self.catalog.get("website-service"),
                              problems=self.problems)

    def test_refuses_without_an_effort_estimate(self):
        self.engine.configure(hourly_rate_usd=85)
        with self.assertRaises(NoPricingBasis):
            self.engine.quote(offer=self.catalog.get("website-service"),
                              problems=self.problems)

    def test_readiness_names_what_is_missing(self):
        readiness = self.engine.readiness()
        self.assertFalse(readiness["can_quote"])
        self.assertTrue(any("hourly_rate" in m for m in readiness["missing"]))
        self.assertEqual(readiness["quotable_count"], 0)

    def test_readiness_reflects_what_can_actually_be_quoted(self):
        """Readiness must agree with quote(). Reporting false while quoting fine is
        a worse failure than being unable to quote at all."""
        from winston.ratecard import RateCard
        card = RateCard(self.repo, self.catalog)
        card.initialize()
        engine = PricingEngine(self.repo, self.catalog, card)
        engine.configure(hourly_rate_usd=50, min_margin=0.35)
        self.catalog.verify("website-service")

        self.assertFalse(engine.readiness()["can_quote"], "nothing is enabled yet")
        card.enable("website-service")

        readiness = engine.readiness()
        self.assertTrue(readiness["can_quote"])
        self.assertIn("website-service", readiness["quotable_services"])
        engine.quote(offer=self.catalog.get("website-service"), problems=self.problems)

    def test_rejects_a_nonsensical_rate(self):
        with self.assertRaises(ValueError):
            self.engine.configure(hourly_rate_usd=0)
        with self.assertRaises(ValueError):
            self.engine.configure(min_margin=1.5)


class BandTests(PricingBase):
    def test_bands_are_ordered_and_above_cost(self):
        band = self.engine.quote(offer=self._ready(), problems=self.problems)
        self.assertLess(band.delivery_cost_usd, band.floor_usd)
        self.assertLess(band.floor_usd, band.target_usd)
        self.assertLess(band.target_usd, band.premium_usd)

    def test_floor_holds_the_configured_margin(self):
        band = self.engine.quote(offer=self._ready(), problems=self.problems)
        margin_at_floor = (band.floor_usd - band.delivery_cost_usd) / band.floor_usd
        self.assertGreaterEqual(round(margin_at_floor, 3), 0.25)

    def test_price_scales_with_effort_not_with_the_business(self):
        """Doubling the effort estimate should roughly double the price."""
        small = self.engine.quote(offer=self._ready(low=8, high=12), problems=self.problems)
        large = self.engine.quote(offer=self._ready(low=16, high=24), problems=self.problems)
        self.assertAlmostEqual(large.target_usd / small.target_usd, 2.0, delta=0.1)

    def test_integrations_increase_the_estimate(self):
        offer = self._ready()
        simple = self.engine.quote(offer=offer, problems=[{"code": "no_website", "confidence": 0.9}])
        complex_ = self.engine.quote(offer=offer, problems=[
            {"code": "no_website", "confidence": 0.9},
            {"code": "no_online_booking", "confidence": 0.8},
            {"code": "no_online_ordering", "confidence": 0.8},
            {"code": "no_measurement", "confidence": 0.7},
        ])
        self.assertGreater(complex_.effort_hours, simple.effort_hours)
        self.assertGreater(complex_.target_usd, simple.target_usd)

    def test_every_adjustment_carries_a_reason(self):
        band = self.engine.quote(offer=self._ready(), problems=[
            {"code": "no_website", "confidence": 0.9},
            {"code": "no_online_booking", "confidence": 0.8},
            {"code": "no_online_ordering", "confidence": 0.8},
        ])
        self.assertTrue(band.adjustments)
        for adjustment in band.adjustments:
            self.assertTrue(adjustment.reason)
            self.assertGreater(adjustment.multiplier, 0)

    def test_rationale_shows_the_arithmetic(self):
        band = self.engine.quote(offer=self._ready(), problems=self.problems)
        joined = " ".join(band.rationale).casefold()
        self.assertIn("base effort", joined)
        self.assertIn("delivery cost", joined)
        self.assertIn("margin", joined)

    def test_confidence_is_capped_without_comparables(self):
        """Zero past engagements means Winston cannot be confident about price."""
        band = self.engine.quote(offer=self._ready(), problems=self.problems,
                                 evidence_confidence=1.0)
        self.assertLess(band.confidence, 0.85)
        self.assertTrue(any("no comparable" in r.casefold() for r in band.rationale))

    def test_scope_assumptions_are_recorded(self):
        band = self.engine.quote(offer=self._ready(), problems=self.problems,
                                 signals={"cms": {"value": "Wix"}})
        self.assertTrue(any("Wix" in a for a in band.scope_assumptions))

    def test_quote_serialises_completely(self):
        payload = self.engine.quote(offer=self._ready(), problems=self.problems).as_dict()
        for key in ("floor_usd", "target_usd", "premium_usd", "confidence",
                    "effort_hours", "delivery_cost_usd", "adjustments",
                    "scope_assumptions", "rationale"):
            self.assertIn(key, payload)


class DiscountTests(PricingBase):
    def _band(self):
        return self.engine.quote(offer=self._ready(), problems=self.problems)

    def test_discount_requires_a_recognised_reason(self):
        with self.assertRaises(ValueError):
            self.engine.apply_discount(self._band(), "just_because", 0.10)

    def test_discount_cannot_exceed_its_ceiling(self):
        with self.assertRaises(ValueError):
            self.engine.apply_discount(self._band(), "referral", 0.50)

    def test_discount_cannot_break_the_margin_floor(self):
        band = self._band()
        with self.assertRaises(ValueError):
            self.engine.apply_discount(band, "limited_scope", 0.25)

    def test_valid_discount_is_recorded_with_its_reason(self):
        band = self.engine.quote(offer=self._ready(low=40, high=60), problems=self.problems)
        discounted = self.engine.apply_discount(band, "case_study", 0.10)
        self.assertEqual(discounted.discount["reason_code"], "case_study")
        self.assertGreater(discounted.discount["margin_after"], 0)
        self.assertTrue(any("discount" in r.casefold() for r in discounted.rationale))

    def test_no_discount_reason_references_a_protected_characteristic(self):
        for code, (label, _) in DISCOUNT_REASONS.items():
            haystack = f"{code} {label}".casefold()
            from winston.pricing import _PROTECTED_PATTERN
            self.assertIsNone(_PROTECTED_PATTERN.search(haystack),
                              f"discount reason {code!r} references a protected term")


if __name__ == "__main__":
    unittest.main()
