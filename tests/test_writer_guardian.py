"""Phase A: Writer and Guardian.

Guardian is tested against hand-written adversarial drafts rather than model output,
because the point is to prove the gate holds regardless of what produced the text. A
future model change, a prompt regression, or a hand-edited draft must all be caught by
the same deterministic rules.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from winston.catalog import Catalog
from winston.commercial import CommercialLedger
from winston.fit import FitEngine
from winston.guardian import Guardian
from winston.repository import WinstonRepository
from winston.signals import SignalStore, extract_signals
from winston.writer import Writer, select_proof, strip_em_dashes

# Problems here are observed directly in the markup: no h1, no form, no meta
# description. Capability gaps such as missing ordering are inferred and therefore
# gated out of the sales brief, so a fixture relying on them would produce no draft.
STALE_RESTAURANT = """<html><head><title>Irie Jerk</title></head><body>
<table><tr><td>Menu</td></tr></table><p>Call to order</p>
<p>Copyright 2013</p></body></html>"""

HEALTHY_SITE = """<!doctype html><html><head><title>Modern</title>
<meta name="viewport" content="width=device-width"><meta name="description" content="x">
<script src="https://www.googletagmanager.com/gtag/js?id=G-A"></script></head>
<body><h1>Modern</h1><form><input type="email"></form>
<a href="https://toasttab.com/x">Order</a><footer>&copy; 2026</footer></body></html>"""


class PhaseABase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "phasea.db")
        self.repo.initialize()
        self.catalog = Catalog(self.repo)
        self.catalog.initialize()
        CommercialLedger(self.repo).initialize()
        self.signals = SignalStore(self.repo)
        self.signals.initialize()
        self.fit = FitEngine(self.repo, self.catalog, self.signals)
        self.guardian = Guardian(self.repo, self.catalog)

        self.ai = MagicMock()
        self.ai.generate.return_value = MagicMock(
            text="Your site has no main heading and no enquiry form. People who find "
                 "you cannot easily get in touch. YardLink builds sites with proper "
                 "structure and lead capture. Would that be useful?",
            provider="ollama", model="qwen3:8b", input_tokens=10,
            output_tokens=20, estimated_cost_usd=0.0)
        self.writer = Writer(self.repo, self.catalog, self.signals, self.fit, self.ai)

        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Irie Jerk", "email": "irie@example.com", "place_id": "p1",
             "type": "jamaican restaurant", "address": "Brooklyn, NY",
             "website": "http://irie.com"}, "test")

    def tearDown(self):
        self.temp.cleanup()

    def _research(self, html=STALE_RESTAURANT, url="http://irie.com"):
        self.signals.record(self.contact_id, extract_signals(html, url))
        run = self.signals.start_run(self.contact_id, url)
        self.signals.complete_run(run, status="ok", pages=1, signals=9)

    def _verify_ordering(self):
        self.catalog.verify("website-service", actor="test")

    def _contact(self):
        return dict(self.repo.connect().execute(
            "SELECT * FROM contacts WHERE id=?", (self.contact_id,)).fetchone())

    def _review(self, body, subject="Subject", brief=None):
        return self.guardian.review(subject=subject, body=body,
                                    contact=self._contact(), brief=brief or self._brief())

    def _brief(self):
        return self.writer.build_brief(self.contact_id)


class WriterTests(PhaseABase):
    def test_writer_uses_observed_problem(self):
        self._research()
        self._verify_ordering()
        brief = self.writer.build_brief(self.contact_id)
        codes = {p["code"] for p in brief["observed_problems"]}
        self.assertIn("weak_seo_basics", codes)
        self.assertTrue(all(p["evidence"] for p in brief["observed_problems"]))

    def test_writer_leads_with_the_intent_problem(self):
        """An ordering email must open on ordering, not on whatever sorted first."""
        self._research()
        self._verify_ordering()
        brief = self.writer.build_brief(self.contact_id)
        self.assertIn(brief["intent"], {"lead capture opportunity", "SEO opportunity",
                                        "website opportunity"})
        self.assertTrue(brief["observed_problems"][0]["commercially_assertable"])

    def test_writer_uses_catalogue(self):
        self._research()
        self._verify_ordering()
        brief = self.writer.build_brief(self.contact_id)
        self.assertEqual(brief["recommended_service"]["slug"], "website-service")

    def test_writer_uses_portfolio_proof(self):
        self._research()
        self._verify_ordering()
        brief = self.writer.build_brief(self.contact_id)
        self.assertTrue(brief["proof"])
        self.assertEqual(brief["proof"][0]["slug"], "yardlink-eats",
                         "industry standing should outrank generic linked proof")

    def test_writer_rejects_unverified_service(self):
        """Nothing verified means nothing to sell, so no email is written."""
        self._research()
        draft = self.writer.write(self.contact_id)
        self.assertEqual(draft.status, "no_verified_offer")
        self.assertEqual(draft.body, "")
        self.ai.generate.assert_not_called()

    def test_writer_refuses_without_evidence(self):
        self._verify_ordering()
        draft = self.writer.write(self.contact_id)
        self.assertEqual(draft.status, "no_evidence")
        self.ai.generate.assert_not_called()

    def test_writer_withholds_low_confidence_observations(self):
        """Weak evidence is dropped, never softened into a hedge."""
        self._research()
        self._verify_ordering()
        writer = Writer(self.repo, self.catalog, self.signals, self.fit, self.ai,
                        confidence_floor=0.95)
        brief = writer.build_brief(self.contact_id)
        self.assertTrue(brief["withheld_low_confidence"])
        for problem in brief["observed_problems"]:
            self.assertGreaterEqual(problem["confidence"], 0.95)

    def test_writer_does_not_invent_problems_for_a_healthy_site(self):
        self.signals.record(self.contact_id, extract_signals(HEALTHY_SITE, "https://irie.com"))
        run = self.signals.start_run(self.contact_id, "https://irie.com")
        self.signals.complete_run(run, status="ok", pages=1, signals=9)
        self._verify_ordering()
        codes = {p["code"] for p in self.writer.build_brief(self.contact_id)["observed_problems"]}
        self.assertNotIn("weak_seo_basics", codes, "Toast ordering was detected")
        self.assertNotIn("not_mobile_friendly", codes)

    def test_writer_prompt_contains_only_briefed_facts(self):
        self._research()
        self._verify_ordering()
        self.writer.write(self.contact_id)
        prompt = self.ai.generate.call_args[0][0]
        self.assertIn("Irie Jerk", prompt)
        self.assertNotIn("digital agency", prompt.casefold())
        self.assertNotIn("ai chatbot", prompt.casefold())

    def test_writer_output_has_no_em_dash(self):
        self._research()
        self._verify_ordering()
        self.ai.generate.return_value.text = "Your site — the ordering page — is missing."
        draft = self.writer.write(self.contact_id)
        self.assertNotIn("—", draft.body)

    def test_strip_em_dashes(self):
        self.assertNotIn("—", strip_em_dashes("a — b"))
        self.assertEqual(strip_em_dashes("a — b"), "a, b")

    def test_proof_selection_is_limited(self):
        self._verify_ordering()
        offer = self.catalog.get("website-service")
        proof = select_proof(self.catalog, offer, "jamaican restaurant",
                             {"weak_seo_basics"})
        self.assertLessEqual(len(proof), 2, "an email must not become a portfolio dump")
        self.assertEqual(proof[0]["slug"], "yardlink-eats",
                         "industry standing must rank first")

    def test_draft_records_generation_provenance(self):
        self._research()
        self._verify_ordering()
        payload = self.writer.write(self.contact_id).as_dict()
        self.assertEqual(payload["generation"]["provider"], "ollama")
        self.assertEqual(payload["generation"]["model"], "qwen3:8b")
        self.assertIn("generated_at", payload["generation"])
        self.assertTrue(payload["brief"]["observed_problems"])


class GuardianStyleTests(PhaseABase):
    def setUp(self):
        super().setUp()
        self._research()
        self._verify_ordering()

    def test_guardian_blocks_em_dash(self):
        result = self._review("Your site has no enquiry form — visitors cannot get in touch.")
        self.assertFalse(result.approved)
        self.assertIn("no_em_dash", [i["rule"] for i in result.issues])

    def test_guardian_blocks_banned_filler(self):
        result = self._review("I hope this email finds you well. Your site has no enquiry form.")
        self.assertFalse(result.approved)
        self.assertIn("banned_phrase", [i["rule"] for i in result.issues])

    def test_guardian_blocks_overlong_draft(self):
        result = self._review("Your site has no enquiry form. " * 60)
        self.assertFalse(result.approved)
        self.assertIn("too_long", [i["rule"] for i in result.issues])

    def test_guardian_allows_a_clean_draft(self):
        result = self._review(
            "I noticed your site has no enquiry form, so anyone who finds you has to "
            "pick up the phone. YardLink builds sites with lead capture built in. Our "
            "YardLink Eats app already works with Caribbean restaurants across New York. "
            "Would it help to see what that could look like?")
        self.assertTrue(result.approved, f"unexpected issues: {result.issues}")


class GuardianClaimTests(PhaseABase):
    def setUp(self):
        super().setUp()
        self._research()
        self._verify_ordering()

    def test_guardian_blocks_fabricated_observation(self):
        """The site has no booking problem recorded; asserting one is a fabrication."""
        result = self._review(
            "I noticed you don't have online booking for appointments. "
            "YardLink builds booking systems. Interested?")
        self.assertFalse(result.approved)
        self.assertIn("unobserved_problem", [i["rule"] for i in result.issues])

    def test_guardian_blocks_invented_statistics(self):
        result = self._review(
            "Your site has no enquiry form. Businesses see 40% more leads with one. "
            "Would that help?")
        self.assertFalse(result.approved)
        self.assertIn("unsupported_claim", [i["rule"] for i in result.issues])

    def test_guardian_blocks_guarantees(self):
        result = self._review(
            "Your site has no enquiry form. We guarantee more leads. Interested?")
        self.assertFalse(result.approved)

    def test_guardian_blocks_named_client_claims(self):
        result = self._review(
            "Your site has no enquiry form. We built a lead capture system for "
            "Golden Krust. Would that help you?")
        self.assertFalse(result.approved)
        self.assertIn("named_client", [i["rule"] for i in result.issues])

    def test_guardian_blocks_a_draft_with_no_evidence(self):
        result = self.guardian.review(
            subject="Hi", body="We build great websites. Want one?",
            contact=self._contact(), brief={"observed_problems": []})
        self.assertFalse(result.approved)
        self.assertIn("no_evidence", [i["rule"] for i in result.issues])


class GuardianCommercialTests(PhaseABase):
    def setUp(self):
        super().setUp()
        self._research()
        self._verify_ordering()

    def test_guardian_blocks_pitching_a_consumer_product(self):
        """YardLink Eats is verified and shipping, but restaurants do not buy it."""
        result = self._review(
            "Your site has no enquiry form. You should buy YardLink Eats to fix it. "
            "Would that work?")
        self.assertFalse(result.approved)
        self.assertIn("not_offerable", [i["rule"] for i in result.issues])

    def test_guardian_blocks_pitching_a_portfolio_project(self):
        result = self._review(
            "Your site has no enquiry form. You can purchase Otonia from us. Interested?")
        self.assertFalse(result.approved)

    def test_guardian_blocks_unverified_entry(self):
        """GuardLink is unverified, so it may not appear in outreach at all."""
        result = self._review(
            "Your site has no enquiry form. GuardLink could help you. Interested?")
        self.assertFalse(result.approved)
        self.assertIn("unverified_entry", [i["rule"] for i in result.issues])

    def test_guardian_blocks_offering_a_coming_soon_product(self):
        """WedLink is verified but COMING_SOON, so it cannot be positioned as the answer."""
        result = self._review(
            "Your site has no enquiry form. WedLink could help you. Interested?")
        self.assertFalse(result.approved)

    def test_guardian_allows_citing_a_consumer_product_as_proof(self):
        result = self._review(
            "I noticed your site has no enquiry form. YardLink builds sites with "
            "lead capture. Our YardLink Eats app already works with Caribbean "
            "restaurants across New York. Would it help to see what that looks like?")
        self.assertTrue(result.approved, f"unexpected issues: {result.issues}")

    def test_guardian_blocks_protected_characteristic_pricing(self):
        result = self._review(
            "Your site has no enquiry form. We offer a discount because you are "
            "a minority-owned business. Interested?")
        self.assertFalse(result.approved)
        self.assertIn("protected_characteristic", [i["rule"] for i in result.issues])

    def test_guardian_blocks_ethnicity_based_pricing(self):
        result = self._review(
            "Your site has no enquiry form. Given your nationality we can lower the "
            "price for you. Interested?")
        self.assertFalse(result.approved)


class GuardianFulfilmentTests(PhaseABase):
    """Winston must not promise delivery YardLink cannot perform."""

    def setUp(self):
        super().setUp()
        self._research()
        self._verify_ordering()

    def test_blocks_automatic_publishing_claim(self):
        result = self._review("Your site has no enquiry form. We build it and "
                              "one-click publish it live. Interested?")
        self.assertFalse(result.approved)
        self.assertIn("unsupported_fulfilment", [i["rule"] for i in result.issues])

    def test_blocks_guaranteed_seo_or_leads(self):
        for claim in ("We guarantee more leads.", "We guarantee top SEO rankings."):
            result = self._review(f"Your site has no enquiry form. {claim} Interested?")
            self.assertFalse(result.approved, claim)

    def test_blocks_unlimited_scope(self):
        result = self._review("Your site has no enquiry form. Unlimited revisions "
                              "included. Interested?")
        self.assertFalse(result.approved)

    def test_blocks_invented_delivery_times(self):
        result = self._review("Your site has no enquiry form. We deliver in just "
                              "3 days. Interested?")
        self.assertFalse(result.approved)

    def test_allows_an_honest_offer(self):
        result = self._review(
            "I noticed your site has no enquiry form, so customers have to call. "
            "YardLink builds sites with lead capture built in. "
            "Would it help to see what that could look like?")
        self.assertTrue(result.approved, f"unexpected: {result.issues}")


class GuardianSafetyTests(PhaseABase):
    def setUp(self):
        super().setUp()
        self._research()
        self._verify_ordering()
        self.clean = ("I noticed your site has no enquiry form, so customers have to "
                      "call. YardLink builds lead capture into the site. Would that be useful?")

    def test_guardian_blocks_suppressed_recipient(self):
        self.repo.suppress("irie@example.com", "unsubscribed")
        result = self._review(self.clean)
        self.assertFalse(result.approved)
        self.assertIn("suppressed_recipient", [i["rule"] for i in result.issues])

    def test_guardian_blocks_duplicate_outreach(self):
        ledger = CommercialLedger(self.repo)
        ledger.record_message(contact_id=self.contact_id, to_email="irie@example.com",
                              subject="s", body="b", source="test", source_record_id="m1")
        result = self._review(self.clean)
        self.assertFalse(result.approved)
        self.assertIn("duplicate_outreach", [i["rule"] for i in result.issues])

    def test_guardian_blocks_contact_without_email(self):
        contact = self._contact()
        contact["email"] = ""
        result = self.guardian.review(subject="s", body=self.clean, contact=contact,
                                      brief=self._brief())
        self.assertFalse(result.approved)

    def test_guardian_result_is_structured(self):
        payload = self._review(self.clean).as_dict()
        for key in ("approved", "issues", "warnings", "claim_checks",
                    "evidence_checks", "style_checks", "commercial_checks", "confidence"):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
