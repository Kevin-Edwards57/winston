"""Prospect → YardLink matching.

Three questions, answered separately because they have different answers:

1. **What does this business need?** — derived from observed signals, never assumed.
2. **What can YardLink genuinely provide?** — the verified, sellable catalogue.
3. **What proves we can build it?** — portfolio and internal work, cited as evidence
   but never offered for sale.

The recommendation is the intersection. Where the intersection is empty, the honest
answer is "no product fits — recommend a service", and where even that is empty the
honest answer is "nothing to recommend". Forcing a product onto a prospect it does not
suit is how outreach becomes spam.

Scores are reported separately and never collapsed into one unexplained number.
`PRODUCT_FIT` being zero while `SERVICE_FIT` is high is meaningful information: it means
YardLink should pitch custom work rather than a packaged product.

**Problems are only derived from signals that were actually observed.** If
`mobile_responsive` is unknown — because the page was client-rendered, or the site could
not be fetched — Winston does not conclude the site is not mobile-friendly. Unknown
reduces confidence; it never manufactures a problem to sell against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .catalog import Catalog
from .repository import WinstonRepository
from .signals import SignalStore

# ── Problem taxonomy ─────────────────────────────────────────────────────
# Each problem is derived from a specific observed signal. Severity is how much
# the problem plausibly costs the business, on a 0-1 scale.

BOOKING_INDUSTRIES = {
    "barbershop", "hair salon", "nail salon", "dentist", "gym", "photographer",
    "daycare", "tutoring center", "auto repair", "cleaning service", "spa",
}
# At most two pieces of evidence in an email. More reads as a portfolio dump.
MAX_PROOF = 2

ORDERING_INDUSTRIES = {
    "restaurant", "jamaican restaurant", "caribbean restaurant", "catering",
    "catering company", "bakery", "cafe",
}


@dataclass
class Problem:
    """An observed business problem, with the evidence that produced it."""
    code: str
    label: str
    severity: float
    evidence: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "severity": round(self.severity, 2),
                "evidence": self.evidence, "confidence": round(self.confidence, 2)}


@dataclass
class FitResult:
    """A complete, explainable commercial assessment of one prospect."""
    contact_id: str
    problems: list[Problem] = field(default_factory=list)
    product_fit: float = 0.0
    service_fit: float = 0.0
    portfolio_relevance: float = 0.0
    problem_severity: float = 0.0
    commercial_opportunity: float = 0.0
    confidence: float = 0.0
    recommended_product: dict[str, Any] | None = None
    recommended_service: dict[str, Any] | None = None
    proof: list[dict[str, Any]] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    strategic_standing: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "scores": {
                "PRODUCT_FIT": round(self.product_fit, 3),
                "SERVICE_FIT": round(self.service_fit, 3),
                "PORTFOLIO_RELEVANCE": round(self.portfolio_relevance, 3),
                "PROBLEM_SEVERITY": round(self.problem_severity, 3),
                "COMMERCIAL_OPPORTUNITY": round(self.commercial_opportunity, 3),
                "CONFIDENCE": round(self.confidence, 3),
            },
            "observed_problems": [p.as_dict() for p in self.problems],
            "recommended_product": self.recommended_product,
            "recommended_service": self.recommended_service,
            "proof": self.proof,
            "strategic_standing": self.strategic_standing,
            "alternatives": self.alternatives,
            "reasons": self.reasons,
            "blockers": self.blockers,
        }


def derive_problems(signals: dict[str, dict[str, Any]], *, industry: str = "",
                    has_website: bool = True) -> list[Problem]:
    """Read observed problems out of stored signals. Unknown never becomes a problem."""
    problems: list[Problem] = []
    industry = (industry or "").casefold().strip()

    def observed(name: str) -> dict[str, Any] | None:
        return signals.get(name)

    if not has_website:
        problems.append(Problem(
            "no_website", "No website found", 0.9,
            "no website recorded for this business", 0.9))
        return problems

    if not signals:
        # Researched nothing, or research failed. No problems can be claimed.
        return problems

    mobile = observed("mobile_responsive")
    if mobile and mobile["value"] is False:
        problems.append(Problem(
            "not_mobile_friendly", "Site does not adapt to phones", 0.85,
            mobile["evidence"], mobile["confidence"]))

    ssl = observed("has_ssl")
    if ssl and ssl["value"] is False:
        problems.append(Problem(
            "no_ssl", "Site is served over plain HTTP", 0.7,
            ssl["evidence"], ssl["confidence"]))

    stale = observed("years_since_copyright_update")
    if stale and isinstance(stale["value"], (int, float)) and stale["value"] >= 3:
        problems.append(Problem(
            "outdated_website", f"Site appears unmaintained for ~{int(stale['value'])} years",
            min(0.4 + 0.1 * float(stale["value"]), 0.8), stale["evidence"], stale["confidence"]))

    form = observed("has_contact_form")
    if form and form["value"] is False:
        problems.append(Problem(
            "no_lead_capture", "No enquiry form — visitors cannot leave details", 0.75,
            form["evidence"], form["confidence"]))

    # Only a confirmed absence is a commercial problem. "Not detected" means Winston
    # could not see analytics, which is a limit of the detector rather than a fact
    # about the business. Selling against it produced three false opportunities in
    # the 50-prospect experiment, one of them to a Shopify store.
    measurement = observed("measurement_state")
    if measurement and measurement["value"] == "confirmed_absence":
        problems.append(Problem(
            "no_measurement", "No analytics found, so marketing spend is unmeasurable",
            0.5, measurement["evidence"], measurement["confidence"]))

    # Capability gaps are industry-conditional. A restaurant without online ordering
    # is a real problem; a photographer without it is not.
    if industry in BOOKING_INDUSTRIES and "online_booking" not in signals:
        researched = bool(signals)
        problems.append(Problem(
            "no_online_booking", "No online booking detected for an appointment business",
            0.8, "no booking platform found on the pages researched",
            0.55 if researched else 0.2))

    if industry in ORDERING_INDUSTRIES and "online_ordering" not in signals:
        problems.append(Problem(
            "no_online_ordering", "No online ordering detected for a food business",
            0.8, "no ordering platform found on the pages researched", 0.55))

    seo_gaps = [name for name in ("has_title", "has_meta_description", "has_h1")
                if (observed(name) or {}).get("value") is False]
    if seo_gaps:
        problems.append(Problem(
            "weak_seo_basics", "Missing basic on-page SEO elements", 0.45,
            f"absent: {', '.join(seo_gaps)}", 0.85))

    return problems


def _text_pool(entry: dict[str, Any]) -> set[str]:
    """Everything an entry claims to solve or serve, lowercased for matching."""
    parts: list[str] = []
    for field_name in ("problems_solved", "capabilities", "industries"):
        parts.extend(str(v) for v in (entry.get(field_name) or []))
    parts.append(entry.get("description", ""))
    return {p.casefold().strip() for p in parts if p}


def _match_score(entry: dict[str, Any], problems: list[Problem], industry: str) -> tuple[float, list[str]]:
    """How well one catalogue entry addresses this prospect's observed problems."""
    if not problems:
        return 0.0, []
    pool = _text_pool(entry)
    if not pool:
        return 0.0, []

    reasons: list[str] = []
    matched_weight = 0.0
    total_weight = sum(p.severity for p in problems) or 1.0

    for problem in problems:
        tokens = {problem.code.replace("_", " "), problem.label.casefold()}
        hit = any(any(token in candidate or candidate in token for candidate in pool)
                  for token in tokens if token)
        if hit:
            matched_weight += problem.severity * problem.confidence
            reasons.append(f"addresses {problem.label.lower()}")

    industry_bonus = 0.0
    if industry and any(industry in candidate for candidate in pool):
        industry_bonus = 0.15
        reasons.append(f"targets {industry}")

    return min(matched_weight / total_weight + industry_bonus, 1.0), reasons


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
            # Carried so the guarantee stays checkable: evidence is cited, never sold.
            "sellable": entry.get("sellable", False),
            "offerable_to_business": entry.get("offerable_to_business", False),
            "relevance": score,
            "why": reasons[0] if reasons else "related capability",
            "reasons": reasons,
        })

    ranked.sort(key=lambda r: -r["relevance"])
    return ranked[:limit]


