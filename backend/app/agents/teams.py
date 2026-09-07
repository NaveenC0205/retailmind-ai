"""Persona-scoped LangGraph teams.

Customer, product-page, and shop-owner chats share one orchestrator but
different specialist allow-lists so the seller bot cannot be invoked from a
shopper session (and vice versa). Policy/RAG is on every team.
"""
from __future__ import annotations

from app.agents.base import AGENTS

CUSTOMER_SPECIALISTS = (
    "shopping",
    "product",
    "order",
    "checkout",
    "policy",
    "refund",
    "support",
    "recommendation",
)

PRODUCT_SPECIALISTS = (
    "product",
    "shopping",
    "policy",
    "recommendation",
    "checkout",
)

OWNER_SPECIALISTS = (
    "admin",
    "inventory",
    "policy",
    "order",
    "support",
)

TEAMS = {
    "customer": CUSTOMER_SPECIALISTS,
    "product": PRODUCT_SPECIALISTS,
    "owner": OWNER_SPECIALISTS,
}


def resolve_persona(requested: str, principal_kind: str) -> str:
    persona = (requested or "customer").strip().lower()
    if persona not in TEAMS:
        persona = "customer"
    if persona == "owner" and principal_kind != "operator":
        return "customer"
    return persona


def specialists_for(persona: str) -> tuple[str, ...]:
    return TEAMS.get(persona, CUSTOMER_SPECIALISTS)


def supervisor_hint(persona: str) -> str:
    if persona == "owner":
        return (
            "You supervise the SELLER ops team. Delegate inventory/restock to inventory, "
            "pending/approve/reject to admin, policy to policy, tickets to support. "
            "Never shop for a customer. Cite retrieved policy when answering warranty or SLA."
        )
    if persona == "product":
        return (
            "You supervise a PRODUCT-PAGE customer team. Prefer the product specialist, "
            "then shopping/recommendation, and policy (RAG) for returns/warranty. "
            "Ground every spec claim in catalogue tools or retrieved documents."
        )
    return (
        "You supervise the CUSTOMER shopping team. Use shopping/product/recommendation "
        "for catalogue questions, policy (RAG retrieve_policy) for returns/warranty/shipping, "
        "order/refund/checkout for account actions. Always retrieve policy before stating it."
    )


def assert_team_tools() -> None:
    for name, specs in TEAMS.items():
        for agent in specs:
            assert agent in AGENTS, f"{name} team unknown agent {agent}"
