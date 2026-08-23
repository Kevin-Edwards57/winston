"""Provider registry and routing — maximum intelligence per dollar.

Winston's inference bill should be near zero. A local 3B model classifying a reply
costs nothing and takes a second; sending that same classification to Claude costs money
to be no more correct. So routing is by task difficulty, and escalation is a decision
rather than a default.

    LIGHT     classification, extraction, parsing        -> llama3.2:3b
    MEDIUM    audits, drafting, synthesis                -> qwen3:8b
    HEAVY     ambiguous reasoning, strategy              -> qwen3:8b, then cloud
    CRITICAL  customer-facing commercial decisions       -> strongest configured

None of that is hardcoded. The policy lives in settings and is editable, because the
right tier for a task is a commercial judgement that will change as models change.

Two rules hold regardless of policy:

**No silent paid escalation.** A paid provider is reachable only when zero-cost mode is
off and that provider is explicitly enabled. A free tier failing does not authorise
spending money.

**A stronger model does not bypass Guardian.** Routing decides who writes the text. It
has no influence over whether the text is allowed to reach a prospect.

Provider health here is measured, not declared. `provider_usage` already records every
call, so success rate, latency, and cost per task come from what actually happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .costs import BudgetGuard
from .repository import WinstonRepository, utc_now


class TaskClass(str, Enum):
    """How much reasoning a task genuinely needs."""
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    CRITICAL = "critical"


# What each purpose actually requires. Purposes absent here default to MEDIUM.
TASK_CLASSIFICATION: dict[str, TaskClass] = {
    "reply_classification": TaskClass.LIGHT,
    "email_extraction": TaskClass.LIGHT,
    "industry_classification": TaskClass.LIGHT,
    "structured_parse": TaskClass.LIGHT,
    "summarize_short": TaskClass.LIGHT,

    "outreach_draft": TaskClass.MEDIUM,
    "email_draft": TaskClass.MEDIUM,
    "website_analysis": TaskClass.MEDIUM,
    "research_synthesis": TaskClass.MEDIUM,
    "prospect_summary": TaskClass.MEDIUM,

    "commercial_strategy": TaskClass.HEAVY,
    "proposal_reasoning": TaskClass.HEAVY,
    "multi_source_synthesis": TaskClass.HEAVY,
    "objection_analysis": TaskClass.HEAVY,

    "pricing_narrative": TaskClass.CRITICAL,
    "proposal_generation": TaskClass.CRITICAL,
}

# Default preference order per class. Editable at runtime.
DEFAULT_ROUTING_POLICY: dict[str, list[str]] = {
    TaskClass.LIGHT.value: ["ollama:llama3.2:3b", "ollama:qwen3:8b", "gemini"],
    TaskClass.MEDIUM.value: ["ollama:qwen3:8b", "gemini", "ollama:llama3.2:3b"],
    TaskClass.HEAVY.value: ["ollama:qwen3:8b", "gemini", "claude"],
    TaskClass.CRITICAL.value: ["gemini", "ollama:qwen3:8b", "claude"],
}


@dataclass
class ProviderCapability:
    """What one provider/model can do, and what it costs to find out."""
    key: str
    provider: str
    model: str
    paid: bool
    context_tokens: int
    suitable_for: set[TaskClass]
    cost_class: str                       # free | free_tier | paid
    notes: str = ""

    def handles(self, task: TaskClass) -> bool:
        return task in self.suitable_for


CAPABILITIES: dict[str, ProviderCapability] = {
    "ollama:llama3.2:3b": ProviderCapability(
        key="ollama:llama3.2:3b", provider="ollama", model="llama3.2:3b", paid=False,
        context_tokens=128_000, cost_class="free",
        suitable_for={TaskClass.LIGHT},
        notes="Fast local model. Sufficient for classification and extraction."),
    "ollama:qwen3:8b": ProviderCapability(
        key="ollama:qwen3:8b", provider="ollama", model="qwen3:8b", paid=False,
        context_tokens=128_000, cost_class="free",
        suitable_for={TaskClass.LIGHT, TaskClass.MEDIUM, TaskClass.HEAVY},
        notes="Primary local workhorse. Requires think=False or it returns empty text."),
    "gemini": ProviderCapability(
        key="gemini", provider="gemini", model="gemini-2.5-flash-lite", paid=False,
        context_tokens=1_000_000, cost_class="free_tier",
        suitable_for={TaskClass.LIGHT, TaskClass.MEDIUM, TaskClass.HEAVY, TaskClass.CRITICAL},
        notes="Free tier. Needs GEMINI_API_KEY; unavailable until configured."),
    "claude": ProviderCapability(
        key="claude", provider="claude", model="claude-sonnet-4-6", paid=True,
        context_tokens=200_000, cost_class="paid",
        suitable_for={TaskClass.HEAVY, TaskClass.CRITICAL},
        notes="Premium fallback. Reachable only when zero-cost mode is off and Claude "
              "is explicitly enabled."),
}


@dataclass
class RoutingDecision:
    """Which provider was chosen for a task, and why."""
    task: str
    task_class: TaskClass
    chosen: str | None = None
    candidates: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    escalated: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task, "task_class": self.task_class.value,
            "chosen": self.chosen, "candidates": self.candidates,
            "skipped": self.skipped, "escalated": self.escalated, "reason": self.reason,
        }


def classify_task(purpose: str) -> TaskClass:
    """How much reasoning this purpose needs. Unknown purposes are treated as MEDIUM."""
    return TASK_CLASSIFICATION.get(purpose, TaskClass.MEDIUM)


class ProviderRegistry:
    """Chooses the cheapest provider that can actually do the job."""

    def __init__(self, repository: WinstonRepository, ai_service: Any,
                 budget: BudgetGuard | None = None) -> None:
        self.repository = repository
        self.ai_service = ai_service
        self.budget = budget or BudgetGuard(repository)

    # ── policy ───────────────────────────────────────────────────────────

    def policy(self) -> dict[str, list[str]]:
        stored = self.repository.get_setting("routing_policy")
        return stored if isinstance(stored, dict) and stored else dict(DEFAULT_ROUTING_POLICY)

    def set_policy(self, policy: dict[str, list[str]]) -> dict[str, list[str]]:
        valid_classes = {t.value for t in TaskClass}
        for task_class, order in policy.items():
            if task_class not in valid_classes:
                raise ValueError(f"unknown task class {task_class!r}; valid: {sorted(valid_classes)}")
            unknown = [k for k in order if k not in CAPABILITIES]
            if unknown:
                raise ValueError(f"unknown provider keys {unknown}; valid: {sorted(CAPABILITIES)}")
        merged = dict(DEFAULT_ROUTING_POLICY)
        merged.update(policy)
        self.repository.set_setting("routing_policy", merged)
        return merged

    # ── availability ─────────────────────────────────────────────────────

    def _live_providers(self) -> dict[str, Any]:
        """Providers the AIService actually holds, keyed by registry key."""
        live: dict[str, Any] = {}
        for provider in getattr(self.ai_service, "providers", []):
            key = provider.name
            if provider.name == "ollama":
                key = f"ollama:{provider.model}"
            live[key] = provider
        return live

    def availability(self) -> list[dict[str, Any]]:
        """Every known provider with its capability and current usability."""
        live = self._live_providers()
        zero_cost = bool(getattr(self.ai_service, "zero_cost_mode", True))
        rows = []
        for key, capability in CAPABILITIES.items():
            provider = live.get(key)
            reachable = bool(provider and provider.available())
            blocked_by_cost = capability.paid and zero_cost
            rows.append({
                "key": key, "provider": capability.provider, "model": capability.model,
                "cost_class": capability.cost_class, "paid": capability.paid,
                "context_tokens": capability.context_tokens,
                "suitable_for": sorted(t.value for t in capability.suitable_for),
                "configured": provider is not None,
                "reachable": reachable,
                "usable": reachable and not blocked_by_cost,
                "blocked_reason": ("zero-cost mode blocks paid providers" if blocked_by_cost
                                   else None if reachable
                                   else "not configured or unreachable"),
                "notes": capability.notes,
            })
        return rows

    # ── routing ──────────────────────────────────────────────────────────

    def route(self, purpose: str, *, estimated_input_tokens: int = 1500,
              estimated_output_tokens: int = 500) -> RoutingDecision:
        """Pick a provider for one task without calling it.

        Token estimates exist so a paid provider can be budget-checked *before* it is
        selected. A provider that cannot afford the call is never chosen, which is why
        budget exhaustion produces a refusal rather than an invoice.
        """
        task_class = classify_task(purpose)
        order = self.policy().get(task_class.value, [])
        availability = {row["key"]: row for row in self.availability()}
        decision = RoutingDecision(task=purpose, task_class=task_class, candidates=list(order))

        for index, key in enumerate(order):
            capability = CAPABILITIES.get(key)
            row = availability.get(key, {})
            if capability is None:
                decision.skipped.append({"key": key, "why": "unknown provider"})
                continue
            if not capability.handles(task_class):
                decision.skipped.append({"key": key, "why": f"not suited to {task_class.value}"})
                continue
            if not row.get("usable"):
                decision.skipped.append({"key": key, "why": row.get("blocked_reason") or "unusable"})
                continue

            # Budget is a routing constraint, not an afterthought. A provider with no
            # remaining budget is unroutable, so exhaustion cannot surface as a bill.
            if capability.paid or capability.cost_class == "free_tier":
                verdict = self.budget.check(
                    capability.provider,
                    input_tokens=estimated_input_tokens, output_tokens=estimated_output_tokens)
                if not verdict.allowed:
                    decision.skipped.append({"key": key, "why": verdict.reason})
                    continue

            decision.chosen = key
            decision.escalated = index > 0
            decision.reason = (
                f"{key} is the first usable provider for a {task_class.value} task"
                + (f", after {index} unusable option(s)" if index else ""))
            if capability.paid:
                decision.reason += ". Paid provider selected deliberately."
            return decision

        decision.reason = (
            f"No usable provider for a {task_class.value} task. "
            f"Tried: {', '.join(order) or 'nothing configured'}.")
        return decision

    def local_alternatives(self, task_class: TaskClass) -> list[str]:
        """Local models that can take over when the primary local model fails.

        Deliberately local-only. Ollama being briefly unreachable is an infrastructure
        problem, and answering it by sending prospect data to a paid API would convert
        a transient outage into a recurring bill.
        """
        usable = {row["key"] for row in self.availability() if row["usable"]}
        return [key for key, capability in CAPABILITIES.items()
                if capability.cost_class == "free" and capability.handles(task_class)
                and key in usable]

    def generate(self, prompt: str, *, purpose: str, system: str = "",
                 max_tokens: int = 400) -> Any:
        """Route, then generate. Records the decision alongside the call."""
        decision = self.route(purpose)
        self.repository.add_event("ai.routed", details=decision.as_dict())
        if decision.chosen is None:
            raise RuntimeError(decision.reason)
        # AIService owns retry, fallback, and usage recording; the registry decides intent.
        return self.ai_service.generate(prompt, system=system, max_tokens=max_tokens,
                                        purpose=purpose)

    # ── measurement ──────────────────────────────────────────────────────

    def performance(self, window_days: int = 90) -> list[dict[str, Any]]:
        """Measured behaviour per provider and task. No estimates."""
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT provider, model, purpose,
                          COUNT(*) calls,
                          SUM(success) successes,
                          ROUND(AVG(CASE WHEN success=1 THEN latency_ms END)) avg_latency_ms,
                          COALESCE(SUM(estimated_cost_usd),0) cost_usd
                   FROM provider_usage
                   WHERE created_at >= datetime('now', ?)
                   GROUP BY provider, model, purpose
                   ORDER BY calls DESC""",
                (f"-{int(window_days)} days",)).fetchall()
        results = []
        for row in rows:
            record = dict(row)
            calls = record["calls"] or 0
            record["success_rate"] = round((record["successes"] or 0) / calls, 4) if calls else None
            record["task_class"] = classify_task(record["purpose"]).value
            record["cost_per_success"] = (
                round(float(record["cost_usd"]) / record["successes"], 6)
                if record["successes"] else None)
            results.append(record)
        return results

    def summary(self) -> dict[str, Any]:
        performance = self.performance()
        total_cost = sum(float(r["cost_usd"]) for r in performance)
        return {
            "policy": self.policy(),
            "availability": self.availability(),
            "performance": performance,
            "total_ai_cost_usd": round(total_cost, 4),
            "budgets": self.budget.budgets(),
            "spend_capable_providers": self.budget.dashboard()["spend_capable_providers"],
            "usable_providers": [r["key"] for r in self.availability() if r["usable"]],
            "task_classes": {t.value: sorted(
                p for p, c in TASK_CLASSIFICATION.items() if c is t) for t in TaskClass},
            "generated_at": utc_now(),
        }
