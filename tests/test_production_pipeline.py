"""The production path must be the intelligent one.

Phase A's whole point is that the Writer and Guardian govern real outreach rather than
sitting beside a legacy generator that still runs. These tests parse winston_app.py to
prove the old path is gone, and exercise the pipeline to prove the new one holds.
"""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import winston_app
from winston.catalog import Catalog
from winston.commercial import CommercialLedger
from winston.fit import FitEngine
from winston.guardian import Guardian
from winston.pipeline import OutreachPipeline
from winston.repository import WinstonRepository
from winston.signals import SignalStore, extract_signals
from winston.writer import Writer

APP = Path(__file__).resolve().parent.parent / "winston_app.py"

STALE_RESTAURANT = """<html><head><title>Irie</title></head><body>
<table><tr><td>Menu</td></tr></table><p>Copyright 2013</p></body></html>"""


class LegacyGeneratorRemovedTests(unittest.TestCase):
    """The hardcoded agency blurb must not exist in the production module."""

    @classmethod
    def setUpClass(cls):
        cls.source = APP.read_text()
        cls.tree = ast.parse(cls.source)

    def _functions(self):
        return {n.name for n in ast.walk(self.tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def test_legacy_writer_is_gone(self):
        for symbol in ("write_email", "get_subject", "write_followup_email"):
            self.assertNotIn(symbol, self._functions(),
                             f"{symbol}() was reintroduced as a second generation path")

    def test_hardcoded_agency_blurb_is_gone(self):
        """Scan string literals, not raw source.

        Comments cannot reach a prospect, and the tombstone marking the removal
        necessarily quotes the copy it replaced.
        """
        literals = " ".join(
            node.value for node in ast.walk(self.tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str))
        for phrase in ("NYC digital agency",
                       "AI chatbots that handle customer questions",
                       "Fast, modern websites"):
            self.assertNotIn(phrase, literals,
                             f"generic pitch copy is still reachable: {phrase!r}")

    def test_only_the_pipeline_creates_production_drafts(self):
        """create_draft must not be called directly from a scan or batch function."""
        offenders = []
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in ("run_scan", "run_existing_contact_drafts"):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "create_draft"):
                    offenders.append(node.name)
        self.assertEqual(offenders, [],
                         f"{offenders} bypasses the pipeline and therefore Guardian")

    def test_generation_functions_call_the_pipeline(self):
        for name in ("run_scan", "run_existing_contact_drafts"):
            node = next(n for n in ast.walk(self.tree)
                        if isinstance(n, ast.FunctionDef) and n.name == name)
            calls = {i.func.attr for i in ast.walk(node)
                     if isinstance(i, ast.Call) and isinstance(i.func, ast.Attribute)}
            self.assertIn("generate", calls, f"{name} does not run the pipeline")

    def test_app_exposes_pipeline_and_guardian(self):
        self.assertTrue(hasattr(winston_app, "pipeline"))
        self.assertTrue(hasattr(winston_app, "guardian"))
        self.assertTrue(hasattr(winston_app, "writer"))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "pipe.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()
        CommercialLedger(self.repo).initialize()
        self.signals = SignalStore(self.repo)
        self.signals.initialize()
        self.fit = FitEngine(self.repo, self.catalog, self.signals)

        self.ai = MagicMock()
        self.ai.generate.return_value = MagicMock(
            text="I noticed your site has no online ordering, so customers have to call "
                 "to place an order. YardLink builds ordering systems that let people "
                 "order directly from the site. Our YardLink Eats app already works with "
                 "Caribbean restaurants across New York. Would it help to see what that "
                 "could look like?",
            provider="ollama", model="qwen3:8b", input_tokens=10, output_tokens=40,
            estimated_cost_usd=0.0)

        self.writer = Writer(self.repo, self.catalog, self.signals, self.fit, self.ai)
        self.guardian = Guardian(self.repo, self.catalog)
        self.pipeline = OutreachPipeline(self.repo, self.catalog, self.signals,
                                         self.fit, self.writer, self.guardian)
        self.pipeline.initialize()

        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Irie Jerk", "email": "irie@example.com", "place_id": "p1",
             "type": "jamaican restaurant", "address": "Brooklyn, NY",
             "website": "http://irie.com"}, "test")

    def tearDown(self):
        self.temp.cleanup()

    def _research(self):
        self.signals.record(self.contact_id, extract_signals(STALE_RESTAURANT, "http://irie.com"))
        run = self.signals.start_run(self.contact_id, "http://irie.com")
        self.signals.complete_run(run, status="ok", pages=1, signals=9)

    def _ready(self):
        self._research()
        self.catalog.verify("ordering-systems", actor="test")

    # ── happy path ───────────────────────────────────────────────────────

    def test_approved_draft_is_queued_with_full_reasoning(self):
        self._ready()
        result = self.pipeline.generate(self.contact_id)
        self.assertEqual(result.status, "queued")
        self.assertTrue(result.reviewable)

        record = self.pipeline.intelligence_for(result.draft_id)
        self.assertTrue(record["approved"])
        self.assertTrue(record["brief"]["observed_problems"])
        self.assertTrue(record["guardian"]["approved"])
        self.assertEqual(record["provider"], "ollama")

    def test_evidence_chain_is_preserved(self):
        """Observation, evidence, offer, and proof must all survive to the reviewer."""
        self._ready()
        record = self.pipeline.intelligence_for(
            self.pipeline.generate(self.contact_id).draft_id)
        brief = record["brief"]
        self.assertTrue(all(p["evidence"] for p in brief["observed_problems"]))
        self.assertEqual(brief["recommended_service"]["slug"], "ordering-systems")
        self.assertTrue(brief["proof"])
        self.assertIn("relevance", brief["proof"][0])

    def test_draft_reaches_the_review_queue(self):
        self._ready()
        result = self.pipeline.generate(self.contact_id)
        self.assertIn(result.draft_id,
                      [d["draft_id"] for d in self.repo.pending_drafts()])

    # ── Guardian veto ────────────────────────────────────────────────────

    def test_guardian_veto_prevents_queueing(self):
        self._ready()
        self.ai.generate.return_value.text = (
            "I noticed you have no online booking, and 40% of customers prefer it. "
            "We guarantee results. Interested?")
        result = self.pipeline.generate(self.contact_id)
        self.assertEqual(result.status, "blocked")
        self.assertIsNone(result.draft_id)
        self.assertEqual(self.repo.pending_drafts(), [],
                         "a blocked draft must never enter the review queue")

    def test_blocked_drafts_are_recorded_not_discarded(self):
        self._ready()
        self.ai.generate.return_value.text = "You have no online booking. Interested?"
        self.pipeline.generate(self.contact_id)
        blocked = self.pipeline.blocked()
        self.assertEqual(len(blocked), 1)
        self.assertIn("unobserved_problem", blocked[0]["issues"])

    def test_em_dash_cannot_reach_the_queue(self):
        self._ready()
        self.ai.generate.return_value.text = (
            "Your site has no online ordering — customers must call. Interested?")
        result = self.pipeline.generate(self.contact_id)
        # The Writer sanitises at source, so this should survive; either way no em dash.
        if result.reviewable:
            draft = self.repo.get_draft(result.draft_id)
            self.assertNotIn("—", draft["body"])
        else:
            self.assertEqual(result.status, "blocked")

    # ── refusals ─────────────────────────────────────────────────────────

    def test_unverified_catalogue_produces_no_draft(self):
        self._research()
        result = self.pipeline.generate(self.contact_id)
        self.assertEqual(result.status, "no_verified_offer")
        self.assertIsNone(result.draft_id)
        self.ai.generate.assert_not_called()

    def test_unresearched_prospect_produces_no_draft(self):
        self.catalog.verify("ordering-systems", actor="test")
        result = self.pipeline.generate(self.contact_id)
        self.assertEqual(result.status, "no_evidence")
        self.ai.generate.assert_not_called()

    def test_suppressed_contact_is_blocked(self):
        self._ready()
        self.repo.suppress("irie@example.com", "unsubscribed")
        result = self.pipeline.generate(self.contact_id)
        self.assertEqual(result.status, "blocked")

    def test_unknown_contact_raises(self):
        with self.assertRaises(KeyError):
            self.pipeline.generate("nope")

    def test_stats_report_blocked_and_approved(self):
        self._ready()
        self.pipeline.generate(self.contact_id)
        stats = self.pipeline.stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["approved"], 1)


