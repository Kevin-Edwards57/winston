"""Question mode — asking about what Winston could not confirm.

Assertability closed a real hole: a problem derived from absence became a sales claim,
and 11 of 13 "no analytics" findings turned out to be wrong. Inferred problems are now
excluded from outreach entirely.

That fix was correct and slightly too blunt. Across 59 researched prospects there are 19
inferred booking gaps and 15 inferred ordering gaps. Winston cannot say *"you have no
online booking"* because it does not know that. It can perfectly honestly ask *"do you
take bookings online?"* — a question makes no claim, so it cannot be a false one.

The distinction this module exists to hold:

    CONFIRMED   observed directly        may state it        -> Writer
    INFERRED    derived from absence     may ask about it    -> QuestionWriter
    UNKNOWN     insufficient research    neither             -> nothing

The failure mode to guard against is drift. A question that answers itself —
*"I noticed you don't offer online booking, do you?"* — is an assertion wearing a
question mark, and Guardian rejects it. Question mode must not become the loophole that
assertability closed.

An investigation also requires somewhere to go commercially. Asking a barbershop about
booking is only worth anyone's time if YardLink can actually build one, so an inferred
problem with no verified offer behind it produces nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .catalog import Catalog
from .fit import INFERRED, FitEngine, FitResult, Problem
from .repository import WinstonRepository, utc_now
from .writer import MAX_PROOF, select_proof, strip_em_dashes

# An investigation is only worth raising if the answer could lead somewhere. These are
# the questions themselves, phrased so that a "yes" is a perfectly good outcome.
QUESTION_TOPICS: dict[str, dict[str, str]] = {
    "no_online_booking": {
        "topic": "online booking",
        "question": "do customers book with you online, or do they call?",
        "why_it_matters": "an appointment business loses the customer who will not phone",
    },
    "no_online_ordering": {
        "topic": "online ordering",
        "question": "can people order from you online, or is it phone and walk-in?",
        "why_it_matters": "a food business without ordering competes on convenience it does not have",
    },
    "no_measurement": {
        "topic": "analytics",
        "question": "do you have any way of seeing where your website visitors come from?",
        "why_it_matters": "marketing spend that cannot be measured cannot be improved",
    },
    "outdated_website": {
        "topic": "the site's age",
        "question": "when was the site last updated?",
        "why_it_matters": "an unmaintained site quietly costs enquiries",
    },
}

SYSTEM_PROMPT = """You write short, genuinely curious emails for YardLink Studio, a \
software studio in New York.

This email ASKS A QUESTION. It does not make a claim.

Winston could not determine the answer from the business's website, so you must not \
pretend to know it. The recipient knows the answer and you do not.

Absolute rules:
- Never assert that the business lacks anything. You do not know that.
- Never write "I noticed you don't", "your site doesn't have", or "you're missing".
- Ask a real question and stop. A question that answers itself is a claim.
- Never use an em dash. Use a comma or a full stop.
- Never write "I hope this email finds you well".
- Never invent statistics, results, guarantees or client names.
- Do not compliment the business generically.

Structure:
1. One short sentence saying you looked at their site and could not tell.
2. The question itself, directly.
3. One sentence, conditional, on what YardLink builds if the answer is no.
4. Nothing else.

