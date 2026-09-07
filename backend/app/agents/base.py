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
        role=(
            "You are the supervisor of a multi-agent electronics retail team "
            "(phones, laptops, audio, monitors, accessories). "
            "Decompose the customer or shop-owner request into sub-tasks, "
            "delegate each to exactly one specialist, wait for their results, "
            "then compose one clear final answer. Never call business tools yourself. "
            "Prefer parallel planning: shopping+policy, order+refund, admin+order when needed."
        ),
        delegates_to=(
            "shopping", "product", "order", "checkout", "policy",
            "refund", "support", "recommendation", "admin",
        ),
    ),
    "shopping": AgentSpec(
        name="shopping",
        role=(
            "You are the shopping specialist for electronics. Help customers find "
            "phones, laptops, headphones, monitors and accessories using search_products "
            "with category, brand, min/max price, min_rating, min_ram_gb, min_storage_gb, "
            "requires_anc, and attribute_contains. Always filter from catalogue data."
        ),
        tools=(
            "search_products", "compare_products", "get_recommendations",
            "get_customer_preferences", "get_active_promotions", "validate_coupon",
        ),
    ),
    "product": AgentSpec(
        name="product",
        role="You answer questions about specific electronics: specs (RAM, storage, CPU, ANC), stock, and comparisons.",
        tools=("search_products", "get_product", "compare_products", "check_inventory"),
    ),
    "order": AgentSpec(
        name="order",
        role="You look up the customer's own orders and shipments, and cancel unshipped orders.",
        tools=("get_orders", "get_order", "cancel_order", "get_shipment", "track_shipment"),
    ),
    "checkout": AgentSpec(
        name="checkout",
        role=(
            "You help place orders for the authenticated customer using create_order. "
            "Confirm product ids and quantities from prior search/facts before ordering."
        ),
        tools=("search_products", "get_product", "check_inventory", "create_order", "get_orders"),
    ),
    "policy": AgentSpec(
        name="policy",
        role=(
            "You answer policy questions ONLY from retrieved company policy documents, "
            "always with citations. Cover returns, shipping, warranty, and exchange."
        ),
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
        role="You rank electronics for a customer (budget, specs, brand preference) and explain the choice using catalogue data.",
        tools=("get_recommendations", "get_customer_preferences", "compare_products", "get_product", "search_products"),
    ),
    "admin": AgentSpec(
        name="admin",
        role=(
            "You assist the shop owner / seller: list pending orders, approve or reject orders, "
            "and summarize operational status. Use admin tools carefully."
        ),
        tools=("get_pending_orders", "approve_order", "reject_order", "get_order", "get_orders"),
    ),
    "single": AgentSpec(
        name="single",
        role="You are a general retail assistant with access to every customer-facing tool.",
        tools=(
            "search_products", "get_product", "compare_products", "check_inventory",
            "get_customer", "get_customer_preferences",
            "get_orders", "get_order", "cancel_order", "create_order",
            "get_shipment", "track_shipment",
            "get_payment", "get_refund",
            "check_return_eligibility", "create_return",
            "create_support_ticket", "get_support_ticket",
            "get_active_promotions", "validate_coupon",
            "get_recommendations", "search_knowledge_base", "retrieve_policy",
            "get_pending_orders", "approve_order", "reject_order",
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
