"""Zero-cost mode.

The invariant under test is narrow and absolute: Winston cannot spend money unless
somebody decided it should. Every test here is a way that decision could be bypassed.

The most important one is `test_indeterminate_cost_is_refused`. A call whose price
cannot be estimated is refused rather than attempted and monitored, because the failure
being prevented is finding out about the charge afterwards.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from winston.costs import (
    BudgetExceeded, BudgetGuard, DEFAULT_BUDGETS, FREE_TIER_LIMITS, IndeterminateCost,
    estimate_cost,
)
from winston.providers import CAPABILITIES, ProviderRegistry, TaskClass
from winston.repository import WinstonRepository, utc_now


class CostBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "cost.db")
        self.repo.initialize()
        self.guard = BudgetGuard(self.repo)
        self.guard.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def _usage(self, provider, model, cost, *, success=1, when=None):
        with self.repo.transaction() as connection:
            connection.execute(
                """INSERT INTO provider_usage(id,provider,model,purpose,success,latency_ms,
                       input_tokens,output_tokens,estimated_cost_usd,error,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (utc_now() + provider + str(cost), provider, model, "test", success,
                 100, 1000, 200, cost, "", when or utc_now()))


class DefaultsTests(CostBase):
    def test_paid_providers_default_to_zero_budget(self):
        budgets = {b["provider"]: b for b in self.guard.budgets()}
        self.assertEqual(budgets["claude"]["monthly_budget_usd"], 0.0)
        self.assertEqual(budgets["gemini"]["monthly_budget_usd"], 0.0)

    def test_paid_providers_default_to_disabled(self):
        budgets = {b["provider"]: b for b in self.guard.budgets()}
        self.assertFalse(budgets["claude"]["enabled"])
        self.assertFalse(budgets["gemini"]["enabled"])

    def test_local_provider_is_unmetered_and_enabled(self):
        budgets = {b["provider"]: b for b in self.guard.budgets()}
        self.assertTrue(budgets["ollama"]["unmetered"])
        self.assertTrue(budgets["ollama"]["enabled"])

    def test_a_fresh_install_cannot_spend(self):
        """An API key in the environment is not authorisation to use it."""
        for provider in ("claude", "gemini"):
            decision = self.guard.check(provider, input_tokens=1000, output_tokens=500)
            self.assertFalse(decision.allowed, f"{provider} could spend on a fresh install")


class GateTests(CostBase):
    def test_local_calls_pass_without_a_budget_check(self):
        decision = self.guard.check("ollama")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.metered)

    def test_indeterminate_cost_is_refused(self):
        """Unknown cost is treated as unbounded, never as zero."""
        self.guard.set_budget("claude", monthly_budget_usd=100.0, enabled=True)
        with self.assertRaises(IndeterminateCost):
            self.guard.enforce("claude")

    def test_unknown_provider_is_refused(self):
        decision = self.guard.check("some-new-api", estimated_cost_usd=0.01)
        self.assertFalse(decision.allowed)
        self.assertIn("no configured budget", decision.reason)

    def test_disabled_provider_is_refused_even_with_budget(self):
        self.guard.set_budget("claude", monthly_budget_usd=50.0, enabled=False)
        decision = self.guard.check("claude", input_tokens=100, output_tokens=100)
        self.assertFalse(decision.allowed)
        self.assertIn("not enabled", decision.reason)

    def test_enabled_provider_within_budget_is_allowed(self):
        self.guard.set_budget("claude", monthly_budget_usd=5.0, enabled=True)
        decision = self.guard.check("claude", input_tokens=2000, output_tokens=600)
        self.assertTrue(decision.allowed)
        self.assertGreater(decision.estimated_cost_usd, 0)

    def test_call_exceeding_remaining_budget_is_refused(self):
        self.guard.set_budget("claude", monthly_budget_usd=1.0, enabled=True)
        decision = self.guard.check("claude", input_tokens=5_000_000, output_tokens=500_000)
        self.assertFalse(decision.allowed)
        self.assertIn("exceeds", decision.reason)

    def test_budget_exhaustion_blocks_further_calls(self):
        self.guard.set_budget("claude", monthly_budget_usd=1.0, enabled=True)
        self._usage("claude", "claude-sonnet-4-6", 0.99)
        decision = self.guard.check("claude", input_tokens=100_000, output_tokens=50_000)
        self.assertFalse(decision.allowed)
        with self.assertRaises(BudgetExceeded):
            self.guard.enforce("claude", input_tokens=100_000, output_tokens=50_000)

    def test_free_tier_request_limit_is_enforced(self):
        """Exceeding a free tier is how a free provider produces a charge."""
        self.guard.set_budget("gemini", monthly_budget_usd=0.0, enabled=True)
        limit = FREE_TIER_LIMITS["gemini"]
        with self.repo.transaction() as connection:
            for index in range(limit):
                connection.execute(
                    """INSERT INTO provider_usage(id,provider,model,purpose,success,
                           latency_ms,input_tokens,output_tokens,estimated_cost_usd,
                           error,created_at)
                       VALUES(?,?,?,?,1,10,10,10,0,'',?)""",
                    (f"g{index}", "gemini", "flash", "test", utc_now()))
        decision = self.guard.check("gemini", input_tokens=10, output_tokens=10)
        self.assertFalse(decision.allowed)
        self.assertIn("free-tier limit", decision.reason)

    def test_only_local_providers_may_be_unmetered(self):
        with self.assertRaises(ValueError):
            self.guard.set_budget("claude", monthly_budget_usd=-1.0)


