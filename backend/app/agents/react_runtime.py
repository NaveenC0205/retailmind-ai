"""Live LangChain ReAct shopper — same pattern as the working RetailPro agent.

Reference: create_react_agent(llm, tools that query SQLite, checkpointer).
ShopZone tools go through the Tool Gateway so authz still holds. CI keeps the
deterministic mock loop; this path is only used when a live OpenAI key is set.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agents.base import SubResult, TerminalState
from app.config import get_settings
from app.tracing import span

CUSTOMER_TOOLS = (
    "search_products",
    "get_product",
    "compare_products",
    "check_inventory",
    "get_orders",
    "get_order",
    "get_payment",
    "get_shipment",
    "track_shipment",
    "list_payment_methods",
    "create_order",
    "cancel_order",
    "retrieve_policy",
    "search_knowledge_base",
    "get_recommendations",
    "get_active_promotions",
)

OWNER_TOOLS = (
    "get_pending_orders",
    "approve_order",
    "reject_order",
    "add_product",
    "list_low_stock",
    "check_inventory",
    "search_products",
    "get_product",
    "get_order",
    "get_orders",
    "retrieve_policy",
)

_STALL = (
    "please hold",
    "hold on for a moment",
    "how can i assist you today",
    "feel free to ask",
    "shopzone assistant working",
)


def live_react_enabled() -> bool:
    s = get_settings()
    if (s.llm_provider or "").lower() in {"mock", "mock-naive"}:
        return False
    if (s.cassette_mode or "").lower() == "replay":
        return False
    if (s.llm_backend or "mock") == "mock":
        return False
    return bool(s.openai_api_key)


def is_stalling_reply(text: str) -> bool:
    low = (text or "").lower()
    if not low.strip():
        return True
    return any(p in low for p in _STALL) and "₹" not in (text or "") and "or-" not in low


def _tools_for_persona(persona: str) -> tuple[str, ...]:
    if persona == "owner":
        return OWNER_TOOLS
    return CUSTOMER_TOOLS


def _system_prompt(orch) -> str:
    logged = orch.principal.kind == "customer" and bool(orch.principal.customer_id)
    cid = orch.principal.customer_id or "guest"
    if orch.persona == "owner":
        return (
            "You are ShopZone Seller Ops — a LangGraph ReAct agent. "
            "Call tools against the live shop database. Never invent stock, orders, or SKUs. "
            "Pending queue → get_pending_orders. Restock → list_low_stock. "
            "New catalogue item → add_product. Never say please hold on; call a tool instead."
        )
    return (
        "You are ShopZone Assistant — a LangGraph ReAct shopping agent. "
        "Always call tools for catalogue, orders, payments, and policy. Never invent prices. "
        f"customer_id={cid}. logged_in={logged}. "
        "Search with search_products (use brand/category when they named them, e.g. Samsung laptops). "
        "Order history: get_orders. A named past order (e.g. hot box): get_orders then match titles. "
        "To place an order: search_products, then create_order with customer_id, items [{product_id, qty: 1}], "
        "and payment_method upi|card|cod (default upi). Do not ask for a street address — omit address and "
        "the shop ships to the customer's saved address. If they say use my existing/saved address, call "
        "create_order immediately with the product from this conversation. "
        "If they said order/buy/for me and named a product and are logged in but skipped payment, use upi and say so. "
        "Guests cannot create_order — tell them to sign in. "
        "Never reply with 'please hold on' or a generic hello when they asked to search or order. "
        "Answer in English, with ₹ and order ids from tool results."
    )


def _build_tools(orch, tool_names: tuple[str, ...]):
    from langchain_core.tools import StructuredTool
    from pydantic import create_model

    from app.tools.contracts import registry

    tools = []
    for name in tool_names:
        contract = registry.get(name)
        if not contract:
            continue
        schema = contract.input_model
        fields: dict[str, Any] = {}
        for pname, prop in (getattr(schema, "model_fields", None) or {}).items():
            fields[pname] = (Any, ... if prop.is_required() else None)
        if not fields:
            ArgModel = create_model(f"{name}_Args", query=(str, ""))
        else:
            ArgModel = create_model(f"{name}_Args", **fields)

        async def _run(_tool=name, **kwargs):
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            cid = orch.principal.customer_id
            if cid and _tool in (
                "get_orders",
                "create_order",
                "get_customer",
                "get_customer_preferences",
                "get_recommendations",
            ):
                kwargs.setdefault("customer_id", cid)
            hitl_ctx = {
                "order_total_inr": 0,
                "reason": "react tool call",
                "checkpoint": {"agent": "langchain-react", "tool": _tool},
            }
            result = await orch.gateway.call(_tool, kwargs, hitl_context=hitl_ctx)
            orch.steps += 1
            return json.dumps(result.for_model(), default=str)

        tools.append(
            StructuredTool.from_function(
                coroutine=_run,
                name=name,
                description=contract.description or name,
                args_schema=ArgModel,
            )
        )
    return tools


def _last_ai_text(messages) -> str:
    for m in reversed(messages or []):
        text = getattr(m, "content", None) or ""
        if not text:
            continue
        if getattr(m, "type", "") == "ai":
            return text if isinstance(text, str) else str(text)
    return ""


async def run_react_shopper(orch, text: str, facts: dict, event_sink=None):
    """One ReAct agent + live DB tools, matching the reference LangChain shop agent."""
    from langgraph.prebuilt import create_react_agent

    from app.agents.langgraph_runtime import _chat_openai, _emit

    await _emit(
        event_sink,
        {"type": "agent_start", "agent": orch.persona or "customer", "task": "react tools", "framework": "langgraph"},
    )
    tools = _build_tools(orch, _tools_for_persona(orch.persona))
    agent = create_react_agent(
        model=_chat_openai(),
        tools=tools,
        prompt=_system_prompt(orch),
        name=f"{orch.persona or 'customer'}_react",
    )
    messages = []
    for role, content in await orch._recent_turns(8):
        if not content:
            continue
        mapped = "user" if role == "user" else "assistant"
        messages.append({"role": mapped, "content": str(content)[:1500]})
    messages.append({"role": "user", "content": text})
    with span("langgraph.react_shopper", persona=orch.persona or "customer"):
        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": messages},
                {"recursion_limit": 6},
            ),
            timeout=16,
        )
    answer = (_last_ai_text(result.get("messages")) or "").strip()
    await _emit(
        event_sink,
        {
            "type": "agent_done",
            "agent": orch.persona or "customer",
            "summary": answer[:400],
            "terminal_state": TerminalState.COMPLETED.value,
            "framework": "langgraph",
        },
    )
    if is_stalling_reply(answer):
        return None
    sub = [
        SubResult(
            agent="react",
            task="live tools",
            summary=answer[:400],
            steps=max(orch.steps, 1),
            tools=[],
            terminal_state=TerminalState.COMPLETED.value,
        ).__dict__
    ]
    return answer, TerminalState.COMPLETED, sub, facts.get("citation_ids", []), facts, ""
