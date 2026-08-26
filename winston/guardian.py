"""Guardian — the validation layer between a generated draft and the send queue.

Guardian has veto power. A draft it rejects does not reach a human reviewer as an
approvable item, because the cost of a bad outreach email is not a wasted send: it is a
false claim made to a real business in YardLink's name.

Checks are deliberately **deterministic**. Asking a model whether another model's output
contains a fabrication is circular, slow, and costs money to be unreliable. Every rule
here is pattern matching against a whitelist assembled from stored evidence, which means
Guardian's verdicts are reproducible, explainable, and free.

The rules fall into four groups:

**Claim checks.** Every factual statement about the prospect must trace to a stored
observation above the confidence threshold. Every YardLink capability named must belong
to a verified catalogue entry. Numbers, statistics, and named clients are rejected
outright unless they appear in evidence, because those are the claims most likely to be
invented and most damaging when wrong.

**Commercial checks.** Only offerable entries may be pitched. A portfolio project or
internal tool named as something to buy is a hard rejection: Otonia is proof, YardLink
Eats is a consumer app, and Winston is not for sale at all.

**Safety checks.** Suppression and duplicate outreach, re-verified here even though the
send state machine checks them later, because a draft that should never have been
written should not consume a human's review time.

**Style checks.** Em dashes are a hard failure by explicit instruction. The rest of the
list exists because "I hope this email finds you well" is how a prospect knows nobody
read anything about their business.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .repository import WinstonRepository, normalize_email

# ── Style ────────────────────────────────────────────────────────────────

EM_DASH = "—"
EN_DASH = "–"

BANNED_PHRASES = (
    "i hope this email finds you well", "i hope this finds you well",
    "hope you're doing well", "hope all is well",
    "in today's digital age", "in today's fast-paced", "leverage synergies",
    "circle back", "touch base", "reach out to you today",
    "revolutionary", "game-changing", "cutting-edge solutions",
    "act now", "limited time only", "don't miss out", "last chance",
    "as a fellow", "i came across your business and was impressed",
    "your business is exactly the kind",
)

# Claims that assert a measured result Winston has never measured.
UNSUPPORTED_CLAIM_PATTERNS = (
    (r"\b\d{1,3}\s?%", "percentage claim"),
    (r"\b\d+x\b", "multiplier claim"),
    (r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:k|m|million|billion)?\b(?!\s*(?:-|to|–)\s*\$)", "monetary claim"),
    (r"\b(?:increased|boosted|grew|doubled|tripled)\s+(?:their|our|your)?\s*\w+\s+by\b", "results claim"),
    (r"\b(?:hundreds|thousands|millions)\s+of\s+(?:customers|clients|businesses|users)\b",
     "volume claim"),
    (r"\bour\s+(?:clients?|customers?)\s+(?:see|saw|report|experience)\b", "client-results claim"),
    (r"\b(?:award-winning|industry-leading|#1|number one|the best)\b", "superiority claim"),
    (r"\bguarantee(?:d|s)?\b", "guarantee"),
)

# Protected characteristics must never appear as commercial reasoning.
PROTECTED_TERMS = (
    "race", "racial", "ethnicity", "ethnic", "nationality", "immigrant",
    "religion", "religious", "muslim", "christian", "jewish", "hindu",
    "gender", "disability", "disabled",
    "black-owned", "white-owned", "asian-owned", "hispanic-owned", "latino-owned",
    "minority-owned", "women-owned",
)

# Phrasing that turns cultural context into a commercial inference.
DISCRIMINATORY_PRICING_PATTERNS = (
    r"\b(?:because|since|given)\s+(?:you|they|your\s+community)\s+(?:are|is)\b[^.]{0,40}"
    r"(?:" + "|".join(PROTECTED_TERMS) + r")",
    r"\b(?:" + "|".join(PROTECTED_TERMS) + r")\b[^.]{0,50}\b(?:discount|price|afford|budget|rate)",
    r"\b(?:discount|price|afford|budget|rate)\b[^.]{0,50}\b(?:" + "|".join(PROTECTED_TERMS) + r")",
)

# Fulfilment claims Winston cannot support. Publishing is the sharpest case: the
# Website Builder's publish seam supports download only, with cloud targets marked
# Phase 4 in PRODUCTION.md, so promising a live URL would be selling vapour.
UNSUPPORTED_FULFILMENT_PATTERNS = (
    (r"\b(?:one[- ]click|automatic(?:ally)?|instant(?:ly)?)\s+(?:publish|deploy|launch|go[- ]live)",
     "automatic publishing"),
    (r"\bpublish(?:ed|ing)?\s+(?:it\s+)?(?:to\s+)?(?:a\s+)?live\s+(?:url|site|website)",
     "live publishing"),
    (r"\b(?:we|yardlink)\s+(?:will\s+)?host\s+(?:it|your\s+site|the\s+site)", "hosting claim"),
    (r"\bunlimited\s+(?:revisions?|customi[sz]ation|changes?|pages?)", "unlimited scope"),
    (r"\b(?:guaranteed?|promise|ensure)\s+[^.]{0,30}\b(?:seo|ranking|traffic|leads?|revenue|sales|customers)",
     "guaranteed outcome"),
    (r"\b(?:rank|ranking)\s+(?:you\s+)?(?:#?1|first|top)\b", "ranking promise"),
    (r"\bin\s+(?:just\s+)?\d+\s+(?:hours?|days?|weeks?)\b", "delivery-time claim"),
)

# Phrasings that assert a deficiency. In question mode these are hard failures
# regardless of evidence, because the whole premise is that Winston does not know.
# "I noticed you don't offer booking, do you?" is an assertion wearing a question mark.
ASSERTION_PATTERNS = (
    r"\b(?:i|we)\s+(?:noticed|see|saw|found|can see)\b[^.?]{0,40}\b(?:you|your)\b"
    r"[^.?]{0,40}\b(?:don'?t|do not|doesn'?t|does not|lack|lacks|missing|no)\b",
    r"\byour\s+(?:site|website|business)\s+(?:doesn'?t|does not|lacks|is missing)\b",
    r"\byou\s+(?:don'?t|do not)\s+(?:have|offer|provide|use)\b",
    r"\byou'?re\s+(?:missing|lacking|without)\b",
    r"\bthere'?s\s+no\b[^.?]{0,30}\bon\s+your\b",
    r"\bsince\s+you\s+(?:don'?t|do not)\b",
)

DEFAULT_CONFIDENCE_FLOOR = 0.5


@dataclass
class GuardianResult:
    """Structured verdict. `approved` false means the draft cannot be queued."""
    approved: bool = True
    confidence: float = 1.0
    issues: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    claim_checks: list[dict[str, Any]] = field(default_factory=list)
    evidence_checks: list[dict[str, Any]] = field(default_factory=list)
    style_checks: list[dict[str, Any]] = field(default_factory=list)
    commercial_checks: list[dict[str, Any]] = field(default_factory=list)
    # Digest of the exact subject and body reviewed. The send path compares this to
    # what it is about to transmit, so a verdict cannot outlive an edit.
    reviewed_digest: str = ""

    def fail(self, category: str, rule: str, detail: str, excerpt: str = "") -> None:
        entry = {"rule": rule, "detail": detail, "excerpt": excerpt[:160]}
        self.issues.append({**entry, "category": category})
        getattr(self, f"{category}_checks").append({**entry, "passed": False})
        self.approved = False

    def warn(self, category: str, rule: str, detail: str, excerpt: str = "") -> None:
        entry = {"rule": rule, "detail": detail, "excerpt": excerpt[:160]}
        self.warnings.append({**entry, "category": category})
        getattr(self, f"{category}_checks").append({**entry, "passed": True, "warning": True})

    def passed(self, category: str, rule: str) -> None:
        getattr(self, f"{category}_checks").append({"rule": rule, "passed": True})

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "confidence": round(self.confidence, 3),
            "issues": self.issues,
            "warnings": self.warnings,
            "claim_checks": self.claim_checks,
            "evidence_checks": self.evidence_checks,
            "style_checks": self.style_checks,
            "commercial_checks": self.commercial_checks,
        }


class Guardian:
    """Validates drafts against stored evidence and the commercial catalogue."""

    def __init__(self, repository: WinstonRepository, catalog: Any,
                 *, confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR) -> None:
        self.repository = repository
        self.catalog = catalog
        self.confidence_floor = confidence_floor

    # ── entry point ──────────────────────────────────────────────────────

    def review(self, *, subject: str, body: str, contact: dict[str, Any],
               brief: dict[str, Any] | None = None) -> GuardianResult:
        """Validate one draft. `brief` is the Writer's structured input.

        A brief carrying ``mode: question`` is held to a different and stricter
        standard: it must actually ask something, and it may assert nothing at all.
        """
        result = GuardianResult()
        brief = brief or {}
        text = f"{subject}\n{body}"
        lowered = text.casefold()

        if brief.get("mode") == "question":
            self._check_question_mode(result, text, lowered, brief)

        self._check_style(result, text, lowered)
        self._check_unsupported_claims(result, text, lowered, brief)
        self._check_problem_claims(result, lowered, brief)
        self._check_commercial(result, lowered, brief)
        self._check_fulfilment_claims(result, text, lowered)
        self._check_protected_characteristics(result, lowered)
        self._check_safety(result, contact)

        # Confidence reflects the weakest evidence the draft leans on.
        confidences = [
            float(problem.get("confidence", 0.0))
            for problem in brief.get("observed_problems", [])
        ]
        result.confidence = round(min(confidences), 3) if confidences else 0.0
        if result.issues:
            result.confidence = 0.0
        return result

    def _check_question_mode(self, result: GuardianResult, text: str, lowered: str,
                             brief: dict[str, Any]) -> None:
        """A question-mode draft must ask, and must not answer itself.

        This is the guard that stops question mode becoming the loophole assertability
        closed. An inferred problem is one Winston could not confirm, so a draft built
        on it may not state anything about the business at all.
        """
        if "?" not in text:
            result.fail("evidence", "question_mode_asserts_nothing_asked",
                        "A question-mode draft contains no question.")

        clean = True
        for pattern in ASSERTION_PATTERNS:
            match = re.search(pattern, lowered)
            if match:
                result.fail("evidence", "question_mode_assertion",
                            "States a deficiency Winston could not confirm. In question "
                            "mode the premise is that the answer is unknown.",
                            text[max(0, match.start() - 30):match.end() + 30])
                clean = False
        if clean:
            result.passed("evidence", "question_preserves_uncertainty")

        if not brief.get("lead_investigation"):
            result.fail("commercial", "no_investigation",
                        "Question mode requires an inferred problem with a verified "
                        "offer behind it.")

    # ── style ────────────────────────────────────────────────────────────

    def _check_style(self, result: GuardianResult, text: str, lowered: str) -> None:
        if EM_DASH in text:
            index = text.index(EM_DASH)
            result.fail("style", "no_em_dash",
                        "Em dashes are prohibited in outreach.",
                        text[max(0, index - 40):index + 40])
        else:
            result.passed("style", "no_em_dash")

        if EN_DASH in text and not re.search(rf"\d\s*{EN_DASH}\s*\d", text):
            result.warn("style", "en_dash",
                        "En dash outside a numeric range reads as an em dash substitute.")

        for phrase in BANNED_PHRASES:
            if phrase in lowered:
                result.fail("style", "banned_phrase",
                            f"Contains filler that signals nobody researched the business: {phrase!r}",
                            phrase)

        words = len(text.split())
        if words > 220:
            result.fail("style", "too_long", f"Draft is {words} words; outreach must stay concise.")
        elif words > 170:
            result.warn("style", "length", f"Draft is {words} words and could be tighter.")
        if words < 40:
            result.warn("style", "too_short", f"Draft is only {words} words.")

        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if any(len(p.split()) > 90 for p in paragraphs):
            result.warn("style", "wall_of_text", "One paragraph exceeds 90 words.")

    # ── claims ───────────────────────────────────────────────────────────

    def _check_unsupported_claims(self, result: GuardianResult, text: str,
                                  lowered: str, brief: dict[str, Any]) -> None:
        """Statistics, results, and guarantees Winston has never measured."""
        allowed_numbers = set()
        pricing = brief.get("pricing") or {}
        for key in ("price_min_usd", "price_max_usd", "recommended_price_usd"):
            if pricing.get(key) is not None:
                allowed_numbers.add(str(int(float(pricing[key]))))

        clean = True
        for pattern, label in UNSUPPORTED_CLAIM_PATTERNS:
            for match in re.finditer(pattern, lowered, re.IGNORECASE):
                snippet = match.group(0)
                if any(number in snippet for number in allowed_numbers):
                    continue
                result.fail("claim", "unsupported_claim",
                            f"{label} that no stored evidence supports.",
                            text[max(0, match.start() - 40):match.end() + 40])
                clean = False
        if clean:
            result.passed("claim", "no_unsupported_claims")

        # Named clients. Winston has never recorded a client name.
        # Capitalised name after "we built ... for". The name class excludes a full stop
        # so the capture cannot run past the end of the sentence, and past-tense verbs
        # only, so "YardLink builds ordering systems for restaurants" is not a claim.
        for match in re.finditer(
                r"\b(?:We|Our team|YardLink)\s+"
                r"(?:built|made|created|developed|delivered|launched)\s+"
                r"(?:[\w,]+\s+){0,6}?for\s+([A-Z][\w'&-]*(?:\s+[A-Z][\w'&-]*)*)",
                text):
            named = match.group(1).strip()
            if named.casefold() not in {"them", "clients", "businesses"}:
                result.fail("claim", "named_client",
                            f"Claims work was done for {named!r}. Winston stores no client names.",
                            match.group(0))

    def _check_problem_claims(self, result: GuardianResult, lowered: str,
                              brief: dict[str, Any]) -> None:
        """Assertions about the prospect must trace to an observation."""
        observed = {
            str(problem.get("code", "")): float(problem.get("confidence", 0.0))
            for problem in brief.get("observed_problems", [])
        }

        # Phrases that assert a specific deficiency, mapped to the problem that licenses them.
        assertions = {
            "no_online_booking": (r"\b(?:no|without|lack|don'?t (?:have|offer)|can'?t)\b[^.]{0,40}"
                                  r"\b(?:online booking|book online|booking system|schedule online)\b"),
            "no_online_ordering": (r"\b(?:no|without|lack|don'?t (?:have|offer))\b[^.]{0,40}"
                                   r"\b(?:online order|ordering)\b"),
            "not_mobile_friendly": r"\b(?:not|isn'?t|doesn'?t)\b[^.]{0,30}\bmobile\b",
            "outdated_website": r"\b(?:outdated|dated|old|hasn'?t been updated|unmaintained)\b[^.]{0,20}\b(?:site|website)\b",
            "no_lead_capture": r"\b(?:no|without)\b[^.]{0,30}\b(?:contact form|enquiry form|inquiry form|lead capture)\b",
            "no_ssl": r"\b(?:not secure|no ssl|http only|insecure)\b",
            "no_website": r"\b(?:no website|don'?t have a website|without a website)\b",
            "weak_seo_basics": r"\b(?:seo|search visibility|search rankings?)\b[^.]{0,30}\b(?:weak|missing|poor|lacking)\b",
        }

        clean = True
        for code, pattern in assertions.items():
            match = re.search(pattern, lowered)
            if not match:
                continue
            if code not in observed:
                result.fail("evidence", "unobserved_problem",
                            f"Asserts {code.replace('_', ' ')!r} but no such observation is stored.",
                            match.group(0))
                clean = False
            elif observed[code] < self.confidence_floor:
                result.fail("evidence", "low_confidence_claim",
                            f"Asserts {code.replace('_', ' ')!r} at confidence "
                            f"{observed[code]:.2f}, below the {self.confidence_floor} floor.",
                            match.group(0))
                clean = False
            else:
                result.evidence_checks.append(
                    {"rule": "observation_supported", "passed": True, "detail": code})

        if not observed and brief.get("mode") != "question":
            result.fail("evidence", "no_evidence",
                        "No observed problems in the brief. Outreach must rest on research.")
            clean = False
        if clean and observed:
            result.passed("evidence", "all_claims_traceable")

    # ── commercial ───────────────────────────────────────────────────────

    def _check_commercial(self, result: GuardianResult, lowered: str,
                          brief: dict[str, Any]) -> None:
        """Only offerable entries may be pitched; proof may only be cited."""
        try:
            entries = self.catalog.list()
        except Exception:
            result.warn("commercial", "catalogue_unavailable", "Could not read the catalogue.")
            return

        allowed_offer = {
            (brief.get("recommended_service") or {}).get("slug"),
            (brief.get("recommended_product") or {}).get("slug"),
        } - {None}
        allowed_proof = {p.get("slug") for p in brief.get("proof", [])} | {
            p.get("slug") for p in brief.get("strategic_standing", [])} - {None}

        sell_verb = (r"\b(?:buy|purchase|sign up for|subscribe to|licence|license|"
                     r"get started with|onboard(?:ed)? (?:to|onto))\b")
        # Softer phrasing still positions the entry as the solution on offer.
        offer_phrase = (r"\b(?:could|would|can|will)\s+(?:help|work for|suit|fix|solve)\b"
                        r"|\b(?:is|would be)\s+(?:a\s+)?(?:great|good|perfect)?\s*fit\b"
                        r"|\bwe\s+(?:can|could)\s+(?:offer|set\s+you\s+up|give\s+you)\b"
                        r"|\brecommend\b")

        clean = True
        for entry in entries:
            name = (entry.get("name") or "").casefold()
            if not name or name not in lowered:
                continue
            slug = entry["slug"]

            if not entry.get("verified"):
                result.fail("commercial", "unverified_entry",
                            f"Mentions {entry['name']!r}, which is unverified and may not be "
                            "referenced in outreach.", entry["name"])
                clean = False
                continue

            index = lowered.index(name)
            window = lowered[max(0, index - 90):index + len(name) + 90]
            pitched = bool(
                re.search(sell_verb + r"[^.]{0,40}" + re.escape(name), window)
                or re.search(re.escape(name) + r"[^.]{0,30}\b(?:costs?|pricing|per month|plan)\b", window)
                or re.search(re.escape(name) + r"[^.]{0,30}(?:" + offer_phrase + r")", window)
                or re.search(r"(?:" + offer_phrase + r")[^.]{0,30}" + re.escape(name), window))

            if pitched and not entry.get("offerable_to_business"):
                reason = ("a consumer product" if entry.get("audience") == "consumer"
                          else f"classified {entry['kind']}")
                result.fail("commercial", "not_offerable",
                            f"Pitches {entry['name']!r} as something to buy, but it is {reason} "
                            "and may only be cited as proof.", window.strip()[:120])
                clean = False
            elif pitched and slug not in allowed_offer:
                result.fail("commercial", "offer_not_recommended",
                            f"Pitches {entry['name']!r}, which the Strategist did not recommend.",
                            window.strip()[:120])
                clean = False
            elif not pitched and slug not in (allowed_proof | allowed_offer):
                result.warn("commercial", "unplanned_reference",
                            f"References {entry['name']!r} without it being selected as proof.")

        if clean:
            result.passed("commercial", "offers_and_proof_valid")

        if brief.get("recommendation_status") == "no_verified_offer":
            result.fail("commercial", "no_verified_offer",
                        "No verified YardLink offer matches this prospect, so no outreach "
                        "should be generated.")

    def _check_fulfilment_claims(self, result: GuardianResult, text: str, lowered: str) -> None:
        """Block promises about delivery YardLink cannot currently keep."""
        clean = True
        for pattern, label in UNSUPPORTED_FULFILMENT_PATTERNS:
            match = re.search(pattern, lowered, re.IGNORECASE)
            if match:
                result.fail("claim", "unsupported_fulfilment",
                            f"{label} is not a verified YardLink capability.",
                            text[max(0, match.start() - 30):match.end() + 30])
                clean = False
        if clean:
            result.passed("claim", "no_unsupported_fulfilment")

    def _check_protected_characteristics(self, result: GuardianResult, lowered: str) -> None:
        clean = True
        for pattern in DISCRIMINATORY_PRICING_PATTERNS:
            match = re.search(pattern, lowered, re.IGNORECASE)
            if match:
                result.fail("commercial", "protected_characteristic",
                            "Ties a protected characteristic to commercial terms.",
                            match.group(0))
                clean = False
        for term in PROTECTED_TERMS:
            if re.search(rf"\b{re.escape(term)}\b", lowered):
                result.warn("commercial", "protected_term_present",
                            f"Mentions {term!r}. Permitted only as the business's own public "
                            "positioning, never as commercial reasoning.")
        if clean:
            result.passed("commercial", "no_protected_characteristics")

    # ── safety ───────────────────────────────────────────────────────────

    def _check_safety(self, result: GuardianResult, contact: dict[str, Any]) -> None:
        email = (contact or {}).get("email", "")
        if not email:
            result.fail("evidence", "no_recipient", "Contact has no email address.")
            return

        if self.repository.is_suppressed(email):
            result.fail("commercial", "suppressed_recipient",
                        "Recipient is suppressed and must never be contacted again.")
        else:
            result.passed("commercial", "not_suppressed")

        normalized = normalize_email(email)
        already = pending = 0
        with self.repository.read() as connection:
            # The commercial ledger may not be initialised in every context; a missing
            # table means "no prior outreach recorded", not a validation failure.
            try:
                already = connection.execute(
                    "SELECT COUNT(*) n FROM messages WHERE normalized_email=?",
                    (normalized,)).fetchone()["n"]
            except Exception:
                result.warn("commercial", "ledger_unavailable",
                            "Could not check prior outreach; the ledger is not initialised.")
            pending = connection.execute(
                """SELECT COUNT(*) n FROM drafts d JOIN contacts c ON c.id=d.contact_id
                   WHERE c.normalized_email=? AND d.stage IN ('draft','reviewed','approved','queued')""",
                (normalized,)).fetchone()["n"]

        if already:
            result.fail("commercial", "duplicate_outreach",
                        f"This address has already received {already} message(s).")
        elif pending > 1:
            result.fail("commercial", "duplicate_draft",
                        f"{pending} drafts already pending for this address.")
        else:
            result.passed("commercial", "no_duplicate")
