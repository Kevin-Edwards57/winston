"""Cost control — a surprise AI bill should be structurally impossible.

Winston's inference is meant to cost nothing. Two local models on a box that is already
paid for can classify replies, audit websites, and draft outreach without a single
metered call, and the only way that stays true is if spending money requires a decision
somebody actually made.

So the gate is pre-call and mandatory. Before any paid provider runs, the estimated cost
of the call is compared against what remains of that provider's monthly budget. Default
budgets are **$0**, which means a freshly configured Winston cannot spend money even if
an API key is sitting in the environment.

The rule that matters most is the one about ignorance:

    If the cost of a call cannot be estimated, the call is refused.

Not attempted-and-monitored, not logged-and-allowed. An unknown cost is treated as
unbounded, because the failure mode being prevented is discovering the bill afterwards.

Free providers are exempt because there is nothing to gate. Local inference through
Ollama is unmetered, so it runs without asking.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .repository import WinstonRepository, utc_now

# Per-1M-token pricing used to estimate a call before it runs. Deliberately
# conservative: over-estimating refuses a call that would have been affordable, while
# under-estimating produces the bill this module exists to prevent.
TOKEN_PRICING: dict[str, dict[str, float]] = {
    "claude": {"input_per_1m": 3.00, "output_per_1m": 15.00},
    "gemini": {"input_per_1m": 0.0, "output_per_1m": 0.0},   # free tier
    "ollama": {"input_per_1m": 0.0, "output_per_1m": 0.0},   # local
}

# Every paid provider starts at zero. Spending requires a deliberate change.
DEFAULT_BUDGETS: dict[str, float] = {
    "ollama": -1.0,     # negative means unmetered
    "gemini": 0.0,
    "claude": 0.0,
}

# Requests-per-day ceilings for providers whose free tier is quota based rather than
# priced. Exceeding a free tier is how a "free" provider produces a charge.
FREE_TIER_LIMITS: dict[str, int] = {
    "gemini": 1_000,
}


class BudgetExceeded(RuntimeError):
    """Raised when a call would spend beyond its provider's remaining budget."""


class IndeterminateCost(RuntimeError):
    """Raised when a paid call's cost cannot be estimated. Unknown means refused."""


COSTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_budgets (
    provider TEXT PRIMARY KEY,
    monthly_budget_usd REAL NOT NULL DEFAULT 0,
    daily_request_limit INTEGER,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL DEFAULT 'default',
    updated_at TEXT NOT NULL
);
"""


@dataclass
class BudgetDecision:
    """Whether one call may proceed, and the arithmetic behind that."""
    provider: str
    allowed: bool
    reason: str
    estimated_cost_usd: float = 0.0
    month_to_date_usd: float = 0.0
    monthly_budget_usd: float = 0.0
    remaining_usd: float = 0.0
    metered: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "allowed": self.allowed, "reason": self.reason,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "month_to_date_usd": round(self.month_to_date_usd, 4),
            "monthly_budget_usd": self.monthly_budget_usd,
            "remaining_usd": round(self.remaining_usd, 4),
            "metered": self.metered,
        }


def estimate_cost(provider: str, *, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated dollars for one call. ``None`` means unknown, which means refuse."""
    pricing = TOKEN_PRICING.get(provider)
    if pricing is None:
        return None
    return (input_tokens * pricing["input_per_1m"] / 1_000_000
            + output_tokens * pricing["output_per_1m"] / 1_000_000)


