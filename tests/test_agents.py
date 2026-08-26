"""Agent contracts must describe the code that exists.

The failure mode here is a registry that drifts: a list of names in a document that
outlives the code it claims, so a dashboard reports eleven autonomous agents when there
are four functions. These tests resolve every implementation and derive every status from
the database, so the registry cannot say something Winston cannot do.
"""
import tempfile
import unittest
from pathlib import Path

from winston.agents import (
    CONTRACTS, AgentRegistry, Kind, Status, unresolved_references,
    verify_implementations)
from winston.commercial import CommercialLedger
from winston.repository import WinstonRepository


class ContractIntegrityTests(unittest.TestCase):
    def test_every_operational_contract_resolves(self):
        """A role claiming to work must point at code that exists."""
        self.assertEqual(verify_implementations(), [])

    def test_planned_roles_are_allowed_to_be_missing(self):
        """Negotiator has no module because there is nothing to build against yet."""
        unresolved = unresolved_references()
        for reference in unresolved:
            contract = next(c for c in CONTRACTS if c.reference == reference)
            self.assertFalse(contract.declared_status.is_operational,
                             f"{reference} is missing but claims to be operational")

    def test_contracts_declare_inputs_and_outputs(self):
        for contract in CONTRACTS:
            with self.subTest(agent=contract.name):
                self.assertTrue(contract.inputs)
                self.assertTrue(contract.outputs)
                self.assertTrue(contract.purpose)

    def test_deterministic_roles_are_labelled_deterministic(self):
        """Calling a pure function an autonomous agent would be theatre."""
        expected = {"Auditor", "Researcher", "Pricer", "Guardian", "Fit Engine"}
        for contract in CONTRACTS:
            if contract.name in expected:
                self.assertIs(contract.kind, Kind.DETERMINISTIC, contract.name)

    def test_every_role_declares_how_it_refuses(self):
        """A role that cannot refuse anything is not a safeguard."""
        for contract in CONTRACTS:
            if contract.declared_status.is_operational:
                with self.subTest(agent=contract.name):
                    self.assertTrue(contract.refusals or contract.failure_states)

    def test_agent_names_are_unique(self):
        names = [c.name for c in CONTRACTS]
        self.assertEqual(len(names), len(set(names)))