Write 50 to 90 words. Plain text. Sound like a person who is actually asking, not one \
who has already decided."""


@dataclass
class InvestigationOpportunity:
    """An inferred problem worth asking about, with somewhere to go if the answer is no."""
    contact_id: str
    business: str
    industry: str
    problem_code: str
    topic: str
    question: str
    why_it_matters: str
    evidence: str
    confidence: float
    limitations: list[str] = field(default_factory=list)
    potential_offer: dict[str, Any] | None = None
    proof: list[dict[str, Any]] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        """Worth asking only if YardLink could act on a 'no'."""
        return self.potential_offer is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id, "business": self.business,
            "industry": self.industry, "problem_code": self.problem_code,
            "topic": self.topic, "question": self.question,
            "why_it_matters": self.why_it_matters,
            "evidence": self.evidence, "confidence": round(self.confidence, 2),
            "limitations": self.limitations,
            "potential_offer": self.potential_offer, "proof": self.proof,
            "actionable": self.actionable,
            "assertability": INFERRED,
            "commercially_assertable": False,
        }


@dataclass
class QuestionDraft:
    """A question-mode draft. Deliberately a different type from a claim-based Draft."""
    subject: str = ""
    body: str = ""
    status: str = "drafted"      # drafted | no_investigation | no_offer | failed
    mode: str = "question"
    brief: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject, "body": self.body, "status": self.status,
            "mode": self.mode, "brief": self.brief, "error": self.error,
            "generation": {"provider": self.provider, "model": self.model,
                           "generated_at": utc_now()},
        }


class InvestigationEngine:
    """Finds inferred problems worth asking a business about."""

    def __init__(self, repository: WinstonRepository, catalog: Catalog,
                 fit_engine: FitEngine) -> None:
        self.repository = repository
        self.catalog = catalog
        self.fit = fit_engine

    def _offer_for(self, problem_code: str, industry: str) -> dict[str, Any] | None:
        """A verified, offerable service that would address this if the answer is no."""
        readable = problem_code.replace("_", " ")
        for entry in self.catalog.offerable():
            solved = {str(s).casefold() for s in (entry.get("problems_solved") or [])}
            if any(readable == s or readable in s or s in readable for s in solved):
                return {"slug": entry["slug"], "name": entry["name"],
                        "kind": entry["kind"]}
        return None

    def investigate(self, contact_id: str) -> list[InvestigationOpportunity]:
        """Inferred problems for one prospect that are worth a question."""
        with self.repository.read() as connection:
            contact = connection.execute(
                "SELECT id,name,business_type FROM contacts WHERE id=?",
                (contact_id,)).fetchone()
        if contact is None:
            raise KeyError(f"Unknown contact {contact_id}")

        assessment: FitResult = self.fit.assess(contact_id)
        industry = (contact["business_type"] or "").casefold().strip()

        opportunities: list[InvestigationOpportunity] = []
        for problem in assessment.inferred_problems:
            topic = QUESTION_TOPICS.get(problem.code)
            if topic is None:
                continue   # inferred, but nothing sensible to ask
            offer = self._offer_for(problem.code, industry)
            proof = (select_proof(self.catalog, self.catalog.get(offer["slug"]) or {},
                                  industry, {problem.code}, limit=1)
                     if offer else [])
            opportunities.append(InvestigationOpportunity(
                contact_id=contact_id, business=contact["name"], industry=industry,
                problem_code=problem.code, topic=topic["topic"],
                question=topic["question"], why_it_matters=topic["why_it_matters"],
                evidence=problem.evidence, confidence=problem.confidence,
                limitations=problem.limitations, potential_offer=offer, proof=proof))

        # Strongest first, but an unactionable investigation ranks below any actionable
        # one however confident it is: a question nobody can follow up on is noise.
        opportunities.sort(key=lambda o: (not o.actionable, -o.confidence))
        return opportunities

    def summary(self) -> dict[str, Any]:
        """How much inferred evidence exists, and how much of it is worth asking about."""
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT DISTINCT c.id FROM contacts c
                   JOIN research_runs r ON r.contact_id=c.id AND r.status='ok'""").fetchall()
        actionable = unactionable = 0
        by_topic: dict[str, int] = {}
        for row in rows:
            for opportunity in self.investigate(row["id"]):
                if opportunity.actionable:
                    actionable += 1
                    by_topic[opportunity.topic] = by_topic.get(opportunity.topic, 0) + 1
                else:
                    unactionable += 1
        return {
            "researched_prospects": len(rows),
            "actionable_investigations": actionable,
            "unactionable_investigations": unactionable,
            "by_topic": by_topic,
            "note": ("Actionable means an inferred problem with a verified offerable "
                     "service behind it. These may be asked about, never asserted."),
        }


