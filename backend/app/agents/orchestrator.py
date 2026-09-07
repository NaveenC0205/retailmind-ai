"""Agent Orchestrator: mode routing, the bounded loop, delegation.

Four modes share one runtime. Mode is a routing decision plus a capability
set, not four codebases -- otherwise you cannot run the same dataset through
all four and compare them, which is the entire point of building this.

The loop is bounded on three axes (steps, tokens, wall clock) and terminates
into one of six states. Budget exhaustion returns `partial` with whatever was
learned, never a crash and never an empty answer.
"""
from __future__ import annotations

import time
from typing import Optional

from app.agents.base import (
    AGENTS,
    AgentSpec,
    Budget,
    RunResult,
    SubResult,
    TerminalState,
)
from app.config import get_settings
from app.guardrails import (
    ESCALATION_MESSAGE,
    REFUSAL_MESSAGE,
    Verdict,
    run_input_chain,
    run_output_chain,
)
from app.llm.base import AgentAction, CompletionRequest
from app.llm.mock import detect_intent
from app.llm.router import LLMRouter
from app.memory import MemoryExtractor, MemoryStore
from app.models import (
    AgentRun,
    AgentStep,
    Citation,
    GuardrailEvent,
    Retrieval,
    ToolCall,
    new_id,
)
from app.prompts import get_prompt
from app.provenance import Context, Provenance
from app.security import Principal
from app.tools.contracts import registry
from app.tools.gateway import ToolGateway, ToolResult
from app.tracing import Trace, current_trace, new_trace, span

MULTI_AGENT_INTENTS = {
    "order_delay_refund", "return_item", "shopping_complex",
    "gift_advice", "admin_ops", "checkout_help",
}
# order_list is a single-tool lookup; routing it to the agent loop is fine,
# routing it to multi-agent would be four agents for one SELECT.
RAG_INTENTS = {"policy"}
CHAT_INTENTS = {"smalltalk"}


def classify_mode(user_text: str, requested: str = "auto") -> tuple[str, str]:
    """Returns (mode, intent). Mode routing is a cheap deterministic classifier
    on purpose -- a model call here would double latency for every turn and add
    a failure mode to the hottest path in the system."""
    intent = detect_intent(user_text)
    text = (user_text or "").lower()

    # Heuristics that expand multi-agent coverage for the jewellery shop chat.
    if any(k in text for k in ("gift", "anniversary", "wedding", "recommend under", "budget")):
        intent = intent if intent in MULTI_AGENT_INTENTS else "gift_advice"
    if any(k in text for k in ("pending order", "approve order", "reject order", "shop owner", "seller")):
        intent = "admin_ops"
    if any(k in text for k in ("place order", "buy now", "checkout", "create order")):
        intent = "checkout_help"
    jewel = ("earring", "necklace", "pendant", "bangle", "mangalsutra", "charm", "jewellery", "jewelry")
    if any(k in text for k in jewel) and any(
        k in text for k in ("and", "also", "under", "return", "policy", "ship", "gift", "compare")
    ):
        intent = "shopping_complex"

    if requested and requested != "auto":
        return requested, intent
    if intent in MULTI_AGENT_INTENTS:
        return "multi_agent", intent
    if intent in RAG_INTENTS:
        return "rag", intent
    if intent in CHAT_INTENTS:
        return "chat", intent
    return "agent", intent


# ----------------------------------------------------------------------
# fact absorption
# ----------------------------------------------------------------------

