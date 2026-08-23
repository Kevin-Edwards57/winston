"""Pricing — scope and effort in, an explainable band out.

A price Winston cannot justify is worse than no price, so this engine refuses more
often than it quotes. It needs a rate card and an effort estimate for the service
being sold; without either it returns ``no_pricing_basis`` and says which input is
missing. Guessing $1,200 because that sounds like a website would be inventing a
commercial commitment YardLink then has to honour.

**Protected characteristics cannot reach this engine.** Not as a policy someone must
remember, but structurally: every input passes through an allowlist, and a feature whose
name or value matches a protected term raises :class:`ProtectedCharacteristicError`
before any arithmetic runs. Race, ethnicity, nationality, religion, sex, and disability
are not variables that were left out; they are variables the engine cannot physically
accept. ``tests/test_pricing.py`` fails the build if that stops being true.

The distinction that matters: cultural context may shape *how* Winston writes to a
business. It may never shape *what* the business is charged.

Every band carries its arithmetic. A price of $1,400 arrives with the effort estimate
it came from, each multiplier that moved it, and the evidence behind each multiplier,
so a human can disagree with a specific step rather than with a number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .repository import WinstonRepository

# ── The allowlist ────────────────────────────────────────────────────────
# Nothing outside this set may influence a price. Adding an entry is a deliberate
# commercial decision, not an implementation detail.

ALLOWED_PRICING_VARIABLES = frozenset({
    "effort_hours",            # estimated build hours for the service
    "hourly_rate",             # YardLink's configured rate
    "scope_items",             # discrete deliverables observed as needed
    "integrations",            # third-party systems to connect
    "locations",               # number of business locations to support
    "page_count",              # pages in scope
    "complexity",              # technical complexity tier
    "urgency",                 # requested timeline pressure
    "maintenance",             # ongoing support burden
    "existing_platform",       # what they already run on
    "customization_level",     # bespoke versus templated
    "service_tier",            # catalogue tier
    "strategic_account",       # commercial relationship value
})

# Terms that must never appear as a pricing input, in a key or a value.
# Split deliberately: short words like "age" and "black" are matched whole, because
# a prefix match would reject "agency" and "blackout". Stems are matched as prefixes
# because "ethnic" must also catch "ethnicity".
PROTECTED_WORDS = (
    "race", "racial", "nationality", "sex", "gender", "male", "female",
    "age", "elderly", "young", "immigrant", "visa", "citizenship",
    "black", "white", "asian", "hispanic", "latino", "minority",
    "muslim", "christian", "jewish", "hindu", "buddhist",
    "caribbean", "jamaican", "guyanese", "haitian", "trinidadian",
    "surname", "handicap",
)
PROTECTED_STEMS = (
    "ethnic",          # ethnic, ethnicity
    "religio",         # religion, religious
    "disabil", "disabl",
    "pregnan",         # pregnant, pregnancy
    "national origin",
    "neighborhood_demographic", "zip_demographic", "language_spoken",
)

PROTECTED_TERMS = PROTECTED_WORDS + PROTECTED_STEMS

_PROTECTED_PATTERN = re.compile(
    "|".join([rf"\b{re.escape(w)}\b" for w in PROTECTED_WORDS]
             + [rf"\b{re.escape(s)}" for s in PROTECTED_STEMS]),
    re.IGNORECASE,
)

# Discounts require a commercial reason. The percentages are ceilings, not defaults.
DISCOUNT_REASONS = {
    "first_client": ("First client in a new service line", 0.20),
    "case_study": ("Client agrees to a public case study", 0.15),
    "bundle": ("Two or more services purchased together", 0.15),
    "annual_commitment": ("Annual commitment rather than monthly", 0.15),
    "prepaid": ("Paid in full up front", 0.10),
    "referral": ("Came through a referral", 0.10),
    "limited_scope": ("Reduced scope versus the standard engagement", 0.25),
    "strategic_account": ("Strategic account with expansion potential", 0.20),
    "volume": ("Multiple sites or locations in one engagement", 0.20),
    "nonprofit": ("Registered nonprofit", 0.20),
}

DEFAULT_MIN_MARGIN = 0.35        # over delivery cost
DEFAULT_TARGET_UPLIFT = 1.25     # target sits above floor
DEFAULT_PREMIUM_UPLIFT = 1.35    # premium sits above target


class ProtectedCharacteristicError(ValueError):
    """Raised when a protected characteristic is offered as a pricing input."""


class DisallowedPricingVariable(ValueError):
    """Raised when an input is not on the commercial allowlist."""


class NoPricingBasis(RuntimeError):
    """Raised when the inputs needed to justify a price do not exist."""


@dataclass
class Adjustment:
    """One multiplier applied to the estimate, and why."""
    factor: str
    multiplier: float
    reason: str
    evidence: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"factor": self.factor, "multiplier": round(self.multiplier, 3),
                "reason": self.reason, "evidence": self.evidence}


@dataclass
class PriceBand:
    """An explainable price range."""
    floor_usd: float
    target_usd: float
    premium_usd: float
    currency: str = "USD"
    confidence: float = 0.0
    effort_hours: float = 0.0
    hourly_rate: float = 0.0
    delivery_cost_usd: float = 0.0
    margin_at_target: float = 0.0
    adjustments: list[Adjustment] = field(default_factory=list)
    scope_assumptions: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    discount: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "floor_usd": round(self.floor_usd, 2),
            "target_usd": round(self.target_usd, 2),
            "premium_usd": round(self.premium_usd, 2),
            "currency": self.currency,
            "confidence": round(self.confidence, 3),
            "effort_hours": round(self.effort_hours, 1),
            "hourly_rate": self.hourly_rate,
            "delivery_cost_usd": round(self.delivery_cost_usd, 2),
            "margin_at_target": round(self.margin_at_target, 3),
            "adjustments": [a.as_dict() for a in self.adjustments],
            "scope_assumptions": self.scope_assumptions,
            "rationale": self.rationale,
            "discount": self.discount,
        }


def assert_no_protected_characteristics(features: dict[str, Any]) -> None:
    """Reject protected characteristics before any arithmetic runs."""
    for key, value in features.items():
        match = _PROTECTED_PATTERN.search(f"{key} {value}")
        if match:
            raise ProtectedCharacteristicError(
                f"{match.group(0)!r} cannot influence price. Found in {key!r}. Cultural "
                "context may shape how Winston writes to a business, never what it charges.")


def validate_pricing_inputs(features: dict[str, Any]) -> dict[str, Any]:
    """Every input must be on the allowlist and free of protected characteristics."""
    assert_no_protected_characteristics(features)
    unknown = set(features) - ALLOWED_PRICING_VARIABLES
    if unknown:
        raise DisallowedPricingVariable(
            f"not commercial pricing variables: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_PRICING_VARIABLES)}")
    return features


class PricingEngine:
    """Produces price bands from scope, effort, and a configured rate card."""

    def __init__(self, repository: WinstonRepository, catalog: Any) -> None:
        self.repository = repository
        self.catalog = catalog

    # ── configuration ────────────────────────────────────────────────────

    def rate_card(self) -> dict[str, Any]:
        """Operator-configured commercial settings. No defaults for the rate itself."""
        return {
            "hourly_rate_usd": self.repository.get_setting("pricing_hourly_rate_usd"),
            "min_margin": self.repository.get_setting("pricing_min_margin", DEFAULT_MIN_MARGIN),
            "target_uplift": self.repository.get_setting("pricing_target_uplift", DEFAULT_TARGET_UPLIFT),
            "premium_uplift": self.repository.get_setting("pricing_premium_uplift", DEFAULT_PREMIUM_UPLIFT),
        }

    def configure(self, *, hourly_rate_usd: float | None = None,
                  min_margin: float | None = None) -> dict[str, Any]:
        if hourly_rate_usd is not None:
            if hourly_rate_usd <= 0:
                raise ValueError("hourly_rate_usd must be positive")
            self.repository.set_setting("pricing_hourly_rate_usd", float(hourly_rate_usd))
        if min_margin is not None:
            if not 0 <= min_margin < 1:
                raise ValueError("min_margin must be between 0 and 1")
            self.repository.set_setting("pricing_min_margin", float(min_margin))
        return self.rate_card()

    def readiness(self) -> dict[str, Any]:
        """What is missing before Winston can quote anything."""
        card = self.rate_card()
        missing = []
        if not card["hourly_rate_usd"]:
            missing.append("pricing_hourly_rate_usd is not configured")

        priced = unpriced = 0
        for entry in self.catalog.list(kind="SERVICE"):
            if entry.get("effort_hours_min") and entry.get("effort_hours_max"):
                priced += 1
            else:
                unpriced += 1
        if unpriced:
            missing.append(f"{unpriced} service(s) have no effort estimate")

        return {"rate_card": card, "services_with_effort": priced,
                "services_without_effort": unpriced, "missing": missing,
                "can_quote": not missing}

    # ── scope ────────────────────────────────────────────────────────────

    @staticmethod
    def derive_scope(problems: list[dict[str, Any]], signals: dict[str, dict[str, Any]],
                     offer: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Read commercial scope out of observed evidence only."""
        assumptions: list[str] = []
        codes = {p.get("code") for p in problems}

        scope_items = len([c for c in codes if c])
        assumptions.append(f"{scope_items} observed problem(s) in scope")

        # An existing platform changes migration effort. This is technical, not demographic.
        existing = (signals.get("cms") or {}).get("value") or ""
        if existing:
            assumptions.append(f"existing platform: {existing}")

        integrations = 0
        if "no_online_booking" in codes:
            integrations += 1
            assumptions.append("booking system integration required")
        if "no_online_ordering" in codes:
            integrations += 1
            assumptions.append("ordering system integration required")
        if "no_measurement" in codes:
            integrations += 1
            assumptions.append("analytics setup required")

        complexity = "standard"
        if integrations >= 2 or scope_items >= 5:
            complexity = "high"
        elif scope_items <= 2 and not integrations:
            complexity = "low"
        assumptions.append(f"complexity assessed as {complexity}")

        features = {
            "scope_items": scope_items,
            "integrations": integrations,
            "complexity": complexity,
            "existing_platform": existing or "none observed",
            "service_tier": offer.get("name", ""),
        }
        return validate_pricing_inputs(features), assumptions

    # ── quoting ──────────────────────────────────────────────────────────

    def quote(self, *, offer: dict[str, Any], problems: list[dict[str, Any]],
              signals: dict[str, dict[str, Any]] | None = None,
              evidence_confidence: float = 0.0) -> PriceBand:
        """Produce a band, or refuse and say what is missing."""
        card = self.rate_card()
        rate = card["hourly_rate_usd"]
        if not rate:
            raise NoPricingBasis(
                "No hourly rate configured. Set it with POST /pricing/configure before "
                "Winston can quote anything.")

        low, high = offer.get("effort_hours_min"), offer.get("effort_hours_max")
        if not low or not high:
            raise NoPricingBasis(
                f"{offer.get('name', 'This service')} has no effort estimate. Add "
                "effort_hours_min and effort_hours_max to the catalogue entry.")

        features, assumptions = self.derive_scope(problems, signals or {}, offer)
        base_hours = (float(low) + float(high)) / 2
        adjustments: list[Adjustment] = []

        complexity_multipliers = {"low": 0.85, "standard": 1.0, "high": 1.25}
        multiplier = complexity_multipliers[features["complexity"]]
        if multiplier != 1.0:
            adjustments.append(Adjustment(
                "complexity", multiplier,
                f"complexity assessed as {features['complexity']}",
                f"{features['scope_items']} observed problems, "
                f"{features['integrations']} integration(s)"))

        if features["integrations"]:
            integration_multiplier = 1 + 0.12 * features["integrations"]
            adjustments.append(Adjustment(
                "integrations", integration_multiplier,
                f"{features['integrations']} third-party integration(s) in scope",
                "derived from observed capability gaps"))
            multiplier *= integration_multiplier

        hours = base_hours * multiplier
        cost = hours * float(rate)
        floor = cost * (1 + float(card["min_margin"]))
        target = floor * float(card["target_uplift"])
        premium = target * float(card["premium_uplift"])

        # Confidence tracks the evidence the scope was read from, not the arithmetic.
        historical = self._comparable_count(offer.get("slug", ""))
        confidence = min(0.4 + evidence_confidence * 0.4 + min(historical, 5) * 0.04, 0.95)

        rationale = [
            f"Base effort {base_hours:.0f}h from the catalogue entry for {offer.get('name')}",
            f"Adjusted to {hours:.0f}h by {multiplier:.2f}x from observed scope",
            f"Delivery cost {hours:.0f}h x ${rate:.0f}/h = ${cost:,.0f}",
            f"Floor holds the configured {float(card['min_margin']):.0%} minimum margin",
        ]
        if historical:
            rationale.append(f"{historical} comparable past engagement(s) informed confidence")
        else:
            rationale.append("No comparable past engagements yet, so confidence is capped")

        return PriceBand(
            floor_usd=floor, target_usd=target, premium_usd=premium,
            confidence=confidence, effort_hours=hours, hourly_rate=float(rate),
            delivery_cost_usd=cost,
            margin_at_target=(target - cost) / target if target else 0.0,
            adjustments=adjustments, scope_assumptions=assumptions, rationale=rationale)

    def _comparable_count(self, offer_slug: str) -> int:
        """How many past engagements exist for this service. Currently zero for all."""
        if not offer_slug:
            return 0
        with self.repository.read() as connection:
            try:
                return connection.execute(
                    """SELECT COUNT(*) n FROM deals d JOIN proposals p ON p.id = d.proposal_id
                       WHERE d.status='won' AND p.offer_summary LIKE ?""",
                    (f"%{offer_slug}%",)).fetchone()["n"]
            except Exception:
                return 0

    # ── discounts ────────────────────────────────────────────────────────

    def apply_discount(self, band: PriceBand, reason_code: str,
                       percent: float) -> PriceBand:
        """Discount against the target, never below the margin floor."""
        if reason_code not in DISCOUNT_REASONS:
            raise ValueError(
                f"{reason_code!r} is not a recognised commercial discount reason. "
                f"Valid: {sorted(DISCOUNT_REASONS)}")
        label, ceiling = DISCOUNT_REASONS[reason_code]
        if percent <= 0 or percent > 1:
            raise ValueError("percent must be between 0 and 1")
        if percent > ceiling:
            raise ValueError(
                f"{percent:.0%} exceeds the {ceiling:.0%} ceiling for {reason_code!r}. "
                "A larger discount needs explicit human approval.")

        discounted = band.target_usd * (1 - percent)
        if discounted < band.floor_usd:
            raise ValueError(
                f"${discounted:,.0f} falls below the ${band.floor_usd:,.0f} margin floor. "
                "Reduce the discount or reduce the scope.")

        band.discount = {
            "reason_code": reason_code, "reason": label,
            "percent": round(percent, 3),
            "discounted_usd": round(discounted, 2),
            "margin_after": round((discounted - band.delivery_cost_usd) / discounted, 3),
        }
        band.rationale.append(f"Discount {percent:.0%} applied: {label}")
        return band
