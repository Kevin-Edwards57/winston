"""Phase B.5 and C: rate card provenance, and provider routing.

The rate card tests exist to stop a starting guess hardening into an authoritative
number. Winston has zero completed engagements, so every price is an assumption, and
nothing in the system may quietly present one as evidence.

The routing tests exist to keep inference cheap and escalation deliberate. A local model
answering a classification correctly for free is the desired outcome, not a compromise.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from winston.ai import AIService
from winston.catalog import Catalog
from winston.pricing import NoPricingBasis, PricingEngine
from winston.providers import (
    CAPABILITIES, DEFAULT_ROUTING_POLICY, ProviderRegistry, TaskClass, classify_task,
)
from winston.ratecard import Basis, CALIBRATION_MINIMUM, RateCard, STARTER_RATE_CARD
from winston.repository import WinstonRepository


class RateCardBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "rc.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()
        self.card = RateCard(self.repo, self.catalog)
        self.card.initialize()

    def tearDown(self):
        self.temp.cleanup()


class ProvenanceTests(RateCardBase):
    """Assumptions must never masquerade as evidence."""

    def test_every_seeded_price_is_an_assumption(self):
        for entry in self.card.list():
            self.assertIs(entry.price_basis, Basis.OPERATOR_ASSUMPTION,
                          f"{entry.slug} claims a basis it has not earned")
            self.assertFalse(entry.price_basis.is_evidence_backed)

    def test_status_says_plainly_that_nothing_is_evidence_backed(self):
        status = self.card.status()
        self.assertTrue(status["all_prices_are_assumptions"])
        self.assertEqual(status["evidence_backed"], 0)
        self.assertIn("assumption", status["warning"].casefold())

    def test_editing_a_price_keeps_it_an_assumption(self):
        self.card.upsert({"slug": "website-service", "price_target_usd": 1000}, actor="kevin")
        self.assertIs(self.card.get("website-service").price_basis, Basis.OPERATOR_ASSUMPTION)

    def test_a_target_above_the_premium_is_rejected(self):
        with self.assertRaises(ValueError):
            self.card.upsert({"slug": "website-service", "price_target_usd": 99_999})

    def test_calibration_refuses_without_enough_closed_deals(self):
        result = self.card.calibrate_from_outcomes("website-service")
        self.assertFalse(result["calibrated"])
        self.assertEqual(result["sample_size"], 0)
        self.assertEqual(result["required"], CALIBRATION_MINIMUM)

    def test_basis_labels_are_distinguishable(self):
        self.assertNotEqual(Basis.OPERATOR_ASSUMPTION.label, Basis.HISTORICAL.label)
        self.assertFalse(Basis.OPERATOR_ASSUMPTION.is_evidence_backed)
        self.assertFalse(Basis.OBSERVED.is_evidence_backed)
        self.assertTrue(Basis.HISTORICAL.is_evidence_backed)
        self.assertTrue(Basis.CALIBRATED.is_evidence_backed)

    def test_every_edit_is_recorded(self):
        self.card.upsert({"slug": "website-service", "price_target_usd": 900}, actor="kevin")
        self.assertTrue(self.card.revisions("website-service"))


class EnablementTests(RateCardBase):
    """A rate existing is not a decision to sell the service."""

    def test_starter_entries_seed_disabled(self):
        self.assertEqual(self.card.status()["enabled"], 0)
        self.assertEqual(self.card.list(enabled_only=True), [])

    def test_enabling_requires_price_and_effort(self):
        self.card.upsert({"slug": "crm-setup", "price_target_usd": None,
                          "effort_hours_min": None, "effort_hours_max": None})
        with self.assertRaises(ValueError):
            self.card.enable("crm-setup")

    def test_operator_can_enable_a_complete_entry(self):
        self.card.enable("website-service", actor="kevin")
        self.assertIn("website-service", self.card.status()["enabled_slugs"])

    def test_unknown_slug_is_rejected(self):
        with self.assertRaises(KeyError):
            self.card.upsert({"slug": "does-not-exist", "price_target_usd": 100})

    def test_inverted_bands_are_rejected(self):
        with self.assertRaises(ValueError):
            self.card.upsert({"slug": "website-service",
                              "price_floor_usd": 2000, "price_target_usd": 500})


class RateCardPricingTests(RateCardBase):
    def setUp(self):
        super().setUp()
        self.engine = PricingEngine(self.repo, self.catalog, self.card)
        self.engine.configure(hourly_rate_usd=50, min_margin=0.35)
        self.catalog.verify("website-service")
        self.problems = [{"code": "no_website", "confidence": 0.9}]

    def test_disabled_entry_refuses_to_quote(self):
        with self.assertRaises(NoPricingBasis):
            self.engine.quote(offer=self.catalog.get("website-service"),
                              problems=self.problems)

    def test_enabled_entry_quotes_from_the_rate_card(self):
        self.card.enable("website-service")
        band = self.engine.quote(offer=self.catalog.get("website-service"),
                                 problems=self.problems)
        self.assertGreater(band.target_usd, 0)
        self.assertEqual(band.basis, Basis.OPERATOR_ASSUMPTION.value)
        self.assertFalse(band.evidence_backed)

    def test_quote_states_it_is_an_assumption(self):
        self.card.enable("website-service")
        band = self.engine.quote(offer=self.catalog.get("website-service"),
                                 problems=self.problems)
        self.assertTrue(any("assumption" in r.casefold() for r in band.rationale))

    def test_prices_are_rounded_not_falsely_precise(self):
        self.card.enable("website-service")
        band = self.engine.quote(offer=self.catalog.get("website-service"),
                                 problems=self.problems)
        for value in (band.floor_usd, band.target_usd, band.premium_usd):
            self.assertEqual(value % 25, 0, "manufactured precision reads as false confidence")

    def test_scope_may_raise_a_banded_price_but_never_lower_it(self):
        """The band already prices a typical engagement; discounting double-counts."""
        self.card.enable("website-service")
        offer = self.catalog.get("website-service")
        simple = self.engine.quote(offer=offer, problems=self.problems)
        heavy = self.engine.quote(offer=offer, problems=[
            {"code": "no_website", "confidence": 0.9},
            {"code": "no_online_booking", "confidence": 0.8},
            {"code": "no_online_ordering", "confidence": 0.8},
            {"code": "no_measurement", "confidence": 0.7},
        ])
        self.assertGreaterEqual(heavy.target_usd, simple.target_usd)

    def test_a_price_below_delivery_cost_is_refused(self):
        self.card.upsert({"slug": "website-service", "price_floor_usd": 50,
                          "price_target_usd": 75, "price_premium_usd": 100})
        self.card.enable("website-service")
        with self.assertRaises(NoPricingBasis) as caught:
            self.engine.quote(offer=self.catalog.get("website-service"),
                              problems=self.problems)
        self.assertIn("inconsistent", str(caught.exception).casefold())

    def test_starter_card_is_viable_at_a_realistic_internal_rate(self):
        """Sanity check on the seeded assumptions, not a claim about the market."""
        self.card.enable("website-service")
        band = self.engine.quote(offer=self.catalog.get("website-service"),
                                 problems=self.problems)
        self.assertGreater(band.margin_at_target, 0.2)


class TaskClassificationTests(unittest.TestCase):
    def test_light_tasks_are_classified_light(self):
        for purpose in ("reply_classification", "email_extraction", "structured_parse"):
            self.assertIs(classify_task(purpose), TaskClass.LIGHT)

    def test_drafting_is_medium(self):
        self.assertIs(classify_task("outreach_draft"), TaskClass.MEDIUM)

    def test_strategy_is_heavy(self):
        self.assertIs(classify_task("commercial_strategy"), TaskClass.HEAVY)

    def test_customer_facing_commercial_output_is_critical(self):
        self.assertIs(classify_task("proposal_generation"), TaskClass.CRITICAL)

    def test_unknown_purpose_defaults_to_medium(self):
        self.assertIs(classify_task("something_new"), TaskClass.MEDIUM)

    def test_small_model_is_not_offered_heavy_work(self):
        self.assertFalse(CAPABILITIES["ollama:llama3.2:3b"].handles(TaskClass.HEAVY))
        self.assertTrue(CAPABILITIES["ollama:llama3.2:3b"].handles(TaskClass.LIGHT))

    def test_paid_provider_is_not_offered_trivial_work(self):
        self.assertFalse(CAPABILITIES["claude"].handles(TaskClass.LIGHT),
                         "paying a premium model to classify a reply is waste")


class RoutingBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "route.db")
        self.repo.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def _registry(self, *, keys=("ollama:llama3.2:3b", "ollama:qwen3:8b"),
                  zero_cost=True):
        providers = []
        for key in keys:
            capability = CAPABILITIES[key]
            provider = MagicMock()
            provider.name = capability.provider
            provider.model = capability.model
            provider.paid = capability.paid
            provider.available.return_value = True
            providers.append(provider)
        service = MagicMock()
        service.providers = providers
        service.zero_cost_mode = zero_cost
        return ProviderRegistry(self.repo, service)


class RoutingTests(RoutingBase):
    def test_light_work_goes_to_the_small_local_model(self):
        decision = self._registry().route("reply_classification")
        self.assertEqual(decision.chosen, "ollama:llama3.2:3b")
        self.assertFalse(decision.escalated)

    def test_medium_work_goes_to_the_workhorse(self):
        self.assertEqual(self._registry().route("outreach_draft").chosen, "ollama:qwen3:8b")

    def test_heavy_work_stays_local_when_local_can_handle_it(self):
        self.assertEqual(self._registry().route("commercial_strategy").chosen, "ollama:qwen3:8b")

    def test_critical_work_refuses_rather_than_downgrading(self):
        """No configured provider is suited to critical work, so Winston declines."""
        decision = self._registry().route("proposal_generation")
        self.assertIsNone(decision.chosen)
        self.assertIn("no usable provider", decision.reason.casefold())

    def test_zero_cost_mode_blocks_paid_escalation(self):
        registry = self._registry(keys=("ollama:qwen3:8b", "claude"), zero_cost=True)
        decision = registry.route("proposal_generation")
        self.assertNotEqual(decision.chosen, "claude")
        self.assertTrue(any(s["key"] == "claude" and "zero-cost" in s["why"]
                            for s in decision.skipped))

    def test_paid_escalation_needs_both_gates_open(self):
        """Zero-cost mode off is necessary but not sufficient; a budget is also required."""
        registry = self._registry(keys=("ollama:qwen3:8b", "claude"), zero_cost=False)
        self.assertIsNone(registry.route("proposal_generation").chosen,
                          "a $0 budget must still block a paid provider")

        registry.budget.set_budget("claude", monthly_budget_usd=5.0, enabled=True)
        decision = registry.route("proposal_generation")
        self.assertEqual(decision.chosen, "claude")
        self.assertTrue(decision.escalated)
        self.assertIn("deliberately", decision.reason)

    def test_unreachable_provider_is_skipped_with_a_reason(self):
        registry = self._registry()
        registry.ai_service.providers[0].available.return_value = False
        decision = registry.route("reply_classification")
        self.assertEqual(decision.chosen, "ollama:qwen3:8b")
        self.assertTrue(decision.skipped)

    def test_every_decision_explains_itself(self):
        for purpose in ("reply_classification", "outreach_draft", "commercial_strategy"):
            self.assertTrue(self._registry().route(purpose).reason)


class PolicyTests(RoutingBase):
    def test_policy_defaults_are_returned_when_unset(self):
        self.assertEqual(self._registry().policy(), DEFAULT_ROUTING_POLICY)

    def test_policy_is_configurable(self):
        registry = self._registry()
        registry.set_policy({"light": ["ollama:qwen3:8b", "ollama:llama3.2:3b"]})
        self.assertEqual(registry.route("reply_classification").chosen, "ollama:qwen3:8b")

    def test_unknown_task_class_is_rejected(self):
        with self.assertRaises(ValueError):
            self._registry().set_policy({"nonsense": ["gemini"]})

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            self._registry().set_policy({"light": ["gpt-4"]})

    def test_availability_reports_why_a_provider_is_unusable(self):
        rows = {r["key"]: r for r in self._registry().availability()}
        self.assertTrue(rows["ollama:qwen3:8b"]["usable"])
        self.assertFalse(rows["claude"]["usable"])
        self.assertIsNotNone(rows["claude"]["blocked_reason"])

    def test_summary_reports_measured_cost_not_estimates(self):
        summary = self._registry().summary()
        self.assertIn("total_ai_cost_usd", summary)
        self.assertIn("usable_providers", summary)
        self.assertEqual(summary["total_ai_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