def absorb(facts: dict, tool: str, data: dict) -> dict:
    """Pull the handful of identifiers later steps need out of a tool result.

    A real LLM reads these out of the TOOL_OUTPUT envelope. Keeping an explicit
    structured mirror means the trajectory evaluators can assert on argument
    *provenance* -- e.g. that a shipment_id was carried from a previous tool
    result rather than invented.
    """
    if not isinstance(data, dict):
        return facts
    if tool == "search_products":
        products = data.get("products") or []
        facts["candidate_product_ids"] = [p["product_id"] for p in products]
        facts["candidate_titles"] = [p["title"] for p in products]
        if products:
            facts.setdefault("product_id", products[0]["product_id"])
    elif tool in ("get_product",):
        facts["product_id"] = data.get("product_id", facts.get("product_id"))
    elif tool == "compare_products":
        facts["cheapest_product_id"] = data.get("cheapest_product_id")
        facts["best_rated_product_id"] = data.get("best_rated_product_id")
    elif tool == "get_orders":
        orders = data.get("orders") or []
        facts["orders"] = orders
        if orders:
            facts["order_id"] = orders[0]["order_id"]
            facts["order_status"] = orders[0]["status"]
            facts["order_total_inr"] = orders[0]["total_inr"]
            items = orders[0].get("items") or []
            if items:
                facts["order_item_id"] = items[0]["order_item_id"]
    elif tool == "get_order":
        facts["order_id"] = data.get("order_id", facts.get("order_id"))
        facts["order_status"] = data.get("status")
        facts["order_total_inr"] = data.get("total_inr")
        items = data.get("items") or []
        if items:
            facts["order_item_id"] = items[0]["order_item_id"]
    elif tool == "get_shipment":
        facts["shipment_id"] = data.get("shipment_id")
        facts["shipment_status"] = data.get("status")
        facts["days_late"] = data.get("days_late")
        if data.get("exception_code"):
            facts["shipment_exception"] = data["exception_code"]
    elif tool == "track_shipment":
        facts["shipment_status"] = data.get("status", facts.get("shipment_status"))
        facts["days_late"] = data.get("days_late", facts.get("days_late"))
        events = data.get("events") or []
        if events:
            facts["last_scan"] = events[-1]["description"]
            facts.setdefault("shipment_exception", data.get("exception_code"))
    elif tool == "get_payment":
        facts["payment_id"] = data.get("payment_id")
        facts["payment_amount_inr"] = data.get("amount_inr")
    elif tool == "check_return_eligibility":
        facts["return_eligible"] = data.get("eligible")
        facts["return_window_days"] = data.get("window_days")
        facts["order_item_id"] = data.get("order_item_id", facts.get("order_item_id"))
    elif tool == "create_return":
        facts["return_id"] = data.get("return_id")
    elif tool == "create_support_ticket":
        facts["ticket_id"] = data.get("ticket_id")
    elif tool == "get_active_promotions":
        facts["promotions"] = [p["code"] for p in (data.get("promotions") or [])]
    elif tool in ("search_knowledge_base", "retrieve_policy"):
        facts["chunks"] = data.get("chunks") or []
        facts["citation_ids"] = [c["id"] for c in facts["chunks"]]
    elif tool == "get_recommendations":
        recs = data.get("recommendations") or []
        facts["candidate_product_ids"] = [p["product_id"] for p in recs]
        facts["candidate_titles"] = [p["title"] for p in recs]
        facts["applied_preference"] = data.get("applied_preference") or {}
    return facts


def summarise_result(tool: str, result: ToolResult) -> str:
    if not result.ok:
        return f"{tool} -> {result.status}: {result.denial_reason}"
    import json

    body = json.dumps(result.data, default=str)
    return body if len(body) <= 1400 else body[:1400] + " ...[truncated]"


# ----------------------------------------------------------------------
# orchestrator
# ----------------------------------------------------------------------

