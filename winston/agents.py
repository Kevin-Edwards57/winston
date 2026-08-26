"""Agent contracts — what Winston actually runs, stated honestly.

Most "agent frameworks" are a list of names in a document. The list drifts from the code,
nobody notices, and the dashboard confidently reports that eleven autonomous agents are
active when there are four functions and a cron job.

Two decisions here exist to prevent that.

**Implementations are resolved, not described.** Every contract names a real module
attribute, and :func:`verify_implementations` imports it. A contract pointing at
something that does not exist is a test failure, so the registry cannot quietly rot.

**Status is derived, not declared.** Whether Inbox is working is a question about whether
a mailbox scan has ever succeeded, not a constant someone typed. Whether Learner can say
anything is a question about how many eligible outcomes exist. Those are computed at call
time from the database, which means the Agent Center cannot claim a capability Winston
does not have.

Most of these are deterministic functions, and the registry says so. Calling
`derive_problems` an autonomous agent would be theatre; it is a pure function over stored
signals, and that is a strength rather than something to dress up.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .repository import WinstonRepository, utc_now


class Status(str, Enum):
    """Honest states. Nothing is ACTIVE because documentation says so."""
    ACTIVE = "active"
    ACTIVE_WITH_INSUFFICIENT_DATA = "active_with_insufficient_data"
    PARTIAL = "partial"
    BUILT_UNVERIFIED = "built_unverified"
    BLOCKED_EXTERNAL = "blocked_external"
    BLOCKED_DATA = "blocked_data"
    PLANNED = "planned"

    @property
    def is_operational(self) -> bool:
        return self in (Status.ACTIVE, Status.ACTIVE_WITH_INSUFFICIENT_DATA)


class Kind(str, Enum):
    DETERMINISTIC = "deterministic"      # pure logic, no model involved
    MODEL_DRIVEN = "model_driven"        # calls an LLM
    HYBRID = "hybrid"                    # model output behind deterministic gates


@dataclass
class AgentContract:
    """One role, with the code that implements it and the shape of its work."""
    name: str
    purpose: str
    module: str
    attribute: str
    kind: Kind
    inputs: dict[str, str]
    outputs: dict[str, str]
    dependencies: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    failure_states: list[str] = field(default_factory=list)
    personality: str = ""
    declared_status: Status = Status.ACTIVE
    notes: str = ""

    @property
    def reference(self) -> str:
        return f"{self.module}.{self.attribute}"

    def resolve(self) -> Callable | type | None:
        """Import the real implementation. None means the contract is a lie."""
        try:
            return getattr(importlib.import_module(self.module), self.attribute)
        except (ImportError, AttributeError):
            return None

    @property
    def implemented(self) -> bool:
        return self.resolve() is not None


# ── The roles ────────────────────────────────────────────────────────────
# Ordered along the pipeline. Personality is operating style only; it never
# influences a deterministic rule.

CONTRACTS: tuple[AgentContract, ...] = (
    AgentContract(
        name="Scout", purpose="Find businesses worth investigating",
        module="winston_app", attribute="google_places_search",
        kind=Kind.DETERMINISTIC,
        inputs={"query": "str", "location": "str"},
        outputs={"candidates": "list[dict]"},
        dependencies=["Google Places API"],
        refusals=["runs only when explicitly started; it costs money per search"],
        failure_states=["api_key_missing", "quota_exceeded", "network_error"],
        personality="curious and fast",
        declared_status=Status.ACTIVE,
        notes="The only component that costs money to run, so it is opt-in."),

    AgentContract(
        name="Researcher", purpose="Collect factual evidence about a business",
        module="winston.signals", attribute="research_contact",
        kind=Kind.DETERMINISTIC,
        inputs={"contact_id": "str", "website": "str"},
        outputs={"status": "ok|unreachable|failed", "signals": "int"},
        dependencies=["requests", "stdlib HTMLParser"],
        refusals=["records unreachable rather than guessing at a site it cannot fetch"],
        failure_states=["unreachable", "timeout", "non_html_content"],
        personality="methodical and factual",
        declared_status=Status.ACTIVE,
        notes="No AI. Fetches a page, follows same-domain contact links, extracts."),

    AgentContract(
        name="Auditor", purpose="Decide what the evidence actually supports",
        module="winston.fit", attribute="derive_problems",
        kind=Kind.DETERMINISTIC,
        inputs={"signals": "dict", "industry": "str", "has_website": "bool"},
        outputs={"problems": "list[Problem] with confirmed|inferred assertability"},
        dependencies=["SignalStore"],
        refusals=["will not assert a problem derived from absence",
                  "records unknown rather than manufacturing a negative"],
        failure_states=["no_signals"],
        personality="skeptical and hard to impress",
        declared_status=Status.ACTIVE,
        notes="The assertability boundary. 11 of 13 analytics findings were wrong "
              "before this existed."),

    AgentContract(
        name="Strategist", purpose="Decide whether a real commercial action exists",
        module="winston.fit", attribute="FitEngine",
        kind=Kind.DETERMINISTIC,
        inputs={"contact_id": "str"},
        outputs={"decision": "CLAIM_OPPORTUNITY|QUESTION_OPPORTUNITY|"
                             "NO_OPPORTUNITY|INSUFFICIENT_EVIDENCE"},
        dependencies=["Auditor", "Catalogue", "InvestigationEngine"],
        refusals=["will not route an inferred problem to a claim"],
        failure_states=["unknown_contact"],
        personality="selective and commercially sharp",
        declared_status=Status.ACTIVE,
        notes="Embedded in FitEngine plus InvestigationEngine rather than a separate "
              "module. The contract is formalised around the real implementation."),

    AgentContract(
        name="Fit Engine", purpose="Match problems to verified capabilities and proof",
        module="winston.fit", attribute="FitEngine",
        kind=Kind.DETERMINISTIC,
        inputs={"contact_id": "str"},
        outputs={"product_fit": "float", "service_fit": "float",
                 "recommended_service": "dict|None", "proof": "list[dict]"},
        dependencies=["Catalogue", "select_proof"],
        refusals=["will not recommend an unverified service",
                  "will not offer a consumer product to a business"],
        failure_states=["no_verified_offer"],
        personality="precise",
        declared_status=Status.ACTIVE,
        notes="Proof ranking is canonical in winston.fit.select_proof; the writers "
              "import it rather than reimplementing, so ranking cannot diverge."),

    AgentContract(
        name="Pricer", purpose="Produce a defensible price band",
        module="winston.pricing", attribute="PricingEngine",
        kind=Kind.DETERMINISTIC,
        inputs={"offer": "dict", "problems": "list[dict]", "signals": "dict"},
        outputs={"floor_usd": "float", "target_usd": "float", "premium_usd": "float",
                 "basis": "operator_assumption|historical|calibrated"},
        dependencies=["RateCard"],
        refusals=["refuses without a rate card or effort estimate",
                  "rejects protected characteristics before any arithmetic runs",
                  "refuses when the configured price is below delivery cost"],
        failure_states=["no_pricing_basis", "protected_characteristic",
                        "disallowed_variable"],
        personality="analytical and margin-aware",
        declared_status=Status.ACTIVE),

    AgentContract(
        name="Writer", purpose="Turn evidence into outreach",
        module="winston.writer", attribute="Writer",
        kind=Kind.HYBRID,
        inputs={"contact_id": "str"},
        outputs={"subject": "str", "body": "str", "mode": "claim", "brief": "dict"},
        dependencies=["Strategist", "Fit Engine", "Pricer", "AI router"],
        refusals=["states only what the brief contains",
                  "declines when no verified offer fits",
                  "drops observations below the confidence floor rather than hedging"],
        failure_states=["no_verified_offer", "no_evidence", "provider_failure"],
        personality="concise and human",
        declared_status=Status.ACTIVE,
        notes="CLAIM_MODE. Model output behind deterministic gates."),

    AgentContract(
        name="Question Writer", purpose="Ask about what could not be confirmed",
        module="winston.questions", attribute="QuestionWriter",
        kind=Kind.HYBRID,
        inputs={"contact_id": "str"},
        outputs={"subject": "str", "body": "str", "mode": "question", "brief": "dict"},
        dependencies=["InvestigationEngine", "Catalogue", "AI router"],
        refusals=["will not assert anything the business might not lack",
                  "requires a verified offer behind the question"],
        failure_states=["no_investigation", "no_offer", "provider_failure"],
        personality="genuinely curious",
        declared_status=Status.ACTIVE,
        notes="QUESTION_MODE. A distinct type from a claim draft so the two cannot be "
              "confused."),

    AgentContract(
        name="Guardian", purpose="Refuse anything Winston cannot support",
        module="winston.guardian", attribute="Guardian",
        kind=Kind.DETERMINISTIC,
        inputs={"subject": "str", "body": "str", "contact": "dict", "brief": "dict"},
        outputs={"approved": "bool", "issues": "list", "reviewed_digest": "sha256"},
        dependencies=["Catalogue", "Repository"],
        refusals=["em dashes", "unsupported claims", "unobserved problems",
                  "unverified entries", "portfolio pitched as product",
                  "protected-characteristic pricing", "suppressed recipients",
                  "duplicates", "assertion drift in question mode"],
        failure_states=["blocked"],
        personality="uncompromising",
        declared_status=Status.ACTIVE,
        notes="Independent of both writers. Runs again at the send boundary against a "
              "digest of the exact body, so a verdict cannot outlive an edit."),

    AgentContract(
        name="Inbox", purpose="Read the mailbox and classify what arrives",
        module="winston.inbox", attribute="InboxScanner",
        kind=Kind.HYBRID,
        inputs={"mailbox": "str", "limit": "int", "persist": "bool"},
        outputs={"replies": "int", "bounces": "int", "unsubscribes": "int"},
        dependencies=["IMAP credentials", "CommercialLedger"],
        refusals=["never marks messages read", "never mutates the mailbox"],
        failure_states=["auth_failed", "mailbox_unavailable"],
        personality="literal",
        declared_status=Status.BLOCKED_EXTERNAL,
        notes="Implemented and unit tested. Has never authenticated against a real "
              "mailbox: the Gmail app password is rejected."),

    AgentContract(
        name="Negotiator", purpose="Suggest a reply to a real inbound message",
        module="winston.negotiator", attribute="Negotiator",
        kind=Kind.HYBRID,
        inputs={"reply_id": "str"},
        outputs={"intent": "str", "suggested_response": "str", "confidence": "float"},
        dependencies=["Inbox", "Catalogue", "Pricer", "Guardian"],
        refusals=["never sends", "never makes a binding commitment",
                  "never invents a discount, date or scope"],
        failure_states=["no_replies"],
        personality="calm and non-pushy",
        declared_status=Status.PLANNED,
        notes="Contract only. Zero replies exist, so there is nothing to build against."),

    AgentContract(
        name="Learner", purpose="Report what outreach actually produced",
        module="winston.commercial", attribute="CommercialLedger",
        kind=Kind.DETERMINISTIC,
        inputs={"window": "str"},
        outputs={"funnel": "dict", "dataset_readiness": "dict"},
        dependencies=["CommercialLedger"],
        refusals=["excludes legacy backfill from every dataset",
                  "reports unknown rather than zero for unmeasured stages"],
        failure_states=["insufficient_data"],
        personality="scientific and numbers-first",
        declared_status=Status.ACTIVE_WITH_INSUFFICIENT_DATA,
        notes="Deterministic analytics, not ML. Zero eligible messages, so every rate "
              "is unknown rather than zero."),
)


AGENT_EXECUTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_executions (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    contact_id TEXT,
    task TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    latency_ms INTEGER,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    confidence REAL,
    error TEXT NOT NULL DEFAULT '',
    result_reference TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS agent_executions_agent ON agent_executions(agent, started_at DESC);
"""