class EstimationTests(unittest.TestCase):
    def test_local_inference_costs_nothing(self):
        self.assertEqual(estimate_cost("ollama", input_tokens=100_000, output_tokens=50_000), 0.0)

    def test_paid_estimates_scale_with_tokens(self):
        small = estimate_cost("claude", input_tokens=1000, output_tokens=500)
        large = estimate_cost("claude", input_tokens=10_000, output_tokens=5000)
        self.assertAlmostEqual(large / small, 10.0, places=4)

    def test_unknown_provider_has_no_estimate(self):
        self.assertIsNone(estimate_cost("mystery-api", input_tokens=100, output_tokens=100))


class DashboardTests(CostBase):
    def test_reports_zero_on_a_clean_install(self):
        dashboard = self.guard.dashboard()
        self.assertEqual(dashboard["ai_cost"]["today_usd"], 0.0)
        self.assertEqual(dashboard["ai_cost"]["month_to_date_usd"], 0.0)
        self.assertTrue(dashboard["zero_cost"])

    def test_no_provider_can_spend_by_default(self):
        self.assertEqual(self.guard.dashboard()["spend_capable_providers"], [])

    def test_enabling_a_budget_makes_a_provider_spend_capable(self):
        self.guard.set_budget("claude", monthly_budget_usd=10.0, enabled=True)
        self.assertIn("claude", self.guard.dashboard()["spend_capable_providers"])

    def test_local_usage_appears_without_cost(self):
        self._usage("ollama", "qwen3:8b", 0.0)
        dashboard = self.guard.dashboard()
        self.assertTrue(dashboard["local_inference"])
        self.assertEqual(dashboard["local_inference"][0]["cost_usd"], 0.0)
        self.assertTrue(dashboard["zero_cost"])

    def test_cloud_spend_ends_the_zero_cost_claim(self):
        self._usage("claude", "claude-sonnet-4-6", 0.42)
        dashboard = self.guard.dashboard()
        self.assertFalse(dashboard["zero_cost"])
        self.assertEqual(dashboard["ai_cost"]["month_to_date_usd"], 0.42)


class RoutingBudgetTests(CostBase):
    def _registry(self, keys, *, zero_cost=True):
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
        return ProviderRegistry(self.repo, service, self.guard)

    def test_zero_budget_makes_a_cloud_provider_unroutable(self):
        registry = self._registry(("ollama:qwen3:8b", "gemini"))
        decision = registry.route("outreach_draft")
        self.assertEqual(decision.chosen, "ollama:qwen3:8b")

    def test_exhausted_budget_removes_a_provider_from_routing(self):
        """Exhaustion surfaces as a refusal, never as an invoice."""
        self.guard.set_budget("claude", monthly_budget_usd=0.001, enabled=True)
        registry = self._registry(("claude",), zero_cost=False)
        decision = registry.route("proposal_generation")
        self.assertIsNone(decision.chosen)
        self.assertTrue(any("exceeds" in s["why"] or "budget" in s["why"]
                            for s in decision.skipped))

    def test_local_failure_falls_back_to_local_not_cloud(self):
        """A brief Ollama outage must not become recurring cloud spend."""
        registry = self._registry(("ollama:llama3.2:3b", "ollama:qwen3:8b", "gemini"))
        alternatives = registry.local_alternatives(TaskClass.LIGHT)
        self.assertTrue(alternatives)
        for key in alternatives:
            self.assertEqual(CAPABILITIES[key].cost_class, "free")
            self.assertFalse(CAPABILITIES[key].paid)

    def test_local_inference_needs_no_api_key(self):
        registry = self._registry(("ollama:qwen3:8b",))
        rows = {r["key"]: r for r in registry.availability()}
        self.assertTrue(rows["ollama:qwen3:8b"]["usable"])
        self.assertEqual(rows["ollama:qwen3:8b"]["cost_class"], "free")

    def test_summary_exposes_spend_capability(self):
        summary = self._registry(("ollama:qwen3:8b",)).summary()
        self.assertEqual(summary["total_ai_cost_usd"], 0.0)
        self.assertEqual(summary["spend_capable_providers"], [])


if __name__ == "__main__":
    unittest.main()