class BudgetGuard:
    """Pre-call gate. Nothing metered runs without passing through here."""

    def __init__(self, repository: WinstonRepository) -> None:
        self.repository = repository

    def _ensure(self) -> None:
        """Create the schema on demand.

        Routing must not crash because a budget table has not been initialised yet;
        the safe default is a table full of zero budgets, not an exception.
        """
        self.initialize()

    def initialize(self) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.executescript(COSTS_SCHEMA)
            for provider, budget in DEFAULT_BUDGETS.items():
                connection.execute(
                    """INSERT INTO provider_budgets(provider,monthly_budget_usd,
                           daily_request_limit,enabled,updated_at)
                       VALUES(?,?,?,?,?) ON CONFLICT(provider) DO NOTHING""",
                    (provider, budget, FREE_TIER_LIMITS.get(provider),
                     1 if budget < 0 else 0, utc_now()))

    # ── configuration ────────────────────────────────────────────────────

    def budgets(self) -> list[dict[str, Any]]:
        self._ensure()
        with self.repository.read() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_budgets ORDER BY provider").fetchall()
        result = []
        for row in rows:
            record = dict(row)
            record["enabled"] = bool(record["enabled"])
            record["unmetered"] = record["monthly_budget_usd"] < 0
            record["month_to_date_usd"] = self.month_to_date(record["provider"])
            record["remaining_usd"] = (
                None if record["unmetered"]
                else round(record["monthly_budget_usd"] - record["month_to_date_usd"], 4))
            result.append(record)
        return result

    def set_budget(self, provider: str, *, monthly_budget_usd: float,  # noqa: D417
                   enabled: bool | None = None, actor: str = "operator") -> dict[str, Any]:
        """Raising a budget above zero is the moment Winston becomes able to spend."""
        if provider not in TOKEN_PRICING:
            raise ValueError(f"unknown provider {provider!r}; known: {sorted(TOKEN_PRICING)}")
        if monthly_budget_usd < 0 and provider != "ollama":
            raise ValueError("only local providers may be unmetered")
        self._ensure()
        with self.repository.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT enabled FROM provider_budgets WHERE provider=?", (provider,)).fetchone()
            resolved = (int(bool(enabled)) if enabled is not None
                        else (current["enabled"] if current else 0))
            connection.execute(
                """INSERT INTO provider_budgets(provider,monthly_budget_usd,enabled,
                       updated_by,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(provider) DO UPDATE SET
                       monthly_budget_usd=excluded.monthly_budget_usd,
                       enabled=excluded.enabled, updated_by=excluded.updated_by,
                       updated_at=excluded.updated_at""",
                (provider, float(monthly_budget_usd), resolved, actor, utc_now()))
        self.repository.add_event("budget.changed", actor=actor,
                                  details={"provider": provider,
                                           "monthly_budget_usd": monthly_budget_usd})
        return next(b for b in self.budgets() if b["provider"] == provider)

    # ── accounting ───────────────────────────────────────────────────────

    def month_to_date(self, provider: str | None = None) -> float:
        clause = " AND provider=?" if provider else ""
        args = (provider,) if provider else ()
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd),0) c FROM provider_usage "
                f"WHERE created_at >= datetime('now','start of month'){clause}", args).fetchone()
        return round(float(row["c"]), 6)

    def today(self, provider: str | None = None) -> float:
        clause = " AND provider=?" if provider else ""
        args = (provider,) if provider else ()
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd),0) c FROM provider_usage "
                f"WHERE created_at >= date('now'){clause}", args).fetchone()
        return round(float(row["c"]), 6)

    def requests_today(self, provider: str) -> int:
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) n FROM provider_usage "
                "WHERE created_at >= date('now') AND provider=?", (provider,)).fetchone()
        return int(row["n"])

    # ── the gate ─────────────────────────────────────────────────────────

    def check(self, provider: str, *, estimated_cost_usd: float | None = None,
              input_tokens: int | None = None, output_tokens: int | None = None
              ) -> BudgetDecision:
        """Decide whether one call may run. Unknown cost is refused, never assumed."""
        self._ensure()
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT * FROM provider_budgets WHERE provider=?", (provider,)).fetchone()

        if row is None:
            return BudgetDecision(
                provider, False,
                f"{provider!r} has no configured budget. An unbudgeted provider cannot run.")

        budget = float(row["monthly_budget_usd"])
        if budget < 0:
            return BudgetDecision(provider, True, "local provider, unmetered",
                                  metered=False, monthly_budget_usd=budget)

        if not row["enabled"]:
            return BudgetDecision(
                provider, False,
                f"{provider!r} is not enabled. Enable it explicitly before it can be called.",
                monthly_budget_usd=budget)

        if estimated_cost_usd is None:
            if input_tokens is None or output_tokens is None:
                return BudgetDecision(
                    provider, False,
                    f"Cost of this {provider!r} call cannot be estimated, so it is refused. "
                    "An unknown cost is treated as unbounded.",
                    monthly_budget_usd=budget)
            estimated_cost_usd = estimate_cost(provider, input_tokens=input_tokens,
                                               output_tokens=output_tokens)
            if estimated_cost_usd is None:
                return BudgetDecision(
                    provider, False,
                    f"No pricing model for {provider!r}, so cost cannot be bounded.",
                    monthly_budget_usd=budget)

        limit = row["daily_request_limit"]
        if limit is not None:
            used = self.requests_today(provider)
            if used >= int(limit):
                return BudgetDecision(
                    provider, False,
                    f"{provider!r} free-tier limit reached: {used} of {limit} requests today. "
                    "Continuing would leave the free tier and incur charges.",
                    estimated_cost_usd=estimated_cost_usd, monthly_budget_usd=budget)

        spent = self.month_to_date(provider)
        remaining = budget - spent
        if estimated_cost_usd > remaining:
            return BudgetDecision(
                provider, False,
                f"${estimated_cost_usd:.4f} exceeds the ${remaining:.4f} remaining of "
                f"{provider!r}'s ${budget:.2f} monthly budget.",
                estimated_cost_usd=estimated_cost_usd, month_to_date_usd=spent,
                monthly_budget_usd=budget, remaining_usd=remaining)

        return BudgetDecision(
            provider, True,
            f"${estimated_cost_usd:.4f} fits within ${remaining:.4f} remaining",
            estimated_cost_usd=estimated_cost_usd, month_to_date_usd=spent,
            monthly_budget_usd=budget, remaining_usd=remaining)

    def enforce(self, provider: str, **kwargs: Any) -> BudgetDecision:
        """Raise rather than return when a call must not proceed."""
        decision = self.check(provider, **kwargs)
        if not decision.allowed:
            if "cannot be estimated" in decision.reason or "cannot be bounded" in decision.reason:
                raise IndeterminateCost(decision.reason)
            raise BudgetExceeded(decision.reason)
        return decision

    # ── reporting ────────────────────────────────────────────────────────

    def dashboard(self) -> dict[str, Any]:
        """Make the zero-cost architecture visible rather than assumed."""
        now = datetime.now(timezone.utc)
        day_of_month = max(now.day, 1)
        month_total = self.month_to_date()

        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT provider, model,
                          COUNT(*) requests,
                          SUM(success) successes,
                          COALESCE(SUM(input_tokens),0) input_tokens,
                          COALESCE(SUM(output_tokens),0) output_tokens,
                          COALESCE(SUM(estimated_cost_usd),0) cost_usd,
                          ROUND(AVG(CASE WHEN success=1 THEN latency_ms END)) avg_latency_ms
                   FROM provider_usage
                   WHERE created_at >= datetime('now','start of month')
                   GROUP BY provider, model ORDER BY requests DESC""").fetchall()

        providers = []
        for row in rows:
            record = dict(row)
            requests = record["requests"] or 0
            record["success_rate"] = (round((record["successes"] or 0) / requests, 4)
                                      if requests else None)
            record["failures"] = requests - (record["successes"] or 0)
            record["local"] = record["provider"] == "ollama"
            providers.append(record)

        budgets = self.budgets()
        return {
            "ai_cost": {
                "today_usd": self.today(),
                "month_to_date_usd": month_total,
                "projected_month_usd": round(month_total / day_of_month * 30, 4),
            },
            "local_inference": [p for p in providers if p["local"]],
            "cloud_inference": [p for p in providers if not p["local"]],
            "budgets": budgets,
            "spend_capable_providers": [
                b["provider"] for b in budgets
                if b["enabled"] and not b["unmetered"] and b["monthly_budget_usd"] > 0],
            "zero_cost": month_total == 0.0,
            "generated_at": utc_now(),
        }