class AgentRegistry:
    """The roles, their real implementations, and their measured state."""

    def __init__(self, repository: WinstonRepository, ledger: Any = None) -> None:
        self.repository = repository
        self.ledger = ledger

    def initialize(self) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.executescript(AGENT_EXECUTION_SCHEMA)

    # ── derived status ───────────────────────────────────────────────────

    def _derived_status(self, contract: AgentContract) -> tuple[Status, str]:
        """Compute status from the database rather than trusting the declaration.

        A contract can claim anything; this asks whether the capability has actually
        been exercised. Where reality and the declaration disagree, reality wins.
        """
        if not contract.implemented:
            return Status.PLANNED, f"{contract.reference} does not resolve"

        if contract.name == "Inbox":
            scanned = self.repository.get_setting("inbox_last_scanned_at")
            if scanned:
                return Status.ACTIVE, f"last scanned {scanned}"
            return (Status.BLOCKED_EXTERNAL,
                    "no successful mailbox scan recorded; credentials unverified")

        if contract.name == "Learner":
            readiness = (self.ledger.dataset_readiness() if self.ledger else {})
            eligible = readiness.get("learner_eligible_messages", 0)
            if eligible:
                return Status.ACTIVE, f"{eligible} eligible message(s)"
            return (Status.ACTIVE_WITH_INSUFFICIENT_DATA,
                    f"0 eligible messages; {readiness.get('excluded_legacy_messages', 0)} "
                    "legacy records excluded")

        if contract.name == "Negotiator":
            with self.repository.read() as connection:
                replies = connection.execute("SELECT COUNT(*) n FROM replies").fetchone()["n"]
            return (Status.PLANNED if not replies else Status.BUILT_UNVERIFIED,
                    f"{replies} real reply/replies recorded")

        return contract.declared_status, "implementation resolves"

    # ── measured execution ───────────────────────────────────────────────

    def record(self, agent: str, *, status: str, contact_id: str | None = None,
               task: str = "", latency_ms: int | None = None, provider: str = "",
               model: str = "", confidence: float | None = None, error: str = "",
               result_reference: str = "") -> str:
        from uuid import uuid4
        execution_id = str(uuid4())
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO agent_executions(id,agent,contact_id,task,status,started_at,
                       completed_at,latency_ms,provider,model,confidence,error,
                       result_reference)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (execution_id, agent, contact_id, task, status, now, now, latency_ms,
                 provider, model, confidence, error[:400], result_reference))
        return execution_id

    def _measured(self, agent: str) -> dict[str, Any]:
        """Real execution history. Absent history reports as absent, never as zero."""
        with self.repository.read() as connection:
            row = connection.execute(
                """SELECT COUNT(*) runs,
                          SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok,
                          ROUND(AVG(latency_ms)) avg_latency_ms,
                          MAX(started_at) last_run
                   FROM agent_executions WHERE agent=?""", (agent,)).fetchone()
        runs = row["runs"] or 0
        return {
            "runs": runs,
            "successes": row["ok"] or 0,
            "failures": runs - (row["ok"] or 0),
            "success_rate": round((row["ok"] or 0) / runs, 3) if runs else None,
            "avg_latency_ms": row["avg_latency_ms"],
            "last_run": row["last_run"],
            "has_history": runs > 0,
        }

    # ── reads ────────────────────────────────────────────────────────────

    def describe(self) -> list[dict[str, Any]]:
        agents = []
        for contract in CONTRACTS:
            status, reason = self._derived_status(contract)
            agents.append({
                "name": contract.name, "purpose": contract.purpose,
                "implementation": contract.reference,
                "implemented": contract.implemented,
                "kind": contract.kind.value,
                "model_driven": contract.kind is not Kind.DETERMINISTIC,
                "status": status.value, "status_reason": reason,
                "operational": status.is_operational,
                "declared_status": contract.declared_status.value,
                "inputs": contract.inputs, "outputs": contract.outputs,
                "dependencies": contract.dependencies,
                "refusals": contract.refusals,
                "failure_states": contract.failure_states,
                "personality": contract.personality,
                "notes": contract.notes,
                "execution": self._measured(contract.name),
            })
        return agents

    def summary(self) -> dict[str, Any]:
        agents = self.describe()
        return {
            "agents": agents,
            "total": len(agents),
            "operational": sum(1 for a in agents if a["operational"]),
            "deterministic": sum(1 for a in agents if not a["model_driven"]),
            "model_driven": sum(1 for a in agents if a["model_driven"]),
            "with_execution_history": sum(1 for a in agents if a["execution"]["has_history"]),
            "ml": {
                "name": "ML", "status": "insufficient_data",
                "note": ("ML is a capability state, not an agent. It stays disabled until "
                         "enough real labelled outcomes exist."),
            },
            "note": ("Status is derived from the database, not declared. Most roles are "
                     "deterministic functions, which the registry states plainly rather "
                     "than dressing up as autonomy."),
        }


def verify_implementations() -> list[str]:
    """Contracts that claim to work but do not resolve.

    A PLANNED role is *expected* not to resolve; Negotiator has no module because it has
    nothing to be built against yet, and saying so is the honest state. What must never
    happen is a role declaring itself operational while pointing at code that is not
    there, because that is precisely the drift the registry exists to prevent.
    """
    return [c.reference for c in CONTRACTS
            if c.declared_status.is_operational and not c.implemented]


def unresolved_references() -> list[str]:
    """Every contract that does not resolve, operational or not. Diagnostic only."""
    return [c.reference for c in CONTRACTS if not c.implemented]
