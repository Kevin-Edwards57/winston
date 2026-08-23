"""YardLink knowledge base and prospect-fit matching.

The guards that matter here all protect against the same failure: Winston confidently
selling something it should not. A portfolio project pitched as a product, an internal
tool offered to a barbershop, a capability asserted because it would be convenient, or
a problem invented for a business nobody researched.
"""
import tempfile
import unittest
from pathlib import Path

from winston.catalog import Catalog, CatalogValidationError, UnknownEntry
from winston.fit import FitEngine, derive_problems
from winston.repository import WinstonRepository
from winston.signals import SignalStore, extract_signals

STALE_SITE = """<html><head><title>Old Diner</title></head><body>
<table><tr><td>Menu</td></tr></table><p>Copyright 2011</p></body></html>"""

MODERN_SITE = """<!doctype html><html><head><title>New Co</title>
<meta name="viewport" content="width=device-width"><meta name="description" content="x">
<script src="https://www.googletagmanager.com/gtag/js?id=G-A"></script></head>
<body><h1>New</h1><form><input type="email"></form>
<a href="https://calendly.com/x">Book</a><footer>&copy; 2026</footer></body></html>"""


class CatalogBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "cat.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def _verified_service(self, slug="web-development", **overrides):
        payload = {
            "slug": slug, "name": "Web Development", "kind": "SERVICE", "status": "SERVICE",
            "description": "Modern responsive websites",
            "problems_solved": ["outdated website", "not mobile friendly", "no lead capture"],
            "industries": ["restaurant"], "pricing_model": "fixed scope",
            "price_min_usd": 900, "price_max_usd": 2500,
        }
        payload.update(overrides)
        self.catalog.upsert(payload)
        self.catalog.verify(slug)
        return slug


class ClassificationGateTests(CatalogBase):
    def test_portfolio_cannot_be_marked_sellable(self):
        """Otonia proves capability. It is not for sale."""
        with self.assertRaises(CatalogValidationError):
            self.catalog.upsert({"slug": "otonia", "kind": "PORTFOLIO", "status": "ACTIVE_PRODUCT"})

    def test_internal_tool_cannot_be_marked_sellable(self):
        """Winston runs YardLink; it is not a product unless deliberately reclassified."""
        with self.assertRaises(CatalogValidationError):
            self.catalog.upsert({"slug": "winston", "kind": "INTERNAL_TOOL", "status": "BETA_PRODUCT"})

    def test_seeded_entries_are_all_unsellable_until_verified(self):
        """Seed data knows names only, so nothing may be recommended out of the box."""
        for entry in self.catalog.list():
            self.assertFalse(entry["sellable"], f"{entry['slug']} is sellable without verification")

    def test_verification_is_required_for_sellability(self):
        self.catalog.upsert({"slug": "svc", "name": "S", "kind": "SERVICE", "status": "SERVICE"})
        self.assertFalse(self.catalog.get("svc")["sellable"])
        self.catalog.verify("svc")
        self.assertTrue(self.catalog.get("svc")["sellable"])

    def test_changing_claims_revokes_verification(self):
        """A product whose capabilities changed has not been re-checked by anyone."""
        slug = self._verified_service()
        self.catalog.upsert({"slug": slug, "capabilities": ["something new"]})
        self.assertFalse(self.catalog.get(slug)["verified"])

    def test_cosmetic_edit_preserves_verification(self):
        slug = self._verified_service()
        self.catalog.upsert({"slug": slug, "notes": "internal note"})
        self.assertTrue(self.catalog.get(slug)["verified"])

    def test_archived_entries_are_not_sellable(self):
        slug = self._verified_service()
        self.catalog.delete(slug)
        self.assertEqual(self.catalog.get(slug)["status"], "ARCHIVED")
        self.assertFalse(self.catalog.get(slug)["sellable"])

    def test_invalid_kind_and_status_are_rejected(self):
        with self.assertRaises(CatalogValidationError):
            self.catalog.upsert({"slug": "x", "kind": "NONSENSE", "status": "SERVICE"})
        with self.assertRaises(CatalogValidationError):
            self.catalog.upsert({"slug": "x", "kind": "PRODUCT", "status": "NONSENSE"})

    def test_inverted_price_range_is_rejected(self):
        with self.assertRaises(CatalogValidationError):
            self.catalog.upsert({"slug": "x", "kind": "SERVICE", "status": "SERVICE",
                                 "price_min_usd": 2000, "price_max_usd": 500})


class CatalogEditingTests(CatalogBase):
    def test_products_can_be_added_without_code_changes(self):
        self.catalog.upsert({"slug": "brand-new", "name": "Brand New", "kind": "PRODUCT",
                             "status": "ACTIVE_PRODUCT", "problems_solved": ["a problem"]})
        self.catalog.verify("brand-new")
        self.assertIn("brand-new", [e["slug"] for e in self.catalog.sellable()])

    def test_every_edit_is_recorded(self):
        slug = self._verified_service()
        self.catalog.upsert({"slug": slug, "notes": "n"})
        actions = [r["action"] for r in self.catalog.revisions(slug)]
        self.assertIn("create", actions)
        self.assertIn("verify", actions)
        self.assertIn("update", actions)

    def test_linking_requires_both_entries_to_exist(self):
        with self.assertRaises(UnknownEntry):
            self.catalog.link("web-development", "nope", "proves")

    def test_readiness_reports_nothing_sellable_honestly(self):
        readiness = self.catalog.readiness()
        self.assertFalse(readiness["can_recommend"])
        self.assertEqual(readiness["sellable"], 0)
        self.assertTrue(readiness["awaiting_verification"])


