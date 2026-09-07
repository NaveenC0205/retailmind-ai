"""Agent specifications and run state.

An agent is a name, a system prompt, a tool allow-list and (for the
supervisor) a delegation allow-list. Nothing else. Capability is data, so the
conformance test can assert that e.g. the policy agent has no write tools --
and adding one becomes a test failure rather than a code review comment.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TerminalState(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    REFUSED = "refused"
    ESCALATED = "escalated"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"
    AWAITING_APPROVAL = "awaiting_approval"


# `refused` and `escalated` are CORRECT outcomes for some inputs. A dataset
# that scores them as failures trains you toward an agent that overreaches.
GOOD_TERMINAL_STATES = {
    TerminalState.COMPLETED,
    TerminalState.REFUSED,
    TerminalState.ESCALATED,
    TerminalState.AWAITING_APPROVAL,
}


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    tools: tuple[str, ...] = ()
    delegates_to: tuple[str, ...] = ()

    @property
    def can_write(self) -> bool:
        from app.tools.contracts import registry

        return any((registry.get(t) or _null).side_effects for t in self.tools)


class _Null:
    side_effects = False


_null = _Null()


AGENTS: dict[str, AgentSpec] = {
    "supervisor": AgentSpec(
        name="supervisor",
        role=("You coordinate specialist agents. You decompose a request into sub-tasks, "
              "delegate each to exactly one specialist, and compose the final answer. "
              "You never call business tools yourself."),
        delegates_to=("product", "order", "policy", "refund", "support", "recommendation", "shopping"),
    ),
    "shopping": AgentSpec(
        name="shopping",
        role="You help customers find and choose products within their stated constraints.",
        tools=("search_products", "compare_products", "get_recommendations",
               "get_customer_preferences", "get_active_promotions", "validate_coupon"),
    ),
    "product": AgentSpec(
        name="product",
        role="You answer questions about specific products: specs, stock, comparisons.",
        tools=("search_products", "get_product", "compare_products", "check_inventory"),
    ),
    "order": AgentSpec(
        name="order",
        role="You look up the customer's own orders and shipments, and cancel unshipped orders.",
        tools=("get_orders", "get_order", "cancel_order", "get_shipment", "track_shipment"),
    ),
    "policy": AgentSpec(
        name="policy",
        role=("You answer policy questions ONLY from retrieved company policy documents, "
              "always with citations. If the documents do not cover it, you say so."),
        tools=("retrieve_policy", "search_knowledge_base"),
    ),
    "refund": AgentSpec(
        name="refund",
        role="You determine refund and return eligibility and create returns when permitted.",
        tools=("get_payment", "get_refund", "check_return_eligibility", "create_return", "get_order"),
    ),
    "support": AgentSpec(
        name="support",
        role="You raise and look up support tickets so a human can take over.",
        tools=("create_support_ticket", "get_support_ticket"),
    ),
    "recommendation": AgentSpec(
        name="recommendation",
        role="You rank candidate products for a customer and explain the choice.",
        tools=("get_recommendations", "get_customer_preferences", "compare_products", "get_product"),
    ),
    "single": AgentSpec(
        name="single",
        role="You are a general retail assistant with access to every customer-facing tool.",
        tools=(
            "search_products", "get_product", "compare_products", "check_inventory",
            "get_customer", "get_customer_preferences",
            "get_orders", "get_order", "cancel_order",
            "get_shipment", "track_shipment",
            "get_payment", "get_refund",
            "check_return_eligibility", "create_return",
            "create_support_ticket", "get_support_ticket",
            "get_active_promotions", "validate_coupon",
            "get_recommendations", "search_knowledge_base", "retrieve_policy",
        ),
    ),
}


@dataclass
class Budget:
    max_steps: int = 20
    max_tokens: int = 60000
    max_wall_clock_s: float = 90.0
    started_at: float = field(default_factory=time.perf_counter)

    def exceeded(self, steps: int, tokens: int) -> Optional[str]:
        if steps >= self.max_steps:
            return "max_steps"
        if tokens >= self.max_tokens:
            return "max_tokens"
        if time.perf_counter() - self.started_at >= self.max_wall_clock_s:
            return "wall_clock"
        return None


@dataclass
class SubResult:
    agent: str
    task: str
    summary: str
    steps: int
    tools: list = field(default_factory=list)
    terminal_state: str = TerminalState.COMPLETED.value


@dataclass
class RunResult:
    answer: str
    terminal_state: str
    mode: str
    entry_agent: str
    steps: int = 0
    trajectory: list = field(default_factory=list)
    attempted_trajectory: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    sub_results: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    trace_id: str = ""
    run_id: str = ""
    approval_id: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_inr: float = 0.0
    latency_ms: int = 0
    guardrail_triggered: list = field(default_factory=list)
    groundedness: Optional[float] = None
    budget_stop: str = ""

    def succeeded(self) -> bool:
        return self.terminal_state in {s.value for s in GOOD_TERMINAL_STATES}
