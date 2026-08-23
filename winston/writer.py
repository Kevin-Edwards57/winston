"""Writer — evidence-based outreach.

The outreach this replaces opened the same way to all 1,396 prospects:

    "YardLink Studio is a NYC digital agency that builds fast, modern websites,
     AI chatbots, and automated tools that save business owners hours every week."

It said nothing about the business it was sent to, because it knew nothing about
them. Every signal, score, and catalogue entry built in Phase 2 was invisible to the
one component prospects actually see.

The Writer now receives a structured brief and builds outreach along a fixed spine:

    OBSERVATION -> PROBLEM -> YARDLINK CAPABILITY -> PROOF -> LOW-FRICTION CTA

Three constraints shape everything here.

**The brief is the only source of facts.** The model receives observed problems, the
recommended offer, and the selected proof, and is told it may state nothing else about
the business. It cannot invent a website problem because it never sees the website.

**Low-confidence observations are withheld entirely.** An observation below the floor
is not softened into a hedge, it is dropped. "Your site may not be mobile-friendly"
is a worse email than one that does not mention mobile at all.

**No verified offer means no email.** Where the catalogue has nothing YardLink can
honestly sell, the Writer returns ``no_verified_offer`` rather than reaching for the
nearest plausible service. Refusing to write is a valid outcome.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .catalog import Catalog
from .fit import FitEngine, FitResult
from .repository import WinstonRepository, utc_now
from .signals import SignalStore

CONFIDENCE_FLOOR = 0.5
MAX_PROOF = 2

# Outreach intent, chosen from the observed problem rather than from what YardLink
# would most like to sell. Order matters: the first matching problem wins.
INTENT_BY_PROBLEM = (
    ("no_website", "website opportunity"),
    ("no_online_ordering", "ordering opportunity"),
    ("no_online_booking", "booking opportunity"),
    ("not_mobile_friendly", "website opportunity"),
    ("no_lead_capture", "lead capture opportunity"),
    ("outdated_website", "website opportunity"),
    ("no_ssl", "website opportunity"),
    ("weak_seo_basics", "SEO opportunity"),
    ("no_measurement", "automation opportunity"),
)

SYSTEM_PROMPT = """You write short outreach emails for YardLink Studio, a software \
studio in New York.

You will be given a brief containing everything that is known about one business. \
You may state facts from the brief and nothing else.

Absolute rules:
- Never state a fact about the business that is not in the brief.
- Never invent statistics, percentages, dollar figures, results, guarantees, client \
names, or case studies.
- Never use an em dash. Use a comma, a full stop, or a semicolon.
- Never write "I hope this email finds you well" or any similar filler opening.
- Never use marketing language such as revolutionary, cutting-edge, game-changing, \
or industry-leading.
- Do not compliment the business generically. Reference only the specific observation.

Structure:
1. One sentence naming the specific thing you observed about their site.
2. One sentence on why that costs them something, in plain commercial terms.
3. One sentence on what YardLink builds that addresses it.
4. One short sentence citing the relevant proof project, if provided.
5. A low-friction question as the closing line.