class DerivedStatusTests(unittest.TestCase):
    """Status comes from the database, not from a constant someone typed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "agents.db")
        self.repo.initialize()
        self.ledger = CommercialLedger(self.repo)
        self.ledger.initialize()
        self.registry = AgentRegistry(self.repo, self.ledger)
        self.registry.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def _agent(self, name):
        return next(a for a in self.registry.describe() if a["name"] == name)

    def test_inbox_is_not_active_without_a_successful_scan(self):
        agent = self._agent("Inbox")
        self.assertEqual(agent["status"], Status.BLOCKED_EXTERNAL.value)
        self.assertFalse(agent["operational"])
        self.assertIn("no successful mailbox scan", agent["status_reason"])

    def test_inbox_becomes_active_once_a_scan_has_run(self):
        self.repo.set_setting("inbox_last_scanned_at", "2026-08-24T10:00:00+00:00")
        self.assertEqual(self._agent("Inbox")["status"], Status.ACTIVE.value)

    def test_negotiator_is_planned_while_no_replies_exist(self):
        agent = self._agent("Negotiator")
        self.assertEqual(agent["status"], Status.PLANNED.value)
        self.assertFalse(agent["operational"])

    def test_learner_reports_insufficient_data_rather_than_active(self):
        agent = self._agent("Learner")
        self.assertEqual(agent["status"], Status.ACTIVE_WITH_INSUFFICIENT_DATA.value)
        self.assertIn("0 eligible", agent["status_reason"])

    def test_learner_excludes_legacy_from_its_status(self):
        contact_id, _ = self.repo.upsert_contact(
            {"name": "A", "email": "a@example.com", "place_id": "p"}, "test")
        self.ledger.record_message(
            contact_id=contact_id, to_email="a@example.com", subject="s", body="b",
            source="backfill:sent_messages", source_record_id="legacy-1")
        agent = self._agent("Learner")
        self.assertEqual(agent["status"], Status.ACTIVE_WITH_INSUFFICIENT_DATA.value,
                         "a legacy message must not make Learner active")

    def test_ml_is_not_listed_as_an_agent(self):
        names = {a["name"] for a in self.registry.describe()}
        self.assertNotIn("ML", names)
        self.assertEqual(self.registry.summary()["ml"]["status"], "insufficient_data")

    def test_execution_history_is_absent_not_zero(self):
        """No runs means no history, which is different from a zero success rate."""
        agent = self._agent("Writer")
        self.assertFalse(agent["execution"]["has_history"])
        self.assertIsNone(agent["execution"]["success_rate"])

    def test_recorded_executions_appear(self):
        self.registry.record("Writer", status="ok", latency_ms=120,
                             provider="ollama", model="qwen3:8b")
        self.registry.record("Writer", status="refused", latency_ms=30)
        execution = self._agent("Writer")["execution"]
        self.assertEqual(execution["runs"], 2)
        self.assertEqual(execution["successes"], 1)
        self.assertEqual(execution["failures"], 1)
        self.assertEqual(execution["success_rate"], 0.5)

    def test_summary_counts_match_the_contracts(self):
        summary = self.registry.summary()
        self.assertEqual(summary["total"], len(CONTRACTS))
        self.assertEqual(summary["deterministic"] + summary["model_driven"],
                         summary["total"])


class PipelineSeparationTests(unittest.TestCase):
    """The contracts must reflect the real separation of concerns."""

    def _contract(self, name):
        return next(c for c in CONTRACTS if c.name == name)

    def test_guardian_does_not_depend_on_either_writer(self):
        """Guardian must be able to refuse whatever produced the text."""
        dependencies = self._contract("Guardian").dependencies
        self.assertNotIn("Writer", dependencies)
        self.assertNotIn("Question Writer", dependencies)

    def test_both_writers_exist_as_distinct_roles(self):
        claim = self._contract("Writer")
        question = self._contract("Question Writer")
        self.assertEqual(claim.outputs["mode"], "claim")
        self.assertEqual(question.outputs["mode"], "question")
        self.assertNotEqual(claim.attribute, question.attribute)

    def test_question_writer_refuses_to_assert(self):
        refusals = " ".join(self._contract("Question Writer").refusals).casefold()
        self.assertIn("will not assert", refusals)

    def test_auditor_refuses_to_assert_from_absence(self):
        refusals = " ".join(self._contract("Auditor").refusals).casefold()
        self.assertIn("absence", refusals)

    def test_pricer_refuses_protected_characteristics(self):
        refusals = " ".join(self._contract("Pricer").refusals).casefold()
        self.assertIn("protected characteristic", refusals)

    def test_proof_ranking_is_defined_exactly_once(self):
        """Two ranking paths would let the writers and the fit engine disagree.

        The test does not care which module owns it, only that one does. An earlier
        version asserted a specific location and failed when the canonical definition
        was in fit.py rather than writer.py, which was a wrong test rather than a
        duplicate implementation.
        """
        import ast
        package = Path(__file__).resolve().parent.parent / "winston"
        definitions = []
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text())
            definitions += [f"{path.name}.{n.name}" for n in ast.walk(tree)
                            if isinstance(n, ast.FunctionDef) and n.name == "select_proof"]
        self.assertEqual(len(definitions), 1,
                         f"proof ranking must have one definition, found {definitions}")

    def test_both_writers_use_the_same_proof_ranking(self):
        from winston import questions, writer
        self.assertIs(writer.select_proof, questions.select_proof)

    def test_guardian_lists_question_mode_drift_as_a_refusal(self):
        refusals = " ".join(self._contract("Guardian").refusals).casefold()
        self.assertIn("assertion drift", refusals)


if __name__ == "__main__":
    unittest.main()