class FitEngine:
    """Matches a researched prospect against what YardLink can genuinely provide."""

    def __init__(self, repository: WinstonRepository, catalog: Catalog,
                 signal_store: SignalStore) -> None:
        self.repository = repository
        self.catalog = catalog
        self.signals = signal_store

    def assess(self, contact_id: str) -> FitResult:
        with self.repository.read() as connection:
            contact = connection.execute(
                "SELECT id,name,business_type,website FROM contacts WHERE id=?",
                (contact_id,)).fetchone()
        if contact is None:
            raise KeyError(f"Unknown contact {contact_id}")

        industry = (contact["business_type"] or "").casefold().strip()
        signals = self.signals.for_contact(contact_id)
        result = FitResult(contact_id=contact_id)

        # ── 1. What does this business need? ──
        result.problems = derive_problems(
            signals, industry=industry, has_website=bool(contact["website"]))
        if result.problems:
            result.problem_severity = max(p.severity for p in result.problems)

        researched = self.signals.last_researched(contact_id)
        if not researched:
            result.blockers.append(
                "Not researched yet — no evidence to reason from. Run POST /research/<id>.")
        if not signals:
            result.confidence = 0.0
        else:
            # Confidence tracks how much evidence supports the problems found.
            observed_confidences = [p.confidence for p in result.problems] or [0.0]
            coverage = min(len(signals) / 10.0, 1.0)
            result.confidence = round(
                (sum(observed_confidences) / len(observed_confidences)) * coverage, 3)

        # ── 2. What can YardLink genuinely provide? ──
        # Offers are drawn only from business-facing entries. A verified, shipping
        # consumer product is genuinely for sale -- just not to a barbershop -- so it is
        # excluded here and surfaces as proof instead.
        offerable = self.catalog.offerable()
        if not offerable:
            result.blockers.append(
                "No verified business-facing catalogue entries. Winston will not "
                "recommend unverified products. Fill in and verify entries via /catalog.")

        products = [e for e in offerable if e["kind"] == "PRODUCT"]
        services = [e for e in offerable if e["kind"] == "SERVICE"]

        scored_products = sorted(
            ((e, *_match_score(e, result.problems, industry)) for e in products),
            key=lambda row: row[1], reverse=True)
        scored_services = sorted(
            ((e, *_match_score(e, result.problems, industry)) for e in services),
            key=lambda row: row[1], reverse=True)

        if scored_products and scored_products[0][1] > 0:
            entry, score, reasons = scored_products[0]
            result.product_fit = score
            result.recommended_product = self._summarise(entry, score)
            result.reasons.extend(reasons)

        if scored_services and scored_services[0][1] > 0:
            entry, score, reasons = scored_services[0]
            result.service_fit = score
            result.recommended_service = self._summarise(entry, score)
            result.reasons.extend(r for r in reasons if r not in result.reasons)

        # No product fits is a legitimate outcome, not a failure to be papered over.
        if not result.recommended_product and result.recommended_service:
            result.reasons.append(
                "No packaged YardLink product matches these problems; a service is the "
                "appropriate offer")

        result.alternatives = [
            self._summarise(entry, score)
            for entry, score, _ in (scored_products + scored_services)[:5]
            if score > 0 and (result.recommended_product or {}).get("slug") != entry["slug"]
               and (result.recommended_service or {}).get("slug") != entry["slug"]
        ]

        # ── 3. What proves we can build it? ──
        # Standing in this industry from work that is NOT sold to it. YardLink Eats is
        # a consumer app, but it demonstrates restaurant-focused engineering and gives
        # YardLink an existing relationship in that market -- credibility to cite, never
        # an offer to make.
        for entry in self.catalog.strategic_proof_for(industry):
            result.strategic_standing.append({
                "slug": entry["slug"], "name": entry["name"], "kind": entry["kind"],
                "audience": entry.get("audience", "business"),
                "offerable_to_business": entry["offerable_to_business"],
                "why": f"demonstrates experience in the {industry} market",
            })
            result.reasons.append(
                f"{entry['name']} gives YardLink standing with {industry} businesses "
                "(cite as proof, do not offer it)")

        chosen = result.recommended_product or result.recommended_service
        if chosen:
            # Ranked here rather than only in the Writer. Two proof paths meant the
            # prospect view and the generated draft could cite different evidence,
            # and the assessment omitted the relevance score entirely.
            full = self.catalog.get(chosen["slug"]) or chosen
            result.proof = select_proof(self.catalog, full, industry,
                                        {p.code for p in result.problems})
            result.portfolio_relevance = min(
                (len(result.proof) + len(result.strategic_standing)) * 0.4, 1.0)
            if not result.proof:
                result.blockers.append(
                    f"No portfolio evidence linked to {chosen['slug']} — outreach cannot "
                    "cite proof. Link one via /catalog/link.")

        # ── Commercial opportunity ──
        best_fit = max(result.product_fit, result.service_fit)
        result.commercial_opportunity = round(
            best_fit * result.problem_severity * max(result.confidence, 0.05), 3)

        return result

    @staticmethod
    def _summarise(entry: dict[str, Any], score: float) -> dict[str, Any]:
        return {
            "slug": entry["slug"], "name": entry["name"], "kind": entry["kind"],
            "status": entry["status"], "fit": round(score, 3),
            "price_min_usd": entry.get("price_min_usd"),
            "price_max_usd": entry.get("price_max_usd"),
            "pricing_model": entry.get("pricing_model", ""),
            "effort_hours_min": entry.get("effort_hours_min"),
            "effort_hours_max": entry.get("effort_hours_max"),
        }