Write 80 to 130 words. Plain text. No bullet points, no markdown, no subject line \
in the body. Sound like one person who looked at their website, not like marketing."""


@dataclass
class Draft:
    """A generated draft with the provenance the Learner will need later."""
    subject: str = ""
    body: str = ""
    status: str = "drafted"          # drafted | no_verified_offer | no_evidence | failed
    intent: str = ""
    brief: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject, "body": self.body, "status": self.status,
            "intent": self.intent, "brief": self.brief,
            "generation": {
                "provider": self.provider, "model": self.model,
                "latency_ms": self.latency_ms, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "estimated_cost_usd": self.estimated_cost_usd,
                "generated_at": utc_now(),
            },
            "error": self.error,
        }


def strip_em_dashes(text: str) -> str:
    """Remove em dashes at source. Guardian still enforces; this avoids losing a good draft."""
    text = re.sub(r"\s*—\s*", ", ", text)
    text = re.sub(r"\s*–\s*(?=\D)", ", ", text)
    return re.sub(r",\s*,", ",", text)


def select_proof(catalog: Catalog, offer_slug: str, industry: str,
                 limit: int = MAX_PROOF) -> list[dict[str, Any]]:
    """Pick the strongest evidence, not every project YardLink has ever built.

    Linked proof for the recommended offer ranks first. Standing in the prospect's
    own industry ranks next, because a restaurant recognising YardLink Eats carries
    more weight than a generic capability claim.
    """
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Standing in the prospect's own industry is evaluated FIRST. A restaurant owner
    # recognising YardLink Eats carries more weight than a generic capability claim,
    # and an earlier version buried it behind whichever proof happened to be linked.
    for entry in catalog.strategic_proof_for(industry):
        chosen.append({"slug": entry["slug"], "name": entry["name"],
                       "description": entry.get("description", ""),
                       "why": f"already active in the {industry} market"})
        seen.add(entry["slug"])

    for entry in catalog.proof_for(offer_slug):
        if entry["slug"] not in seen:
            chosen.append({"slug": entry["slug"], "name": entry["name"],
                           "description": entry.get("description", ""),
                           "why": "demonstrates the capability behind this offer"})
            seen.add(entry["slug"])

    return chosen[:limit]


class Writer:
    """Turns a prospect assessment into outreach, or declines to."""

    def __init__(self, repository: WinstonRepository, catalog: Catalog,
                 signal_store: SignalStore, fit_engine: FitEngine, ai_service: Any,
                 *, confidence_floor: float = CONFIDENCE_FLOOR) -> None:
        self.repository = repository
        self.catalog = catalog
        self.signals = signal_store
        self.fit = fit_engine
        self.ai_service = ai_service
        self.confidence_floor = confidence_floor

    # ── brief ────────────────────────────────────────────────────────────

    def build_brief(self, contact_id: str) -> dict[str, Any]:
        """Assemble everything the Writer is permitted to know."""
        with self.repository.read() as connection:
            contact = connection.execute(
                "SELECT id,name,email,business_type,address,website FROM contacts WHERE id=?",
                (contact_id,)).fetchone()
        if contact is None:
            raise KeyError(f"Unknown contact {contact_id}")

        assessment: FitResult = self.fit.assess(contact_id)
        industry = (contact["business_type"] or "").casefold().strip()

        # Only observations strong enough to assert. Weak ones are dropped, not hedged.
        usable = [p for p in assessment.problems if p.confidence >= self.confidence_floor]
        withheld = [p.code for p in assessment.problems if p.confidence < self.confidence_floor]

        offer = assessment.recommended_service or assessment.recommended_product
        intent, lead_code = "", ""
        codes = {p.code for p in usable}
        for code, label in INTENT_BY_PROBLEM:
            if code in codes:
                intent, lead_code = label, code
                break

        # The email must open on the problem that set the intent. Left unsorted, the
        # model led with whichever observation happened to be first, so an "ordering
        # opportunity" email opened by talking about mobile layout.
        usable.sort(key=lambda p: (p.code != lead_code, -p.severity))

        proof = select_proof(self.catalog, offer["slug"], industry) if offer else []

        return {
            "contact_id": contact_id,
            "business": contact["name"],
            "industry": contact["business_type"] or "business",
            "location": contact["address"] or "",
            "website": contact["website"] or "",
            "email": contact["email"] or "",
            "observed_problems": [p.as_dict() for p in usable],
            "withheld_low_confidence": withheld,
            "recommended_service": assessment.recommended_service,
            "recommended_product": assessment.recommended_product,
            "proof": proof,
            "strategic_standing": assessment.strategic_standing,
            "scores": assessment.as_dict()["scores"],
            "blockers": assessment.blockers,
            "intent": intent,
            "recommendation_status": "ok" if offer else "no_verified_offer",
        }

    # ── generation ───────────────────────────────────────────────────────

    def write(self, contact_id: str) -> Draft:
        brief = self.build_brief(contact_id)
        draft = Draft(brief=brief, intent=brief["intent"])

        if not brief["observed_problems"]:
            draft.status = "no_evidence"
            draft.error = ("No observation meets the confidence floor. Research the "
                           "prospect before generating outreach.")
            return draft

        if brief["recommendation_status"] == "no_verified_offer":
            draft.status = "no_verified_offer"
            relevant = ", ".join(p["label"] for p in brief["observed_problems"][:3])
            draft.error = (f"No verified YardLink service addresses: {relevant}. "
                           "Verify a matching service before contacting this business.")
            return draft

        prompt = self._prompt(brief)
        try:
            result = self.ai_service.generate(
                prompt, system=SYSTEM_PROMPT, max_tokens=420, purpose="outreach_draft")
        except Exception as exc:
            draft.status = "failed"
            draft.error = f"{type(exc).__name__}: {exc}"
            return draft

        draft.body = strip_em_dashes((result.text or "").strip())
        draft.subject = strip_em_dashes(self._subject(brief))
        draft.provider = result.provider
        draft.model = result.model
        draft.input_tokens = result.input_tokens
        draft.output_tokens = result.output_tokens
        draft.estimated_cost_usd = result.estimated_cost_usd
        if not draft.body:
            draft.status = "failed"
            draft.error = "Provider returned no text"
        return draft

    def _subject(self, brief: dict[str, Any]) -> str:
        """Subject lines reference the observation, never a generic pitch."""
        business = brief["business"]
        top = brief["observed_problems"][0]["code"] if brief["observed_problems"] else ""
        lines = {
            "no_online_booking": f"Booking on the {business} site",
            "no_online_ordering": f"Online ordering for {business}",
            "not_mobile_friendly": f"{business} on mobile",
            "no_lead_capture": f"Enquiries from the {business} site",
            "outdated_website": f"Quick note on the {business} site",
            "no_ssl": f"{business} site security",
            "no_website": f"A website for {business}",
            "weak_seo_basics": f"{business} in search results",
        }
        return lines.get(top, f"Quick note on {business}")

    @staticmethod
    def _prompt(brief: dict[str, Any]) -> str:
        offer = brief["recommended_service"] or brief["recommended_product"]
        problems = "\n".join(
            f"- {p['label']} (evidence: {p['evidence']}; confidence {p['confidence']})"
            for p in brief["observed_problems"][:3])
        # One proof only. Offering the model two reliably produced an email that
        # listed both, which reads as a portfolio dump rather than a relevant reference.
        proof_lines = "\n".join(
            f"- {p['name']}: {p['description']} ({p['why']})" for p in brief["proof"][:1]
        ) or "- none available, do not mention any YardLink project"

        return f"""BRIEF

Business: {brief['business']}
Industry: {brief['industry']}
Location: {brief['location']}

What we observed on their website:
{problems}

What YardLink Studio should offer them:
- {offer['name']}

Proof you may cite (pick at most one, only if it genuinely fits):
{proof_lines}

Write the email body only. Do not include a subject line. Do not mention any \
observation, capability, or project that is not listed above. Sign off as:
Kevin
YardLink Studio"""