class ProblemDerivationTests(unittest.TestCase):
    def _signals(self, html, url):
        return {s.name: {"value": s.value, "confidence": s.confidence, "evidence": s.evidence}
                for s in extract_signals(html, url)}

    def test_no_signals_yields_no_problems(self):
        """An unresearched business has no known problems — not a perfect one."""
        self.assertEqual(derive_problems({}, industry="restaurant"), [])

    def test_unknown_signal_does_not_become_a_problem(self):
        """Client-rendered sites withhold has_contact_form; that is not 'no lead capture'."""
        signals = {"has_ssl": {"value": True, "confidence": 0.99, "evidence": "https"}}
        codes = {p.code for p in derive_problems(signals, industry="restaurant")}
        self.assertNotIn("no_lead_capture", codes)
        self.assertNotIn("not_mobile_friendly", codes)

    def test_observed_negatives_do_become_problems(self):
        problems = derive_problems(self._signals(STALE_SITE, "http://old.com"), industry="restaurant")
        codes = {p.code for p in problems}
        self.assertIn("not_mobile_friendly", codes)
        self.assertIn("no_ssl", codes)
        self.assertIn("outdated_website", codes)

    def test_a_healthy_site_produces_few_problems(self):
        problems = derive_problems(self._signals(MODERN_SITE, "https://new.com"), industry="barbershop")
        codes = {p.code for p in problems}
        self.assertNotIn("not_mobile_friendly", codes)
        self.assertNotIn("no_ssl", codes)
        self.assertNotIn("no_online_booking", codes, "Calendly was detected")

    def test_capability_gaps_are_industry_conditional(self):
        """A photographer without online ordering is not a problem; a restaurant is."""
        signals = self._signals(MODERN_SITE, "https://x.com")
        restaurant = {p.code for p in derive_problems(signals, industry="restaurant")}
        photographer = {p.code for p in derive_problems(signals, industry="photographer")}
        self.assertIn("no_online_ordering", restaurant)
        self.assertNotIn("no_online_ordering", photographer)

    def test_every_problem_carries_evidence(self):
        for problem in derive_problems(self._signals(STALE_SITE, "http://old.com"), industry="restaurant"):
            self.assertTrue(problem.evidence, f"{problem.code} has no evidence")


class FitEngineTests(CatalogBase):
    def setUp(self):
        super().setUp()
        self.signals = SignalStore(self.repo)
        self.signals.initialize()
        self.engine = FitEngine(self.repo, self.catalog, self.signals)
        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Old Diner", "email": "a@example.com", "place_id": "p1",
             "type": "restaurant", "website": "http://old.com"}, "test")

    def _research(self, html=STALE_SITE, url="http://old.com"):
        self.signals.record(self.contact_id, extract_signals(html, url))
        run = self.signals.start_run(self.contact_id, url)
        self.signals.complete_run(run, status="ok", pages=1, signals=8)

    def test_unverified_catalogue_blocks_all_recommendations(self):
        self._research()
        result = self.engine.assess(self.contact_id)
        self.assertIsNone(result.recommended_product)
        self.assertIsNone(result.recommended_service)
        self.assertTrue(any("verified" in b for b in result.blockers))

    def test_problems_are_still_reported_without_a_catalogue(self):
        """Winston knowing what is wrong does not depend on having something to sell."""
        self._research()
        self.assertTrue(self.engine.assess(self.contact_id).problems)

    def test_unresearched_prospect_gets_no_problems_and_zero_confidence(self):
        self._verified_service()
        result = self.engine.assess(self.contact_id)
        self.assertEqual(result.problems, [])
        self.assertEqual(result.confidence, 0.0)
        self.assertTrue(any("not researched" in b.casefold() for b in result.blockers))

    def test_service_is_recommended_when_no_product_fits(self):
        """Example D: never force a product onto a prospect it does not suit."""
        self._research()
        self._verified_service()
        result = self.engine.assess(self.contact_id)
        self.assertIsNone(result.recommended_product)
        self.assertIsNotNone(result.recommended_service)
        self.assertEqual(result.product_fit, 0.0)
        self.assertGreater(result.service_fit, 0.0)

    def test_scores_are_reported_separately(self):
        self._research()
        self._verified_service()
        scores = self.engine.assess(self.contact_id).as_dict()["scores"]
        for key in ("PRODUCT_FIT", "SERVICE_FIT", "PORTFOLIO_RELEVANCE",
                    "PROBLEM_SEVERITY", "COMMERCIAL_OPPORTUNITY", "CONFIDENCE"):
            self.assertIn(key, scores)

    def test_portfolio_proof_is_cited_but_never_sold(self):
        self._research()
        slug = self._verified_service()
        self.catalog.link(slug, "otonia", "proves")
        result = self.engine.assess(self.contact_id)
        self.assertEqual([p["name"] for p in result.proof], ["Otonia"])
        self.assertFalse(result.proof[0]["sellable"], "proof must never be offered for sale")

    def test_missing_proof_is_flagged_as_a_blocker(self):
        self._research()
        self._verified_service()
        result = self.engine.assess(self.contact_id)
        self.assertTrue(any("portfolio evidence" in b for b in result.blockers))

    def test_commercial_opportunity_requires_evidence(self):
        """Opportunity must not be high on a prospect nobody researched."""
        self._verified_service()
        unresearched = self.engine.assess(self.contact_id)
        self._research()
        researched = self.engine.assess(self.contact_id)
        self.assertLess(unresearched.commercial_opportunity, researched.commercial_opportunity)

    def test_unknown_contact_raises(self):
        with self.assertRaises(KeyError):
            self.engine.assess("does-not-exist")


if __name__ == "__main__":
    unittest.main()
