"""Question mode: asking about what could not be confirmed, without claiming it.

Assertability excluded inferred problems from outreach entirely, which was correct and
slightly too blunt. Across 59 researched prospects there are 19 inferred booking gaps
Winston can honestly ask about but never assert.

The failure this suite guards against is drift. "I noticed you don't offer online
booking, do you?" is an assertion wearing a question mark, and it must fail. Question
mode cannot become the loophole assertability closed.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from winston.catalog import Catalog
from winston.commercial import CommercialLedger
from winston.fit import FitEngine
from winston.guardian import Guardian
from winston.questions import InvestigationEngine, QuestionWriter
from winston.repository import WinstonRepository
from winston.signals import SignalStore, extract_signals

# A salon site with no booking widget. Booking absence is inferred, never confirmed.
SALON = """<!doctype html><html><head><title>Estelle Hair</title>
<meta name="viewport" content="width=device-width"><meta name="description" content="x">
</head><body><h1>Estelle Hair Studio</h1><p>Call us on 555 0100</p>
<form><input type="email"></form><footer>&copy; 2026</footer></body></html>"""


class QuestionModeBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "q.db")
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
            text="I had a look at your site and could not tell either way. Do customers "
                 "book with you online, or do they call? If it is calls, we build "
                 "booking systems that let people pick a time from the site.",
            provider="ollama", model="qwen3:8b")

        self.investigations = InvestigationEngine(self.repo, self.catalog, self.fit)
        self.writer = QuestionWriter(self.repo, self.catalog, self.investigations, self.ai)

        self.contact_id, _ = self.repo.upsert_contact(
            {"name": "Estelle Hair Studio", "email": "estelle@example.com",
             "place_id": "p1", "type": "hair salon", "address": "Brooklyn, NY",
             "website": "https://estelle.example.com"}, "test")
        self.signals.record(self.contact_id,
                            extract_signals(SALON, "https://estelle.example.com"))
        run = self.signals.start_run(self.contact_id, "https://estelle.example.com")
        self.signals.complete_run(run, status="ok", pages=1, signals=9)

    def tearDown(self):
        self.temp.cleanup()

    def _enable_booking(self):
        self.catalog.verify("booking-systems", actor="test")

    def _contact(self):
        return dict(self.repo.connect().execute(
            "SELECT * FROM contacts WHERE id=?", (self.contact_id,)).fetchone())

    def _review(self, body, brief=None, subject="Quick question about online booking"):
        return self.guardian.review(
            subject=subject, body=body, contact=self._contact(),
            brief=brief if brief is not None else self.writer.build_brief(self.contact_id))


class InvestigationTests(QuestionModeBase):
    def test_inferred_problem_becomes_an_investigation(self):
        self._enable_booking()
        topics = {o.problem_code for o in self.investigations.investigate(self.contact_id)}
        self.assertIn("no_online_booking", topics)

    def test_an_investigation_is_never_assertable(self):
        self._enable_booking()
        for opportunity in self.investigations.investigate(self.contact_id):
            self.assertFalse(opportunity.as_dict()["commercially_assertable"])
            self.assertEqual(opportunity.as_dict()["assertability"], "inferred")

    def test_investigation_without_a_verified_offer_is_not_actionable(self):
        """Asking is only worth it if a 'no' leads somewhere."""
        opportunities = self.investigations.investigate(self.contact_id)
        booking = [o for o in opportunities if o.problem_code == "no_online_booking"]
        self.assertTrue(booking)
        self.assertFalse(booking[0].actionable, "booking-systems is not verified here")

    def test_verifying_the_service_makes_it_actionable(self):
        self._enable_booking()
        booking = [o for o in self.investigations.investigate(self.contact_id)
                   if o.problem_code == "no_online_booking"]
        self.assertTrue(booking[0].actionable)
        self.assertEqual(booking[0].potential_offer["slug"], "booking-systems")

    def test_investigations_carry_their_limitations(self):
        self._enable_booking()
        booking = [o for o in self.investigations.investigate(self.contact_id)
                   if o.problem_code == "no_online_booking"][0]
        self.assertTrue(booking.limitations)

    def test_actionable_investigations_rank_above_unactionable(self):
        self._enable_booking()
        opportunities = self.investigations.investigate(self.contact_id)
        actionable = [o.actionable for o in opportunities]
        self.assertEqual(actionable, sorted(actionable, reverse=True))

    def test_confirmed_problems_do_not_become_investigations(self):
        """A confirmed problem belongs in normal outreach, not a question."""
        self._enable_booking()
        codes = {o.problem_code for o in self.investigations.investigate(self.contact_id)}
        assertable = {p.code for p in self.fit.assess(self.contact_id).assertable_problems}
        self.assertEqual(codes & assertable, set())


class QuestionWriterTests(QuestionModeBase):
    def test_writer_refuses_without_a_verified_offer(self):
        draft = self.writer.write(self.contact_id)
        self.assertEqual(draft.status, "no_offer")
        self.ai.generate.assert_not_called()

    def test_writer_produces_a_question_draft(self):
        self._enable_booking()
        draft = self.writer.write(self.contact_id)
        self.assertEqual(draft.status, "drafted")
        self.assertEqual(draft.mode, "question")
        self.assertIn("?", draft.body)

    def test_brief_asserts_no_observed_problems(self):
        """The premise of question mode is that nothing is known to assert."""
        self._enable_booking()
        brief = self.writer.build_brief(self.contact_id)
        self.assertEqual(brief["observed_problems"], [])
        self.assertEqual(brief["mode"], "question")

    def test_prompt_never_states_the_business_lacks_anything(self):
        self._enable_booking()
        self.writer.write(self.contact_id)
        prompt = self.ai.generate.call_args[0][0].casefold()
        self.assertIn("could not determine", prompt)
        self.assertNotIn("you do not have", prompt)

    def test_em_dashes_are_stripped(self):
        self._enable_booking()
        self.ai.generate.return_value.text = "Do you book online — or by phone?"
        self.assertNotIn("—", self.writer.write(self.contact_id).body)


class AssertionDriftTests(QuestionModeBase):
    """The loophole this must not become."""

    def setUp(self):
        super().setUp()
        self._enable_booking()

    def test_an_honest_question_passes(self):
        result = self._review(
            "I had a look at your site and could not tell either way. Do customers book "
            "with you online, or do they call? If it is calls, we build booking systems.")
        self.assertTrue(result.approved, f"unexpected: {result.issues}")

    def test_a_question_that_answers_itself_is_blocked(self):
        result = self._review("I noticed you don't offer online booking. Do you?")
        self.assertFalse(result.approved)
        self.assertIn("question_mode_assertion", [i["rule"] for i in result.issues])

    def test_direct_assertions_are_blocked(self):
        for body in ("Your website doesn't have online booking. Interested?",
                     "You don't have a booking system. Want one?",
                     "You're missing online booking. Shall we talk?",
                     "Since you don't take bookings online, we can help. Interested?"):
            with self.subTest(body=body):
                self.assertFalse(self._review(body).approved)

    def test_a_draft_with_no_question_is_blocked(self):
        result = self._review("We build booking systems for hair salons.")
        self.assertFalse(result.approved)
        self.assertIn("question_mode_asserts_nothing_asked",
                      [i["rule"] for i in result.issues])

    def test_em_dash_still_blocked_in_question_mode(self):
        result = self._review("Do customers book online — or call?")
        self.assertFalse(result.approved)
        self.assertIn("no_em_dash", [i["rule"] for i in result.issues])

    def test_unverified_service_cannot_be_pitched_in_a_question(self):
        result = self._review(
            "Do customers book online? GuardLink could help you either way.")
        self.assertFalse(result.approved)
        self.assertIn("unverified_entry", [i["rule"] for i in result.issues])

    def test_consumer_product_cannot_be_sold_in_a_question(self):
        result = self._review(
            "Do customers book online? You should buy YardLink Eats to fix it.")
        self.assertFalse(result.approved)

    def test_guarantees_are_still_blocked(self):
        result = self._review("Do customers book online? We guarantee more bookings.")
        self.assertFalse(result.approved)

    def test_question_mode_without_an_investigation_is_blocked(self):
        result = self._review("Do customers book online?",
                              brief={"mode": "question", "lead_investigation": None})
        self.assertFalse(result.approved)
        self.assertIn("no_investigation", [i["rule"] for i in result.issues])

    def test_claim_mode_is_unaffected(self):
        """The normal path must still require observed evidence."""
        result = self.guardian.review(
            subject="s", body="Your site has no enquiry form. Interested?",
            contact=self._contact(), brief={"observed_problems": []})
        self.assertFalse(result.approved)
        self.assertIn("no_evidence", [i["rule"] for i in result.issues])


if __name__ == "__main__":
    unittest.main()
