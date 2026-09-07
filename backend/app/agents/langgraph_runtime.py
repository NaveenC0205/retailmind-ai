"""LangGraph multi-agent runtime (LangChain ecosystem).

Jewellery retail chat uses a real multi-agent framework — LangGraph StateGraph
with a supervisor that routes to specialist nodes (shopping, product, order,
checkout, policy, refund, support, recommendation, admin).

Specialist execution:
  • mock / cassette / no OpenAI key → existing bounded agent loop (eval-safe)
  • openai (+ API key) → LangChain create_react_agent + StructuredTool wrappers
    around the Tool Gateway (policy still enforced)

Streaming: ``run_langgraph_team(..., event_sink=...)`` emits live handoff events
for the SSE chat endpoint.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.base import AGENTS, SubResult, TerminalState
from app.config import get_settings
from app.tracing import span

EventSink = Callable[[dict], Awaitable[None] | None]

SPECIALISTS = (
    "shopping",
    "product",
    "order",
    "checkout",
    "policy",
    "refund",
    "support",
    "recommendation",
    "admin",
    "inventory",
)


class TeamState(TypedDict, total=False):
    user_text: str
    intent: str
    facts: dict
    delegated: list
    sub_results: list
    next_agent: str
    task: str
    answer: str
    terminal: str
    approval_id: str
    hop: int
    framework: str


@dataclass
class LangGraphTeamResult:
    answer: str
    terminal: TerminalState
    sub_results: list[SubResult]
    facts: dict
    approval_id: str = ""
    framework: str = "langgraph"
    events: list[dict] = field(default_factory=list)


async def _emit(sink: Optional[EventSink], payload: dict) -> None:
    if not sink:
        return
    out = sink(payload)
    if hasattr(out, "__await__"):
        await out  # type: ignore[misc]


def _use_langchain_react(orch, agent_name: str = "") -> bool:
    """Live ReAct hangs shop chat on Vercel. Native/mock loop stays eval-safe."""
    return False


def _build_langchain_tools(orch, tool_names: tuple[str, ...]):
    """Wrap Tool Gateway calls as LangChain StructuredTools (async)."""
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
            hitl_ctx = {
                "order_total_inr": 0,
                "reason": "langgraph tool call",
                "checkpoint": {"agent": "langchain", "tool": _tool},
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


def _chat_openai():
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(
        model=s.openai_model,
        api_key=s.openai_api_key or "sk-missing",
        base_url=s.openai_base_url,
        temperature=s.llm_temperature,
    )


async def _run_specialist_langchain(orch, agent_name: str, task: str, user_text: str) -> tuple[str, TerminalState, str]:
    from langgraph.prebuilt import create_react_agent

    spec = AGENTS[agent_name]
    tools = _build_langchain_tools(orch, spec.tools)
    if not tools:
        return f"{agent_name} has no tools configured.", TerminalState.PARTIAL, ""

    model = _chat_openai()
    logged = orch.principal.kind == "customer" and bool(orch.principal.customer_id)
    cid = orch.principal.customer_id or "guest"
    extra = (
        f"\nAuthenticated customer_id={cid}. Logged in={logged}. "
        "If placing an order, call list_payment_methods unless they already named UPI/card/COD, "
        "then create_order with that payment_method. Guests cannot create_order."
    )
    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=spec.role + extra + "\nRespond with a concise final answer for the supervisor.",
        name=agent_name,
    )
    with span("langgraph.react", **{"agent.name": agent_name}):
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": f"{user_text}\n\nSub-task: {task}"}]}
        )
    messages = result.get("messages") or []
    content = ""
    for m in reversed(messages):
        text = getattr(m, "content", None) or ""
        if text and getattr(m, "type", "") == "ai":
            content = text if isinstance(text, str) else str(text)
            break
    return content or f"{agent_name} completed.", TerminalState.COMPLETED, ""


async def _run_specialist_native(orch, agent_name: str, task: str, user_text: str, facts: dict, intent: str):
    child = AGENTS[agent_name]
    with span(
        "agent.delegate",
        **{"agent.parent": "supervisor", "agent.child": child.name, "framework": "langgraph"},
    ):
        child_answer, child_terminal, facts, child_approval = await orch._agent_loop(
            child, task or user_text, user_text, facts, intent
        )
    return child_answer, child_terminal, facts, child_approval


def build_jewellery_team_graph(
    orch,
    event_sink: Optional[EventSink] = None,
    specialists: tuple[str, ...] = SPECIALISTS,
    persona: str = "customer",
):
    """Compile a LangGraph supervisor → specialists → FINISH graph bound to this orchestrator."""
    from app.agents.teams import supervisor_hint

    allowed = tuple(a for a in specialists if a in AGENTS)

    async def supervisor_node(state: TeamState) -> dict:
        hop = int(state.get("hop") or 0) + 1
        facts = dict(state.get("facts") or {})
        delegated = list(state.get("delegated") or [])
        sub_results = list(state.get("sub_results") or [])
        user_text = state.get("user_text") or ""
        intent = state.get("intent") or ""

        stop = orch.budget.exceeded(
            orch.steps, orch.router.total_tokens_in + orch.router.total_tokens_out
        )
        if stop:
            await _emit(event_sink, {"type": "budget", "message": "Budget exceeded"})
            return {
                "next_agent": "FINISH",
                "terminal": TerminalState.BUDGET_EXCEEDED.value,
                "answer": " ".join(
                    (s["summary"] if isinstance(s, dict) else s.summary) for s in sub_results
                )
                or "I couldn't finish every part of that request.",
                "hop": hop,
                "framework": "langgraph",
            }

        supervisor = AGENTS["supervisor"]
        action = await orch._decide(
            supervisor,
            user_text,
            user_text,
            facts,
            intent,
            extra_meta={
                "delegated_agents": delegated,
                "sub_results": [
                    s if isinstance(s, dict) else s.__dict__ for s in sub_results
                ],
                "framework": "langgraph",
                "persona": persona,
                "allowed_agents": list(allowed),
                "supervisor_hint": supervisor_hint(persona),
            },
        )
        orch.steps += 1
        orch._record_step(supervisor.name, action)

        await _emit(
            event_sink,
            {
                "type": "supervisor",
                "action": action.type,
                "agent": action.agent,
                "task": action.task,
                "hop": hop,
                "framework": "langgraph",
            },
        )

        if action.type == "delegate" and action.agent in allowed:
            return {
                "next_agent": action.agent,
                "task": action.task or user_text,
                "hop": hop,
                "framework": "langgraph",
            }

        if action.is_terminal():
            answer = action.content or " ".join(
                (s["summary"] if isinstance(s, dict) else getattr(s, "summary", ""))
                for s in sub_results
            )
            term = {
                "answer": TerminalState.COMPLETED,
                "refuse": TerminalState.REFUSED,
                "escalate": TerminalState.ESCALATED,
            }.get(action.type, TerminalState.COMPLETED)
            return {
                "next_agent": "FINISH",
                "answer": answer,
                "terminal": term.value,
                "hop": hop,
                "framework": "langgraph",
            }

        return {
            "next_agent": "FINISH",
            "answer": " ".join(
                (s["summary"] if isinstance(s, dict) else getattr(s, "summary", ""))
                for s in sub_results
            )
            or "I couldn't finish every part of that request.",
            "terminal": TerminalState.PARTIAL.value,
            "hop": hop,
            "framework": "langgraph",
        }

    def _make_specialist(agent_name: str):
        async def specialist_node(state: TeamState) -> dict:
            user_text = state.get("user_text") or ""
            intent = state.get("intent") or ""
            facts = dict(state.get("facts") or {})
            task = state.get("task") or user_text
            delegated = list(state.get("delegated") or [])
            sub_results = list(state.get("sub_results") or [])
            approval_id = state.get("approval_id") or ""

            await _emit(
                event_sink,
                {
                    "type": "agent_start",
                    "agent": agent_name,
                    "task": task,
                    "framework": "langgraph",
                },
            )

            if _use_langchain_react(orch, agent_name):
                try:
                    child_answer, child_terminal, child_approval = await _run_specialist_langchain(
                        orch, agent_name, task, user_text
                    )
                    child_approval = child_approval or ""
                except Exception as exc:  # noqa: BLE001 — fall back to native loop
                    await _emit(
                        event_sink,
                        {"type": "agent_fallback", "agent": agent_name, "error": str(exc)},
                    )
                    child_answer, child_terminal, facts, child_approval = await _run_specialist_native(
                        orch, agent_name, task, user_text, facts, intent
                    )
            else:
                child_answer, child_terminal, facts, child_approval = await _run_specialist_native(
                    orch, agent_name, task, user_text, facts, intent
                )

            if child_approval:
                approval_id = child_approval

            sub = SubResult(
                agent=agent_name,
                task=task,
                summary=child_answer,
                steps=orch.steps,
                terminal_state=child_terminal.value,
            )
            sub_results.append(sub.__dict__)
            delegated.append(agent_name)

            await _emit(
                event_sink,
                {
                    "type": "agent_done",
                    "agent": agent_name,
                    "summary": (child_answer or "")[:400],
                    "terminal_state": child_terminal.value,
                    "framework": "langgraph",
                },
            )

            out: dict[str, Any] = {
                "facts": facts,
                "delegated": delegated,
                "sub_results": sub_results,
                "approval_id": approval_id,
                "next_agent": "supervisor",
                "framework": "langgraph",
            }
            if child_terminal is TerminalState.AWAITING_APPROVAL:
                out["next_agent"] = "FINISH"
                out["terminal"] = TerminalState.AWAITING_APPROVAL.value
                out["answer"] = child_answer
            return out

        specialist_node.__name__ = f"agent_{agent_name}"
        return specialist_node

    def route_from_supervisor(state: TeamState) -> str:
        nxt = state.get("next_agent") or "FINISH"
        if nxt == "FINISH" or nxt not in allowed:
            return "FINISH"
        return nxt

    def route_from_specialist(state: TeamState) -> str:
        if state.get("next_agent") == "FINISH":
            return "FINISH"
        return "supervisor"

    graph = StateGraph(TeamState)
    graph.add_node("supervisor", supervisor_node)
    for name in allowed:
        graph.add_node(name, _make_specialist(name))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {**{n: n for n in allowed}, "FINISH": END},
    )
    for name in allowed:
        graph.add_conditional_edges(
            name,
            route_from_specialist,
            {"supervisor": "supervisor", "FINISH": END},
        )

    return graph.compile()


async def run_langgraph_team(
    orch,
    text: str,
    facts: dict,
    intent: str,
    event_sink: Optional[EventSink] = None,
    specialists: tuple[str, ...] = SPECIALISTS,
    persona: str = "customer",
) -> LangGraphTeamResult:
    """Execute the persona-scoped multi-agent team on LangGraph."""
    events: list[dict] = []

    async def _collect(ev: dict):
        events.append(ev)
        if event_sink:
            out = event_sink(ev)
            if hasattr(out, "__await__"):
                await out  # type: ignore[misc]

    compiled = build_jewellery_team_graph(
        orch, event_sink=_collect, specialists=specialists, persona=persona
    )
    await _collect(
        {
            "type": "graph_start",
            "framework": "langgraph",
            "persona": persona,
            "specialists": list(specialists),
            "message": f"LangGraph {persona} team starting multi-agent plan",
        }
    )

    with span("langgraph.run", framework="langgraph", intent=intent):
        final: TeamState = await compiled.ainvoke(
            {
                "user_text": text,
                "intent": intent,
                "facts": facts,
                "delegated": [],
                "sub_results": [],
                "next_agent": "supervisor",
                "task": text,
                "answer": "",
                "terminal": TerminalState.COMPLETED.value,
                "approval_id": "",
                "hop": 0,
                "framework": "langgraph",
            }
        )

    raw_subs = final.get("sub_results") or []
    sub_results: list[SubResult] = []
    for s in raw_subs:
        if isinstance(s, SubResult):
            sub_results.append(s)
        elif isinstance(s, dict):
            sub_results.append(
                SubResult(
                    agent=s.get("agent", ""),
                    task=s.get("task", ""),
                    summary=s.get("summary", ""),
                    steps=int(s.get("steps") or 0),
                    tools=list(s.get("tools") or []),
                    terminal_state=s.get("terminal_state", TerminalState.COMPLETED.value),
                )
            )

    answer = final.get("answer") or " ".join(s.summary for s in sub_results) or (
        "I couldn't finish every part of that request."
    )
    term_raw = final.get("terminal") or TerminalState.COMPLETED.value
    try:
        terminal = TerminalState(term_raw)
    except ValueError:
        terminal = TerminalState.COMPLETED

    await _collect(
        {
            "type": "graph_done",
            "framework": "langgraph",
            "agents": [s.agent for s in sub_results],
            "terminal_state": terminal.value,
        }
    )

    return LangGraphTeamResult(
        answer=answer,
        terminal=terminal,
        sub_results=sub_results,
        facts=final.get("facts") or facts,
        approval_id=final.get("approval_id") or "",
        framework="langgraph",
        events=events,
    )