class ProofRankingTests(unittest.TestCase):
    """Relevance ranking must be general, not a special case for one project."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "rank.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()
        for slug in ("ordering-systems", "data-engineering", "website-service"):
            self.catalog.verify(slug, actor="test")

    def tearDown(self):
        self.temp.cleanup()

    def _proof(self, offer_slug, industry, codes=frozenset()):
        from winston.writer import select_proof
        return select_proof(self.catalog, self.catalog.get(offer_slug), industry, set(codes))

    def test_industry_standing_outranks_unrelated_proof(self):
        ranked = self._proof("ordering-systems", "jamaican restaurant")
        self.assertEqual(ranked[0]["slug"], "yardlink-eats")

    def test_ranking_inverts_for_a_data_problem(self):
        """The mechanism is general: a data prospect must not surface YardLink Eats."""
        ranked = self._proof("data-engineering", "accountant")
        self.assertTrue(ranked)
        self.assertNotIn("yardlink-eats", [r["slug"] for r in ranked])

    def test_curated_links_carry_weight_without_keyword_overlap(self):
        """No portfolio entry contains the literal word "website", yet three are linked."""
        ranked = self._proof("website-service", "barbershop")
        self.assertTrue(ranked, "explicitly linked proof must score above zero")

    def test_internal_fulfilment_platform_can_be_cited_as_proof(self):
        """The Builder proves YardLink can deliver, without being the thing sold."""
        ranked = self._proof("website-service", "barbershop")
        builder = next((r for r in ranked if r["slug"] == "website-builder"), None)
        if builder:
            self.assertEqual(self.catalog.get("website-builder")["kind"], "INTERNAL_TOOL")

    def test_unverified_entries_are_never_cited(self):
        ranked = self._proof("custom-software", "security")
        self.assertNotIn("guardlink", [r["slug"] for r in ranked])

    def test_every_proof_carries_a_reason(self):
        for item in self._proof("ordering-systems", "jamaican restaurant"):
            self.assertTrue(item["why"])
            self.assertGreater(item["relevance"], 0)


class ProblemPrioritisationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "prio.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def _problems(self):
        from winston.fit import Problem
        return [
            Problem("not_mobile_friendly", "Site does not adapt to phones", 0.85, "e", 0.85),
            Problem("no_ssl", "Plain HTTP", 0.7, "e", 0.99),
            Problem("no_online_ordering", "No online ordering", 0.8, "e", 0.55),
        ]

    def test_ordering_offer_leads_with_ordering(self):
        from winston.writer import rank_problems
        ranked = rank_problems(self._problems(), self.catalog.get("ordering-systems"))
        self.assertEqual(ranked[0].code, "no_online_ordering")

    def test_website_offer_leads_with_a_website_problem(self):
        from winston.writer import rank_problems
        ranked = rank_problems(self._problems(), self.catalog.get("website-service"))
        self.assertIn(ranked[0].code, {"not_mobile_friendly", "no_ssl"})

    def test_ordering_is_not_merely_array_order(self):
        """The ordering problem is last in the input list and must still lead."""
        from winston.writer import rank_problems
        problems = self._problems()
        self.assertEqual(problems[-1].code, "no_online_ordering")
        ranked = rank_problems(problems, self.catalog.get("ordering-systems"))
        self.assertEqual(ranked[0].code, "no_online_ordering")


if __name__ == "__main__":
    unittest.main()