class QuestionWriter:
    """Writes questions. Structurally cannot write claims, because Guardian checks."""

    def __init__(self, repository: WinstonRepository, catalog: Catalog,
                 investigations: InvestigationEngine, ai_service: Any) -> None:
        self.repository = repository
        self.catalog = catalog
        self.investigations = investigations
        self.ai_service = ai_service

    def build_brief(self, contact_id: str) -> dict[str, Any]:
        opportunities = self.investigations.investigate(contact_id)
        actionable = [o for o in opportunities if o.actionable]
        with self.repository.read() as connection:
            contact = connection.execute(
                "SELECT id,name,email,business_type,address,website FROM contacts WHERE id=?",
                (contact_id,)).fetchone()

        lead = actionable[0] if actionable else None
        return {
            "contact_id": contact_id,
            "business": contact["name"] if contact else "",
            "industry": (contact["business_type"] if contact else "") or "business",
            "website": (contact["website"] if contact else "") or "",
            "email": (contact["email"] if contact else "") or "",
            "mode": "question",
            # Deliberately empty. A question-mode brief asserts nothing, so Guardian's
            # claim checks have nothing to license and every assertion fails.
            "observed_problems": [],
            "investigations": [o.as_dict() for o in opportunities],
            "lead_investigation": lead.as_dict() if lead else None,
            "recommended_service": lead.potential_offer if lead else None,
            "proof": lead.proof if lead else [],
            "status": "ok" if lead else "no_investigation",
        }

    def write(self, contact_id: str) -> QuestionDraft:
        brief = self.build_brief(contact_id)
        draft = QuestionDraft(brief=brief)

        if brief["status"] == "no_investigation":
            unactionable = [i for i in brief["investigations"] if not i["actionable"]]
            draft.status = "no_offer" if unactionable else "no_investigation"
            draft.error = (
                "Inferred problems exist but no verified service addresses them, so "
                "there is nothing to follow a 'no' with."
                if unactionable else
                "No inferred problem worth asking about.")
            return draft

        lead = brief["lead_investigation"]
        try:
            result = self.ai_service.generate(
                self._prompt(brief, lead), system=SYSTEM_PROMPT, max_tokens=320,
                purpose="question_draft")
        except Exception as exc:
            draft.status = "failed"
            draft.error = f"{type(exc).__name__}: {exc}"
            return draft

        draft.body = strip_em_dashes((result.text or "").strip())
        draft.subject = strip_em_dashes(f"Quick question about {lead['topic']}")
        draft.provider, draft.model = result.provider, result.model
        if not draft.body:
            draft.status, draft.error = "failed", "Provider returned no text"
        return draft

    @staticmethod
    def _prompt(brief: dict[str, Any], lead: dict[str, Any]) -> str:
        offer = lead.get("potential_offer") or {}
        proof = brief.get("proof") or []
        proof_line = (f"- {proof[0]['name']}: {proof[0].get('description','')}"
                      if proof else "- none, do not mention any YardLink project")
        return f"""BRIEF

Business: {brief['business']}
Industry: {brief['industry']}

What you could NOT determine from their website:
- {lead['topic']}
- what Winston saw: {lead['evidence']}
- why the answer is uncertain: {'; '.join(lead['limitations']) or 'not established'}

The question to ask:
{lead['question']}

Why it matters commercially, for your own understanding only:
{lead['why_it_matters']}

If the answer turns out to be no, YardLink Studio offers:
- {offer.get('name', 'nothing relevant')}

Proof you may mention at most once, only if it fits naturally:
{proof_line}

Ask the question. Do not answer it for them. Do not claim they lack anything. Sign off as:
Kevin
YardLink Studio"""
