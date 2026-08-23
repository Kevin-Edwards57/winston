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


def score_proof(entry: dict[str, Any], *, industry: str, offer: dict[str, Any],
                problem_codes: set[str], explicitly_linked: bool = False) -> tuple[float, list[str]]:
    """Relevance of one piece of evidence to this specific opportunity.

    Deliberately general. Nothing here privileges a particular project; YardLink Eats
    outranks Anansi for a restaurant because it carries that industry in its strategic
    segments, not because it is named in a special case. A data-engineering prospect
    would invert the ordering through the same arithmetic.
    """
    if not entry.get("verified"):
        return 0.0, ["unverified, cannot be cited"]

    score, reasons = 0.0, []
    # A curated `proves` link is a human asserting relevance. Without this baseline a
    # website offer scored zero proof, because no portfolio entry happens to contain
    # the literal word "website" even though three were deliberately linked to it.
    if explicitly_linked:
        score += 0.3
        reasons.append("linked as proof for this offer")

    segments = {str(s).casefold() for s in (entry.get("strategic_segments") or [])}
    industries = {str(s).casefold() for s in (entry.get("industries") or [])}
    capabilities = {str(c).casefold() for c in (entry.get("capabilities") or [])}
    blob = " ".join(capabilities | {str(entry.get("description", "")).casefold()})

    # Standing in the prospect's own industry is the strongest signal a prospect
    # can personally recognise.
    if industry and industry in segments:
        score += 0.5
        reasons.append(f"already active in the {industry} market")
    elif industry and industry in industries:
        score += 0.35
        reasons.append(f"built for {industry} businesses")
    elif industry:
        head = industry.split()[-1] if industry.split() else ""
        if head and any(head in seg for seg in segments | industries):
            score += 0.2
            reasons.append(f"adjacent to the {industry} market")

    # Capability overlap with what is actually being offered.
    offer_terms = {str(t).casefold() for t in (offer.get("capabilities") or [])}
    offer_terms |= {w for w in str(offer.get("name", "")).casefold().split() if len(w) > 3}
    overlap = [term for term in offer_terms if term and term in blob]
    if overlap:
        score += min(0.3, 0.1 * len(overlap))
        reasons.append(f"demonstrates {overlap[0]}")

    # Evidence that speaks to the observed problem domain.
    problem_words = {word for code in problem_codes for word in code.split("_") if len(word) > 3}
    if any(word in blob for word in problem_words):
        score += 0.15
        reasons.append("addresses the same problem area")

    return round(min(score, 1.0), 3), reasons


def select_proof(catalog: Catalog, offer: dict[str, Any], industry: str,
                 problem_codes: set[str] | None = None,
                 limit: int = MAX_PROOF) -> list[dict[str, Any]]:
    """Rank every citable entry by relevance and return the strongest.

    An earlier version appended linked proof in database order, which put Anansi
    ahead of YardLink Eats for a restaurant purely because of insertion sequence.
    """
    offer_slug = offer.get("slug", "")
    problem_codes = problem_codes or set()

    candidates: dict[str, dict[str, Any]] = {}
    for entry in catalog.proof_for(offer_slug):
        candidates[entry["slug"]] = entry
    for entry in catalog.strategic_proof_for(industry):
        candidates.setdefault(entry["slug"], entry)

    linked = {e["slug"] for e in catalog.proof_for(offer_slug)}
    ranked = []
    for entry in candidates.values():
        score, reasons = score_proof(entry, industry=industry, offer=offer,
                                     problem_codes=problem_codes,
                                     explicitly_linked=entry["slug"] in linked)
        if score <= 0:
            continue
        ranked.append({
            "slug": entry["slug"], "name": entry["name"],
            "description": entry.get("description", ""),
            "url": entry.get("url", ""), "kind": entry["kind"],
            "relevance": score,
            "why": reasons[0] if reasons else "related capability",
            "reasons": reasons,
        })

    ranked.sort(key=lambda r: -r["relevance"])
    return ranked[:limit]


def rank_problems(problems: list[Any], offer: dict[str, Any]) -> list[Any]:
    """Order observations by relevance to the offer being made.

    The opening line of the email is whichever problem lands first, so an ordering
    offer that opened by discussing mobile layout was not a wording issue. It was
    this list being unsorted.
    """
    solved = {str(p).casefold() for p in (offer.get("problems_solved") or [])}

    def relevance(problem: Any) -> tuple[float, float, float]:
        code_text = problem.code.replace("_", " ").casefold()
        label_text = problem.label.casefold()
        direct = any(code_text == s or code_text in s or s in code_text for s in solved)
        loose = any(word in label_text for s in solved for word in s.split() if len(word) > 3)
        addressed = 2.0 if direct else (1.0 if loose else 0.0)
        return (-addressed, -problem.severity * problem.confidence, -problem.confidence)

    return sorted(problems, key=relevance)


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

        # Problems are ordered by how directly the chosen offer addresses them, so the
        # email opens on the problem the offer actually solves.
        if offer:
            full_offer = self.catalog.get(offer["slug"]) or offer
            usable = rank_problems(usable, full_offer)
        else:
            full_offer = {}
            usable.sort(key=lambda p: -(p.severity * p.confidence))

        intent = ""
        lead = usable[0].code if usable else ""
        for code, label in INTENT_BY_PROBLEM:
            if code == lead:
                intent = label
                break

        proof = select_proof(self.catalog, full_offer, industry,
                             {p.code for p in usable}) if offer else []

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