class Orchestrator:
    def __init__(
        self,
        session,
        principal: Principal,
        router: Optional[LLMRouter] = None,
        prompt_version: str = "v1",
        budget: Optional[Budget] = None,
        hitl_enabled: bool = True,
        persist: bool = True,
    ):
        s = get_settings()
        self.session = session
        self.principal = principal
        self.router = router or LLMRouter(prompt_version=prompt_version)
        self.prompt_version = prompt_version
        self.budget = budget or Budget(
            max_steps=s.max_steps,
            max_tokens=s.max_tokens_per_run,
            max_wall_clock_s=s.max_wall_clock_s,
        )
        self.persist = persist
        self.run_id = new_id("RUN")
        self.gateway = ToolGateway(
            session, principal, run_id=self.run_id, hitl_enabled=hitl_enabled
        )
        self.memory = MemoryStore(session, principal)
        self.steps = 0
        self._step_rows: list[AgentStep] = []
        self._tool_rows: list[ToolCall] = []

    # ------------------------------------------------------------------
    async def run(
        self,
        user_text: str,
        conversation_id: str = "",
        mode: str = "auto",
        event_sink=None,
    ) -> RunResult:
        started = time.perf_counter()
        trace: Trace = current_trace() or new_trace()

        with span("request", kind="server", **{"principal.kind": self.principal.kind}):
            # 1 -- input guardrails
            with span("guardrail.input"):
                inbound = run_input_chain(user_text, {"principal": self.principal})

            if inbound.verdict is Verdict.REFUSE:
                return await self._finish(
                    REFUSAL_MESSAGE, TerminalState.REFUSED, "guarded", "guardrail",
                    trace, started, conversation_id, user_text,
                    guardrail_triggered=inbound.triggered(),
                )
            if inbound.verdict is Verdict.ESCALATE:
                return await self._finish(
                    ESCALATION_MESSAGE, TerminalState.ESCALATED, "guarded", "guardrail",
                    trace, started, conversation_id, user_text,
                    guardrail_triggered=inbound.triggered(),
                )

            clean_text = inbound.text
            resolved_mode, intent = classify_mode(clean_text, mode)

            with span("conversation.route", mode=resolved_mode, intent=intent):
                facts = await self.memory.facts_dict()

            # 2 -- dispatch
            if resolved_mode == "chat":
                result = await self._run_chat(clean_text, facts)
            elif resolved_mode == "rag":
                result = await self._run_rag(clean_text, facts, intent)
            elif resolved_mode == "multi_agent":
                result = await self._run_multi(clean_text, facts, intent, event_sink=event_sink)
            else:
                result = await self._run_single(clean_text, facts, intent)

            answer, terminal, sub_results, citations, out_facts, approval_id = result

            # 3 -- output guardrails
            out_ctx = {
                "retrieved_chunk_ids": [c["id"] for c in (out_facts.get("chunks") or [])],
                "retrieved_texts": [c["content"] for c in (out_facts.get("chunks") or [])],
                "requires_grounding": resolved_mode == "rag" or intent == "policy",
            }
            with span("guardrail.output"):
                outbound = run_output_chain(answer, out_ctx)
            if outbound.verdict is Verdict.REFUSE:
                answer, terminal = REFUSAL_MESSAGE, TerminalState.REFUSED
            else:
                answer = outbound.text

            return await self._finish(
                answer,
                terminal,
                resolved_mode,
                self._entry_agent(resolved_mode),
                trace,
                started,
                conversation_id,
                user_text,
                sub_results=sub_results,
                citations=citations,
                facts=out_facts,
                approval_id=approval_id,
                guardrail_triggered=inbound.triggered() + outbound.triggered(),
                groundedness=out_ctx.get("groundedness_score"),
            )

    @staticmethod
    def _entry_agent(mode: str) -> str:
        return {"multi_agent": "supervisor", "agent": "single", "rag": "policy", "chat": "chat"}.get(
            mode, "single"
        )

    # ------------------------------------------------------------------
    # modes
    # ------------------------------------------------------------------
    async def _run_chat(self, text: str, facts: dict):
        context = Context()
        context.add(Provenance.SYSTEM, get_prompt("chat_system", self.prompt_version))
        await self.memory.as_fragments(context)
        context.add(Provenance.USER, text)
        completion = await self.router.complete(
            CompletionRequest(
                prompt=context.render(),
                system=get_prompt("chat_system", self.prompt_version),
                purpose="chat",
                meta={"user_text": text},
            )
        )
        self.steps += 1
        return completion.text, TerminalState.COMPLETED, [], [], facts, ""

    async def _run_rag(self, text: str, facts: dict, intent: str):
        from app.rag.retrieve import Retriever

        hits = await Retriever(self.session).search(text, min_trust="trusted")
        chunks = [
            {"id": h.chunk_id, "document_id": h.document_id, "content": h.content,
             "heading": h.heading, "trust": h.trust, "score": h.rerank_score}
            for h in hits
        ]
        facts["chunks"] = chunks
        facts["citation_ids"] = [c["id"] for c in chunks]

        context = Context()
        context.add(Provenance.SYSTEM, get_prompt("rag_system", self.prompt_version))
        for c in chunks:
            context.add(Provenance.RETRIEVED, f"{c['heading']}\n{c['content']}", source_id=c["id"])
        context.add(Provenance.USER, text)

        completion = await self.router.complete(
            CompletionRequest(
                prompt=context.render(),
                system=get_prompt("rag_system", self.prompt_version),
                purpose="rag_answer",
                meta={"user_text": text, "chunks": chunks},
            )
        )
        self.steps += 1
        terminal = TerminalState.COMPLETED if chunks else TerminalState.PARTIAL
        return completion.text, terminal, [], facts["citation_ids"], facts, ""

    async def _run_single(self, text: str, facts: dict, intent: str, spec_name: str = "single"):
        spec = AGENTS[spec_name]
        answer, terminal, facts, approval_id = await self._agent_loop(spec, text, text, facts, intent)
        return answer, terminal, [], facts.get("citation_ids", []), facts, approval_id

    async def _run_multi(self, text: str, facts: dict, intent: str, event_sink=None):
        """Multi-agent mode runs on LangGraph (LangChain multi-agent framework).

        Supervisor + specialist StateGraph; specialists use the native tool loop
        under mock/eval, or LangChain ``create_react_agent`` when OpenAI is live.
        """
        from app.agents.langgraph_runtime import run_langgraph_team

        team = await run_langgraph_team(
            self, text, facts, intent, event_sink=event_sink
        )
        return (
            team.answer,
            team.terminal,
            team.sub_results,
            team.facts.get("citation_ids", []),
            team.facts,
            team.approval_id,
        )

    # ------------------------------------------------------------------
    # the loop
    # ------------------------------------------------------------------
    async def _agent_loop(
        self, spec: AgentSpec, task: str, user_text: str, facts: dict, intent: str
    ):
        executed: list[str] = []
        approval_id = ""

        while True:
            stop = self.budget.exceeded(
                self.steps, self.router.total_tokens_in + self.router.total_tokens_out
            )
            if stop:
                return (
                    self._partial_answer(facts, spec),
                    TerminalState.BUDGET_EXCEEDED,
                    facts,
                    approval_id,
                )

            action = await self._decide(spec, task, user_text, facts, intent, executed=executed)
            self.steps += 1
            step_row = self._record_step(spec.name, action)

            if action.type == "tool":
                tool_name = action.tool or ""
                if tool_name not in spec.tools:
                    # Capability violation. Recorded, refused, and the agent is
                    # told plainly so it can re-plan rather than loop.
                    with span("agent.violation", reason="tool_out_of_scope",
                              **{"agent.name": spec.name, "tool.name": tool_name}):
                        pass
                    executed.append(tool_name)
                    facts.setdefault("_violations", []).append(tool_name)
                    continue

                hitl_ctx = {
                    "order_total_inr": facts.get("order_total_inr", 0),
                    "reason": "value above auto-approval threshold",
                    "checkpoint": {"facts": facts, "executed": executed, "agent": spec.name},
                }
                result = await self.gateway.call(tool_name, action.arguments, hitl_context=hitl_ctx)
                self._record_tool(step_row, result, action)
                executed.append(tool_name)

                if result.status == "suspended":
                    approval_id = result.data.get("approval_id", "")
                    return (
                        f"That action needs a human to approve it. I've raised "
                        f"reference {approval_id} and someone will pick it up.",
                        TerminalState.AWAITING_APPROVAL,
                        facts,
                        approval_id,
                    )
                if result.ok:
                    facts = absorb(facts, tool_name, result.data)
                continue

            if action.type == "retrieve":
                from app.rag.retrieve import Retriever

                hits = await Retriever(self.session).search(
                    action.arguments.get("query", task), min_trust="trusted"
                )
                facts["chunks"] = [
                    {"id": h.chunk_id, "document_id": h.document_id, "content": h.content,
                     "heading": h.heading, "trust": h.trust, "score": h.rerank_score}
                    for h in hits
                ]
                facts["citation_ids"] = [c["id"] for c in facts["chunks"]]
                continue

            if action.is_terminal():
                return action.content, _terminal_for(action.type), facts, approval_id

            return self._partial_answer(facts, spec), TerminalState.PARTIAL, facts, approval_id

    # ------------------------------------------------------------------
    async def _decide(
        self,
        spec: AgentSpec,
        task: str,
        user_text: str,
        facts: dict,
        intent: str,
        executed: Optional[list[str]] = None,
        extra_meta: Optional[dict] = None,
    ) -> AgentAction:
        system = get_prompt("agent_system", self.prompt_version).replace("{{ROLE}}", spec.role)
        context = Context()
        context.add(Provenance.SYSTEM, system)
        def _format_tool(t: dict) -> str:
            params = t.get("parameters", {}).get("properties", {})
            required = t.get("parameters", {}).get("required", [])
            param_strs = []
            for p, info in params.items():
                typ = info.get("type", "any")
                req = "(required)" if p in required else "(optional)"
                param_strs.append(f"{p}: {typ} {req}")
            params_text = ", ".join(param_strs) if param_strs else "none"
            return f"- {t['name']}({params_text}): {t['description']}"

        context.add(
            Provenance.SYSTEM,
            "Tools available to you:\n"
            + "\n".join(_format_tool(t) for t in registry.specs(list(spec.tools)))
            if spec.tools
            else "You have no tools. Delegate to a specialist.",
        )
        # Tell the model the customer identity for tool calls
        if self.principal.customer_id:
            context.add(
                Provenance.SYSTEM,
                f"The current customer is {self.principal.customer_id}. "
                "Always pass this as customer_id when a tool requires it.",
            )
        await self.memory.as_fragments(context)
        for c in (facts.get("chunks") or [])[:4]:
            context.add(Provenance.RETRIEVED, f"{c['heading']}\n{c['content']}", source_id=c["id"])
        for tr in self.gateway.calls[-4:]:
            context.add(
                Provenance.TOOL_OUTPUT,
                summarise_result(tr.tool, tr),
                source_id=tr.tool,
            )
        context.add(Provenance.USER, task if task == user_text else f"{user_text}\n\nYour sub-task: {task}")

        meta = {
            "agent": spec.name,
            "user_text": user_text,
            "task": task,
            "intent": intent,
            "executed_tools": executed or [],
            "available_tools": list(spec.tools),
            "facts": facts,
            "chunks": facts.get("chunks") or [],
            "citation_ids": facts.get("citation_ids") or [],
            "customer_id": self.principal.customer_id or "",
            "system": system,
        }
        meta.update(extra_meta or {})
        return await self.router.decide(
            CompletionRequest(prompt=context.render(), system=system, purpose="agent_step", meta=meta)
        )

    # ------------------------------------------------------------------
    def _partial_answer(self, facts: dict, spec: AgentSpec) -> str:
        known = []
        if facts.get("order_id"):
            known.append(f"order {facts['order_id']} ({facts.get('order_status', 'status unknown')})")
        if facts.get("shipment_status"):
            known.append(f"shipment {facts['shipment_status']}")
        if facts.get("candidate_titles"):
            known.append("candidates: " + ", ".join(facts["candidate_titles"][:3]))
        detail = "; ".join(known)
        return (
            "I ran out of budget before finishing that. Here's what I established so far: "
            f"{detail}." if detail else
            "I ran out of budget before I could establish anything useful. Please try again."
        )

    def _record_step(self, agent_name: str, action: AgentAction) -> AgentStep:
        row = AgentStep(
            id=new_id("STP"),
            run_id=self.run_id,
            ordinal=self.steps,
            agent_name=agent_name,
            action_type=action.type,
            reasoning_summary=action.reasoning[:800],
        )
        self._step_rows.append(row)
        with span("agent.step", **{"agent.name": agent_name, "action.type": action.type,
                                  "action.tool": action.tool or "", "action.agent": action.agent or ""}):
            pass
        return row

    def _record_tool(self, step: AgentStep, result: ToolResult, action: AgentAction) -> None:
        self._tool_rows.append(
            ToolCall(
                id=new_id("TC"),
                run_id=self.run_id,
                step_id=step.id,
                tool_name=result.tool,
                arguments=action.arguments,
                result=result.data if result.ok else {"error": result.denial_reason},
                status=result.status,
                gateway_verdict=result.verdict,
                denial_reason=result.denial_reason,
                retries=result.retries,
                latency_ms=result.latency_ms,
            )
        )

    # ------------------------------------------------------------------
    async def _finish(
        self,
        answer: str,
        terminal: TerminalState,
        mode: str,
        entry_agent: str,
        trace: Trace,
        started: float,
        conversation_id: str,
        user_text: str,
        sub_results: Optional[list] = None,
        citations: Optional[list] = None,
        facts: Optional[dict] = None,
        approval_id: str = "",
        guardrail_triggered: Optional[list] = None,
        groundedness: Optional[float] = None,
    ) -> RunResult:
        latency_ms = int((time.perf_counter() - started) * 1000)
        result = RunResult(
            answer=answer,
            terminal_state=terminal.value,
            mode=mode,
            entry_agent=entry_agent,
            steps=self.steps,
            trajectory=trace.tool_names(),
            attempted_trajectory=trace.attempted_tool_names(),
            citations=citations or [],
            sub_results=[s.__dict__ if hasattr(s, "__dict__") else s for s in (sub_results or [])],
            facts=facts or {},
            trace_id=trace.trace_id,
            run_id=self.run_id,
            approval_id=approval_id,
            tokens_in=self.router.total_tokens_in,
            tokens_out=self.router.total_tokens_out,
            cost_inr=round(self.router.total_cost_inr, 6),
            latency_ms=latency_ms,
            guardrail_triggered=guardrail_triggered or [],
            groundedness=groundedness,
        )
        if self.persist:
            await self._persist(result, trace, conversation_id, user_text)
        return result

    async def _persist(self, result: RunResult, trace: Trace, conversation_id: str, user_text: str) -> None:
        self.session.add(
            AgentRun(
                id=self.run_id,
                conversation_id=conversation_id,
                message_id="",
                customer_id=self.principal.customer_id or "",
                mode=result.mode,
                entry_agent=result.entry_agent,
                terminal_state=result.terminal_state,
                steps=result.steps,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_inr=result.cost_inr,
                latency_ms=result.latency_ms,
                trace_id=result.trace_id,
                prompt_version=self.prompt_version,
                model_config_name=self.router.model_name,
            )
        )
        for row in self._step_rows:
            self.session.add(row)
        for row in self._tool_rows:
            self.session.add(row)
        for sp in trace.find("rag.retrieve"):
            self.session.add(
                Retrieval(
                    id=new_id("RTV"),
                    run_id=self.run_id,
                    query=str(sp.attributes.get("query", ""))[:1000],
                    strategy=str(sp.attributes.get("strategy", "hybrid")),
                    chunk_ids=sp.attributes.get("chunk_ids", []),
                    scores=[],
                    reranked_ids=sp.attributes.get("reranked_ids", []),
                    dropped_untrusted=sp.attributes.get("dropped_untrusted", []),
                    latency_ms=int(sp.attributes.get("latency_ms", 0) or 0),
                )
            )
        for sp in trace.find("guardrail.check"):
            if sp.attributes.get("verdict") in (None, "allow"):
                continue
            self.session.add(
                GuardrailEvent(
                    id=new_id("GRD"),
                    run_id=self.run_id,
                    chain=str(sp.attributes.get("chain", "")),
                    check_name=str(sp.attributes.get("check", "")),
                    verdict=str(sp.attributes.get("verdict", "")),
                    reason=str(sp.attributes.get("reason", ""))[:400],
                )
            )
        for cid in result.citations:
            self.session.add(
                Citation(
                    id=new_id("CIT"),
                    run_id=self.run_id,
                    chunk_id=cid,
                    document_id=cid.rsplit("-c", 1)[0] if "-c" in cid else cid,
                    validated=cid in result.answer,
                )
            )
        await self.session.flush()

    # ------------------------------------------------------------------
    async def learn(self, user_text: str, conversation_id: str = "") -> list:
        """Post-turn memory extraction. Separate from the run on purpose:
        a compromised agent cannot poison future sessions."""
        facts = await MemoryExtractor(self.router).extract(user_text)
        return await self.memory.write(facts, conversation_id=conversation_id)


def _terminal_for(action_type: str) -> TerminalState:
    return {
        "answer": TerminalState.COMPLETED,
        "refuse": TerminalState.REFUSED,
        "escalate": TerminalState.ESCALATED,
    }.get(action_type, TerminalState.PARTIAL)
