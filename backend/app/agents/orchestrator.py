"""Agent Orchestrator: mode routing, the bounded loop, delegation.

Four modes share one runtime. Mode is a routing decision plus a capability
set, not four codebases -- otherwise you cannot run the same dataset through
all four and compare them, which is the entire point of building this.

The loop is bounded on three axes (steps, tokens, wall clock) and terminates
into one of six states. Budget exhaustion returns `partial` with whatever was
learned, never a crash and never an empty answer.
"""
from __future__ import annotations

import asyncio
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
    if any(k in text for k in ("pending order", "approve order", "reject order", "shop owner", "seller", "restock", "low stock", "inventory")):
        intent = "admin_ops"
    if any(k in text for k in (
        "place order", "place an order", "buy now", "checkout", "create order", "want to buy",
        "i want to order", "buy this", "buy the", "purchase this", "pay with", "payment method",
        "payment option", "how do i pay", "cash on delivery", "order now",
    )):
        intent = "checkout_help"
    if any(k in text for k in ("return policy", "warranty", "shipping policy", "exchange policy")):
        intent = "policy"
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
        facts["candidate_prices"] = [p.get("price_inr") for p in products]
        facts["candidates"] = products
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
    elif tool == "create_order":
        facts["order_id"] = data.get("order_id", facts.get("order_id"))
        facts["order_status"] = data.get("status")
        facts["order_total_inr"] = data.get("total_inr")
        facts["payment_method"] = data.get("payment_method")
        facts["payment_id"] = data.get("payment_id")
        facts["placed_items"] = data.get("items") or []
    elif tool == "list_payment_methods":
        facts["payment_methods"] = data.get("methods") or []
        facts["payment_default"] = data.get("default")
    elif tool == "get_payment":
        facts["payment_id"] = data.get("payment_id")
        facts["payment_method"] = data.get("method")
        facts["payment_status"] = data.get("status")
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
    elif tool == "check_inventory":
        facts["product_id"] = data.get("product_id", facts.get("product_id"))
        facts["available"] = data.get("available")
        facts["in_stock"] = data.get("in_stock")
    elif tool == "list_low_stock":
        facts["low_stock"] = data.get("items") or []
        facts["low_stock_count"] = data.get("count")
    elif tool == "get_pending_orders":
        facts["pending_orders"] = data.get("orders") or []
        facts["pending_count"] = data.get("count")
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
        persona: str = "customer",
        product_id: str = "",
    ):
        from app.agents.teams import resolve_persona

        s = get_settings()
        self.session = session
        self.principal = principal
        self.router = router or LLMRouter(prompt_version=prompt_version)
        self.prompt_version = prompt_version
        self.persona = resolve_persona(persona, principal.kind)
        self.product_id = product_id or ""
        self.budget = budget or Budget(
            max_steps=s.max_steps,
            max_tokens=s.max_tokens_per_run,
            max_wall_clock_s=s.max_wall_clock_s,
        )
        self.persist = persist
        self.run_id = new_id("RUN")
        self.conversation_id = ""
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
        self.conversation_id = conversation_id or ""

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
            from app.nlp.understand import understand

            parsed = understand(clean_text)
            route_text = parsed.rewritten or clean_text
            resolved_mode, intent = classify_mode(route_text, mode)

            with span("conversation.route", mode=resolved_mode, intent=intent, persona=self.persona):
                facts = await self.memory.facts_dict()
            facts["persona"] = self.persona
            facts["understood_query"] = route_text
            facts["original_query"] = inbound.text
            facts["intent"] = intent
            if parsed.changed:
                facts["query_rewritten"] = True
            if self.product_id:
                facts["product_id"] = self.product_id
                if "[Product context" not in clean_text:
                    clean_text = f"[Product context id={self.product_id}] {clean_text}"
                    route_text = f"[Product context id={self.product_id}] {route_text}"
            if self.persona == "owner" and "[Shop owner" not in clean_text:
                clean_text = f"[Shop owner dashboard] {clean_text}"
                route_text = f"[Shop owner dashboard] {route_text}"
            facts = await self._prime_customer(facts)
            policyish = intent == "policy" or any(
                k in (route_text or "").lower()
                for k in ("policy", "warranty", "return window", "exchange policy")
            )
            if policyish or self.persona == "owner":
                facts = await self._prime_rag(route_text, facts)

            # Shop chat always asks for multi_agent; still run dedicated RAG
            # when the question is a policy lookup so answers stay cited.
            if resolved_mode == "multi_agent" and intent == "policy" and self.persona != "owner":
                rag_answer, rag_term, _, citations, rag_facts, _ = await self._run_rag(
                    route_text, facts, intent
                )
                facts = rag_facts
                # Continue multi-agent so shopping+policy compound questions
                # still get a specialist, but keep RAG chunks in context.
                if not any(k in clean_text.lower() for k in ("and", "also", "compare", "order", "refund", "buy")):
                    answer, terminal, sub_results, citations, out_facts, approval_id = (
                        rag_answer, rag_term, [], citations, rag_facts, ""
                    )
                    out_ctx = {
                        "retrieved_chunk_ids": [c["id"] for c in (out_facts.get("chunks") or [])],
                        "retrieved_texts": [c["content"] for c in (out_facts.get("chunks") or [])],
                        "requires_grounding": True,
                    }
                    with span("guardrail.output"):
                        outbound = run_output_chain(answer, out_ctx)
                    if outbound.verdict is Verdict.REFUSE:
                        answer, terminal = REFUSAL_MESSAGE, TerminalState.REFUSED
                    else:
                        answer = outbound.text
                    return await self._finish(
                        answer, terminal, "rag", "policy", trace, started,
                        conversation_id, user_text, sub_results=sub_results,
                        citations=citations, facts=out_facts, approval_id=approval_id,
                        guardrail_triggered=inbound.triggered() + outbound.triggered(),
                        groundedness=out_ctx.get("groundedness_score"),
                    )

            # 2 -- dispatch
            # The storefront always sends multi_agent. Only true greetings skip
            # the specialist graph — typed product/order asks must still run tools.
            simple_orders = await self._guided_my_orders(route_text, facts, intent, event_sink)
            if simple_orders:
                result = simple_orders
            elif self._is_greeting(clean_text) and self.persona != "owner":
                result = await self._run_chat(clean_text, facts)
            elif resolved_mode == "chat":
                result = await self._run_chat(clean_text, facts)
            elif resolved_mode == "rag":
                result = await self._run_rag(route_text, facts, intent)
            elif resolved_mode == "multi_agent":
                result = await self._run_multi(route_text, facts, intent, event_sink=event_sink)
            else:
                result = await self._run_single(route_text, facts, intent)

            answer, terminal, sub_results, citations, out_facts, approval_id = result
            out_facts = out_facts or {}
            out_facts.setdefault("intent", intent)
            out_facts.setdefault("logged_in", facts.get("logged_in"))

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
        context.add(
            Provenance.SYSTEM,
            "The shopper may misspell, skip letters, or mix Hindi/English. "
            "Infer what they meant and reply naturally, like ChatGPT — not a keyword bot. "
            "Always reply in English only. Never use Hindi or Hinglish in your answer. "
            "If they asked about products, offer to search the catalogue.",
        )
        if facts.get("logged_in"):
            context.add(
                Provenance.SYSTEM,
                "This shopper is already signed in. Never ask them to sign in. "
                "If they want their orders or payment status, tell them you can look that up "
                "(the order specialist handles it on the next turn if you cannot).",
            )
        else:
            context.add(
                Provenance.SYSTEM,
                "This shopper is a guest. Catalogue search is fine. If they ask for THEIR orders, "
                "tell them in English that they need to sign in.",
            )
        if facts.get("understood_query") and facts.get("query_rewritten"):
            context.add(
                Provenance.SYSTEM,
                f"Normalized reading of their message: {facts['understood_query']}",
            )
        await self.memory.as_fragments(context)
        context.add(Provenance.USER, text)
        fallback = (
            "I can search the ShopZone catalogue, check return or warranty policy, "
            "and — after you sign in — show your orders or place one with UPI, Card, or COD. "
            "What would you like to do?"
            if not facts.get("logged_in")
            else
            "I can search the catalogue, check policy, list your orders, or place an item "
            "after you pick UPI, Card, or Cash on delivery. What should I do next?"
        )
        try:
            completion = await asyncio.wait_for(
                self.router.complete(
                    CompletionRequest(
                        prompt=context.render(),
                        system=get_prompt("chat_system", self.prompt_version),
                        purpose="chat",
                        meta={"user_text": text, "facts": facts},
                    )
                ),
                timeout=8,
            )
            answer = (completion.text or "").strip() or fallback
        except Exception:  # noqa: BLE001 — live smalltalk must not hang the widget
            answer = fallback
        self.steps += 1
        return answer, TerminalState.COMPLETED, [], [], facts, ""

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

        fallback = ""
        if chunks:
            top = chunks[0]
            fallback = f"{(top.get('content') or '').strip()[:520]} [{top.get('id')}]"
        else:
            fallback = (
                "I don't have a policy document that covers that, so I won't guess. "
                "I can raise a support ticket if you'd like a human to confirm."
            )

        s = get_settings()
        answer = ""
        try:
            complete = self.router.complete(
                CompletionRequest(
                    prompt=context.render(),
                    system=get_prompt("rag_system", self.prompt_version),
                    purpose="rag_answer",
                    meta={"user_text": text, "chunks": chunks},
                )
            )
            if s.llm_provider == "mock" or s.cassette_mode == "replay":
                completion = await complete
            else:
                completion = await asyncio.wait_for(complete, timeout=8)
            answer = (completion.text or "").strip()
        except Exception:  # noqa: BLE001 — live RAG must not hang the shop chat
            answer = ""
        if not answer:
            answer = fallback
        self.steps += 1
        terminal = TerminalState.COMPLETED if chunks else TerminalState.PARTIAL
        return answer, terminal, [], facts["citation_ids"], facts, ""

    async def _prime_customer(self, facts: dict) -> dict:
        """Mark login state. Order rows are fetched by the order agent on demand
        so priming cannot pollute evaluated trajectories."""
        cid = self.principal.customer_id
        logged = self.principal.kind == "customer" and bool(cid)
        facts["logged_in"] = logged
        if cid:
            facts["customer_id"] = cid
        return facts

    async def _recent_turns(self, limit: int = 8) -> list[tuple[str, str]]:
        if not self.conversation_id:
            return []
        from sqlalchemy import select

        from app.models import Message

        rows = (
            await self.session.execute(
                select(Message)
                .where(Message.conversation_id == self.conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [(m.role, m.content) for m in reversed(list(rows))]

    async def _prime_rag(self, text: str, facts: dict) -> dict:
        """Always retrieve trusted policy (+ product KB) so every agentic turn is grounded."""
        from app.rag.retrieve import Retriever

        try:
            retriever = Retriever(self.session)
            trust = "trusted"
            families = None
            if self.persona == "product":
                families = None
            hits = await retriever.search(text, min_trust=trust, families=families)
            if not hits and self.persona in {"product", "customer"}:
                hits = await retriever.search(text, min_trust="untrusted")
            chunks = [
                {
                    "id": h.chunk_id,
                    "document_id": h.document_id,
                    "content": h.content,
                    "heading": h.heading,
                    "trust": h.trust,
                    "score": h.rerank_score,
                }
                for h in hits[:6]
            ]
            if chunks:
                facts["chunks"] = chunks
                facts["citation_ids"] = [c["id"] for c in chunks]
        except Exception:  # noqa: BLE001 — retrieval miss must not kill chat
            pass
        return facts

    async def _run_single(self, text: str, facts: dict, intent: str, spec_name: str = "single"):
        spec = AGENTS[spec_name]
        answer, terminal, facts, approval_id = await self._agent_loop(spec, text, text, facts, intent)
        return answer, terminal, [], facts.get("citation_ids", []), facts, approval_id

    def _is_catalogue_browse(self, text: str, intent: str) -> bool:
        """Guest/customer browse questions should hit search_products, not a live ReAct hang."""
        if self.persona == "owner":
            return False
        low = (text or "").lower()
        if any(
            k in low
            for k in (
                "my order",
                "place order",
                "buy now",
                "buy this",
                "checkout",
                "create order",
                "refund",
                "cancel my",
                "approve",
                "reject",
                "return this",
                "payment method",
                "how do i pay",
            )
        ):
            return False
        if intent in {
            "order_delay_refund",
            "return_item",
            "cancel_order",
            "refund_status",
            "order_status",
            "order_list",
            "checkout_help",
            "admin_ops",
            "policy",
        }:
            return False
        if intent in {"shopping", "gift_advice", "shopping_complex", "promotion", "smalltalk"}:
            if intent == "smalltalk" and self._is_greeting(text):
                return False
            if intent in {"shopping", "gift_advice", "shopping_complex", "promotion"}:
                return True
        return any(
            k in low
            for k in (
                "search",
                "price",
                "find me",
                "show me",
                "looking for",
                "looking to",
                "dikhao",
                "compare",
                "laptop",
                "phone",
                "iphone",
                "macbook",
                "monitor",
                "headphone",
                "earbud",
                "airpod",
                "tablet",
                "mouse",
                "keyboard",
                "speaker",
                "camera",
                "watch",
                "tv",
                "cheap",
                "under ",
                "available",
                "in stock",
                "i need",
                "i want",
                "any good",
            )
        )

    @staticmethod
    def _is_greeting(text: str) -> bool:
        low = (text or "").strip().lower().strip("!?. ")
        if not low or len(low) > 90:
            return False
        exact = {
            "hi", "hey", "hello", "yo", "hi there", "hello there", "hey there",
            "thanks", "thank you", "ok", "okay", "help", "namaste",
        }
        if low in exact:
            return True
        return low.startswith((
            "hi,", "hey,", "hello,", "hi ", "hey ", "hello ",
            "what can you", "who are you", "good morning", "good evening",
            "good afternoon", "how can you help",
        ))

    def _format_catalogue_answer(self, products: list, facts: dict, text: str) -> str:
        understood = (facts.get("understood_query") or text or "").strip()
        lead = "Here’s what I found in the ShopZone catalogue"
        if facts.get("query_rewritten") and understood:
            lead = f"I read that as “{understood}”. {lead}"
        if not products:
            return (
                f"{lead}, but nothing matched closely. "
                "Try a brand or category such as iPhone, Galaxy, MacBook, or headphones — "
                "typos are fine."
            )
        lines = [f"{lead}:"]
        for p in products:
            price = int(p.get("price_inr") or 0)
            rating = p.get("rating")
            brand = p.get("brand") or ""
            ram = (p.get("attributes") or {}).get("ram_gb")
            ram_bit = f" · {ram}GB RAM" if ram else ""
            lines.append(f"• {p.get('title')} ({brand}) — ₹{price:,} · rating {rating}{ram_bit}")
        if not facts.get("logged_in"):
            lines.append(
                "\nWant one of these? Sign in and tell me UPI, Card, or Cash on delivery and I’ll place it."
            )
        else:
            lines.append("\nTell me which one to order and how you’d like to pay (UPI, Card, or COD).")
        return "\n".join(lines)

    async def _compose_catalogue_reply(self, text: str, products: list, facts: dict) -> str:
        """Catalogue list from tools. Live polish used to hang shop chat on Vercel."""
        return self._format_catalogue_answer(products, facts, text)

    async def _notify(self, event_sink, payload: dict) -> None:
        if not event_sink:
            return
        out = event_sink(payload)
        if hasattr(out, "__await__"):
            await out

    async def _fast_catalogue_browse(self, text: str, facts: dict, intent: str, event_sink=None):
        """Answer catalogue/price searches without LangGraph so guests get ₹ prices quickly."""
        if not self._is_catalogue_browse(text, intent):
            return None
        from app.llm.mock import (
            extract_budget_inr,
            extract_category,
            extract_min_ram_gb,
            extract_min_rating,
            wants_comparison,
        )

        await self._notify(
            event_sink,
            {
                "type": "agent_start",
                "agent": "shopping",
                "task": "catalogue browse",
                "framework": "catalogue",
            },
        )
        args: dict = {"query": text, "limit": 6}
        category = extract_category(text)
        if category:
            args["category"] = category
        budget = extract_budget_inr(text)
        if budget:
            args["max_price_inr"] = budget
        ram = extract_min_ram_gb(text)
        if ram:
            args["min_ram_gb"] = ram
        rating = extract_min_rating(text)
        if rating:
            args["min_rating"] = rating
        result = await self.gateway.call("search_products", args)
        if not result.ok:
            await self._notify(
                event_sink,
                {
                    "type": "agent_done",
                    "agent": "shopping",
                    "summary": result.denial_reason or "search failed",
                    "terminal_state": TerminalState.PARTIAL.value,
                    "framework": "catalogue",
                },
            )
            return None
        facts = absorb(facts, "search_products", result.data)
        products = result.data.get("products") or []
        tools_used = ["search_products"]
        extra = ""
        if wants_comparison(text) and len(products) >= 2:
            ids = [p["product_id"] for p in products[:4]]
            cmp_res = await self.gateway.call("compare_products", {"product_ids": ids})
            if cmp_res.ok:
                tools_used.append("compare_products")
                facts = absorb(facts, "compare_products", cmp_res.data)
                best = cmp_res.data.get("best_rated_product_id")
                cheap = cmp_res.data.get("cheapest_product_id")
                extra = f"\nBest rated: {best}. Cheapest: {cheap}."
        answer = await self._compose_catalogue_reply(text, products, facts)
        if extra:
            answer = answer + extra
        await self._notify(
            event_sink,
            {
                "type": "agent_done",
                "agent": "shopping",
                "summary": answer[:400],
                "terminal_state": TerminalState.COMPLETED.value,
                "framework": "catalogue",
            },
        )
        sub = [
            SubResult(
                agent="shopping",
                task="catalogue browse",
                summary=answer,
                steps=max(self.steps, 1),
                tools=tools_used,
                terminal_state=TerminalState.COMPLETED.value,
            ).__dict__
        ]
        return answer, TerminalState.COMPLETED, sub, facts.get("citation_ids", []), facts, ""

    def _followups(self, intent: str, facts: dict, answer: str) -> list[str]:
        """Chips that continue this conversation, not a static help menu."""
        if self.persona == "owner":
            return ["List pending orders", "Which products need restock?", "Summarize today’s order queue"]
        logged = bool(facts.get("logged_in"))
        titles = [t for t in (facts.get("candidate_titles") or []) if t][:3]
        pick = titles[0] if titles else ""
        placed = bool(facts.get("order_id")) and "placed" in (answer or "").lower()
        if placed:
            return ["Show my recent orders", "What is the return policy?", "Track my latest order"]
        if intent == "checkout_help" or facts.get("checkout_stage"):
            stage = facts.get("checkout_stage") or ""
            if not logged or stage == "need_login":
                chips = ["Sign in to place an order", "What payment methods can I use?"]
                if pick:
                    chips.append(f"Search for {pick}")
                else:
                    chips.append("Search for iPhone and give me the prices")
                return chips
            if stage == "need_product" or (not facts.get("product_id") and not pick):
                return ["Buy iPhone 15 with UPI", "Laptops under 80000", "Show my recent orders"]
            if stage == "need_payment" or not facts.get("payment_method"):
                name = pick or "this"
                return [f"Pay for {name} with UPI", "Pay with Card", "Cash on delivery"]
            return ["Show my recent orders", "What is the return policy?", "Add another item"]
        if intent in {"shopping", "gift_advice", "shopping_complex"} and pick:
            buy = f"Buy {pick} with UPI" if logged else "Sign in to place an order"
            return [buy, f"What's the warranty on {pick}?", "Compare with similar products"]
        if intent in {"order_status", "order_list"}:
            if not logged:
                return ["Sign in to place an order", "Search for iPhone and give me the prices", "What is the return policy?"]
            return ["What payment was used on my latest order?", "Cancel my latest unshipped order", "What is the return policy?"]
        if intent == "policy":
            return ["Laptops under 80000", "Show my recent orders" if logged else "Sign in to place an order", "What payment methods can I use?"]
        if logged:
            return ["Show my recent orders", "Search for iPhone prices", "What payment methods can I use?"]
        return ["Search for iPhone and give me the prices", "Laptops under 80000 with 16GB RAM", "Sign in to place an order"]

    def _named_a_product(self, text: str) -> bool:
        from app.llm.mock import extract_category

        q = (text or "").lower()
        if extract_category(q):
            return True
        return any(
            w in q
            for w in (
                "iphone", "macbook", "galaxy", "pixel", "airpod", "laptop",
                "headphone", "monitor", "redmi", "this item", "this product", "this one",
            )
        )

    async def _title_from_history(self) -> str:
        import re as _re

        turns = await self._recent_turns(12)
        for role, content in reversed(turns):
            if role == "user" and self._named_a_product(content or ""):
                return content.strip()
        for role, content in reversed(turns):
            if role not in {"assistant", "system"}:
                continue
            m = _re.search(r"•\s*([^(\n—\-]+)", content or "")
            if m:
                title = m.group(1).strip()
                if 2 < len(title) < 80:
                    return title
        return ""

    def _is_simple_order_list(self, text: str, intent: str) -> bool:
        """True for 'check/show my orders' and 'track my latest' without a specific OR- id."""
        if self.persona == "owner":
            return False
        from app.llm.mock import extract_order_id

        low = (text or "").lower()
        if any(
            k in low
            for k in (
                "cancel", "refund", "promo", "discount", "coupon",
                "policy", "place order", "buy ", "shipment", "awb",
            )
        ):
            return False
        if "return" in low and "policy" not in low and intent != "order_list":
            if intent in {"return_item"}:
                return False
        oid = extract_order_id(text)
        if oid and ("where is" in low or "track" in low):
            return False
        if intent in {"order_list", "order_status"} and not oid:
            return True
        return any(
            k in low
            for k in (
                "check order", "check orders", "show order", "show orders",
                "see order", "see orders", "list order", "my orders",
                "order history", "all my order", "irders", "track my",
                "latest order", "my latest", "existing order", "order details",
                "order detail", "order detils", "my existing", "check my order",
            )
        )

    async def _guided_my_orders(self, text: str, facts: dict, intent: str, event_sink=None):
        """Logged-in order list without a live-model detour that asks guests to sign in."""
        if not self._is_simple_order_list(text, intent):
            return None

        await self._notify(
            event_sink,
            {"type": "agent_start", "agent": "order", "task": "list orders", "framework": "order-ops"},
        )
        facts["intent"] = "order_list"
        cid = self.principal.customer_id or ""
        logged = bool(facts.get("logged_in")) and bool(cid)

        if not logged:
            answer = (
                "You're chatting as a guest, so I can't see orders on an account yet. "
                "Sign in and ask again — I'll list your orders and payment status."
            )
            await self._notify(
                event_sink,
                {
                    "type": "agent_done",
                    "agent": "order",
                    "summary": answer[:400],
                    "terminal_state": TerminalState.COMPLETED.value,
                    "framework": "order-ops",
                },
            )
            sub = [
                SubResult(
                    agent="order",
                    task="need sign-in",
                    summary=answer,
                    steps=max(self.steps, 1),
                    tools=[],
                    terminal_state=TerminalState.COMPLETED.value,
                ).__dict__
            ]
            return answer, TerminalState.COMPLETED, sub, [], facts, ""

        result = await self.gateway.call("get_orders", {"customer_id": cid, "limit": 8})
        tools = ["get_orders"]
        if result.ok:
            facts = absorb(facts, "get_orders", result.data)
            orders = result.data.get("orders") or []
            if not orders:
                answer = "I don't see any orders on your ShopZone account yet. Browse the store and I can place one after you pick UPI, Card, or Cash on delivery."
            else:
                lines = [f"Here are your recent ShopZone orders ({result.data.get('count', len(orders))}):"]
                for o in orders[:8]:
                    items = o.get("items") or []
                    titles = ", ".join(
                        f"{i.get('title') or i.get('product_id')} ×{i.get('qty') or 1}"
                        for i in items[:3]
                    ) or "items"
                    total = int(o.get("total_inr") or 0)
                    lines.append(
                        f"• {o.get('order_id')} — {o.get('status')} — ₹{total:,} — {titles}"
                    )
                lines.append("Ask me about payment, tracking, or a return on any of these.")
                answer = "\n".join(lines)
        else:
            answer = result.denial_reason or "I couldn't load your orders just now. Try again in a moment."

        await self._notify(
            event_sink,
            {
                "type": "agent_done",
                "agent": "order",
                "summary": answer[:400],
                "terminal_state": TerminalState.COMPLETED.value,
                "framework": "order-ops",
            },
        )
        sub = [
            SubResult(
                agent="order",
                task="list orders",
                summary=answer,
                steps=max(self.steps, 1),
                tools=tools,
                terminal_state=TerminalState.COMPLETED.value,
            ).__dict__
        ]
        return answer, TerminalState.COMPLETED, sub, facts.get("citation_ids", []), facts, ""

    async def _guided_checkout(self, text: str, facts: dict, intent: str, event_sink=None):
        """Checkout specialist without LangGraph: collect item + payment, then place."""
        if self.persona == "owner":
            return None
        low = (text or "").lower()
        looks = intent == "checkout_help" or any(
            k in low
            for k in (
                "place order", "place an order", "buy now", "checkout",
                "i want to buy", "i want to order", "pay with", "order now", "buy this",
            )
        )
        if not looks:
            return None
        from app.llm.mock import extract_payment_method, extract_category

        await self._notify(
            event_sink,
            {"type": "agent_start", "agent": "checkout", "task": "collect details and place order", "framework": "checkout"},
        )
        pay = extract_payment_method(text) or facts.get("payment_method")
        if pay:
            facts["payment_method"] = pay

        pid = facts.get("product_id") or self.product_id or None
        if pid:
            facts["product_id"] = pid

        search_q = text
        for filler in (
            "i want to place an order for", "i want to place order", "i want to buy",
            "i want to order", "place an order for", "place an order", "place order",
            "buy now", "checkout", "order now", "pay with upi", "pay with card",
            "cash on delivery", "with upi", "with card", "please",
        ):
            search_q = search_q.replace(filler, " ")
        search_q = " ".join(search_q.split())

        products = list(facts.get("candidates") or [])
        if not pid and (self._named_a_product(search_q) or self._named_a_product(text)):
            args: dict = {"query": search_q or text, "limit": 5}
            cat = extract_category(search_q or text)
            if cat:
                args["category"] = cat
            result = await self.gateway.call("search_products", args)
            if result.ok:
                facts = absorb(facts, "search_products", result.data)
                products = result.data.get("products") or []
                if products:
                    pid = products[0]["product_id"]
                    facts["product_id"] = pid
        if not pid:
            hist = await self._title_from_history()
            if hist:
                result = await self.gateway.call("search_products", {"query": hist, "limit": 3})
                if result.ok:
                    facts = absorb(facts, "search_products", result.data)
                    products = result.data.get("products") or products
                    if products:
                        pid = products[0]["product_id"]
                        facts["product_id"] = pid

        methods = await self.gateway.call("list_payment_methods", {})
        if methods.ok:
            facts = absorb(facts, "list_payment_methods", methods.data)
        labels = ", ".join(
            m.get("label") or m.get("id") for m in (facts.get("payment_methods") or [])
        ) or "UPI, Card, Cash on delivery"

        title = None
        price = None
        if pid:
            got = await self.gateway.call("get_product", {"product_id": pid})
            if got.ok:
                title = got.data.get("title")
                price = got.data.get("price_inr")
                facts.setdefault("candidate_titles", [title] if title else [])
        if not title and (facts.get("candidate_titles") or []):
            title = facts["candidate_titles"][0]
            prices = facts.get("candidate_prices") or []
            price = prices[0] if prices else None

        logged = bool(facts.get("logged_in")) and bool(self.principal.customer_id)

        if not logged:
            facts["checkout_stage"] = "need_login"
            bits = ["I can place this for you — sign in first so the order goes on your account."]
            if title:
                bits.append(f"Item: {title}" + (f" — ₹{int(price):,}" if price else "") + ".")
            elif products:
                bits.append("Matches I can order after you sign in:")
                for p in products[:4]:
                    bits.append(f"• {p.get('title')} — ₹{int(p.get('price_inr') or 0):,}")
            bits.append(f"Then tell me {labels}.")
            answer = "\n".join(bits)
        elif not pid:
            facts["checkout_stage"] = "need_product"
            answer = (
                f"I can place an order. Which item should I buy? "
                f"Name a product (iPhone 15, MacBook Air, Galaxy S24…) and how you’ll pay ({labels})."
            )
        elif not pay:
            facts["checkout_stage"] = "need_payment"
            line = title or "that item"
            if price is not None:
                line += f" — ₹{int(price):,}"
            answer = (
                f"• {line} is ready to order.\n"
                f"How would you like to pay: {labels}?\n"
                "Reply with UPI, Card, or Cash on delivery."
            )
        else:
            qty = 1
            import re as _re
            m = _re.search(r"\b([1-9]|10)\b", text or "")
            if m:
                qty = int(m.group(1))
            created = await self.gateway.call(
                "create_order",
                {
                    "customer_id": self.principal.customer_id,
                    "payment_method": pay,
                    "items": [{"product_id": pid, "qty": qty}],
                },
            )
            if created.ok:
                facts = absorb(facts, "create_order", created.data)
                facts["checkout_stage"] = "placed"
                answer = created.data.get("message") or (
                    f"Order {created.data.get('order_id')} placed. "
                    f"Total ₹{int(created.data.get('total_inr') or 0):,} via {pay}."
                )
                if title:
                    answer = f"Placed {title} (x{qty}). {answer}"
            else:
                facts["checkout_stage"] = "need_payment"
                answer = created.denial_reason or "I could not place that order. Try signing in again or pick UPI, Card, or COD."

        await self._notify(
            event_sink,
            {
                "type": "agent_done",
                "agent": "checkout",
                "summary": answer[:400],
                "terminal_state": TerminalState.COMPLETED.value,
                "framework": "checkout",
            },
        )
        sub = [
            SubResult(
                agent="checkout",
                task="collect details and place order",
                summary=answer,
                steps=max(self.steps, 1),
                tools=["search_products", "list_payment_methods"] + (["create_order"] if facts.get("order_id") else []),
                terminal_state=TerminalState.COMPLETED.value,
            ).__dict__
        ]
        return answer, TerminalState.COMPLETED, sub, facts.get("citation_ids", []), facts, ""

    async def _guided_owner_ops(self, text: str, facts: dict, intent: str, event_sink=None):
        """Seller dashboard agent: pending queue, approve/reject, low stock — no LangGraph hang."""
        if self.persona != "owner":
            return None
        import re as _re

        from app.llm.mock import extract_order_id

        low = (text or "").lower()
        want_add = bool(
            _re.search(r"\b(add|create|register)\b.{0,24}\bproducts?\b", low)
            or "new product" in low
        )
        looks = want_add or intent == "admin_ops" or any(
            k in low
            for k in (
                "pending order", "approve", "reject", "low stock", "restock",
                "inventory", "order queue", "packed", "seller", "stock",
            )
        )
        if not looks:
            return None

        if want_add:
            return await self._guided_add_product(text, low, facts, event_sink)

        await self._notify(
            event_sink,
            {"type": "agent_start", "agent": "admin", "task": "seller ops", "framework": "owner-ops"},
        )
        tools_used: list[str] = []
        lines: list[str] = []
        oid = extract_order_id(text) or extract_order_id(facts.get("original_query") or "")
        pid_m = _re.search(r"\bPR-[A-Za-z0-9]+\b", text or "", _re.I)
        pid = (pid_m.group(0).upper() if pid_m else "") or str(facts.get("product_id") or "")

        want_low = any(k in low for k in ("low stock", "restock", "need restock"))
        want_inv = "inventory" in low or "stock" in low
        want_approve = "approve" in low and "reject" not in low
        want_reject = "reject" in low
        want_pending = any(k in low for k in ("pending", "queue", "list order", "today's order", "todays order"))

        if pid and want_inv and not want_low:
            inv = await self.gateway.call("check_inventory", {"product_id": pid})
            tools_used.append("check_inventory")
            if inv.ok:
                lines.append(
                    f"{pid}: {inv.data.get('available')} available"
                    f"{' (in stock)' if inv.data.get('in_stock') else ' (out of stock)'}"
                )
                for wh in (inv.data.get("warehouses") or [])[:4]:
                    lines.append(f"• {wh.get('warehouse')}: {wh.get('available')}")
            else:
                lines.append(inv.denial_reason or f"Could not check {pid}.")
        elif want_low or (want_inv and not want_approve and not want_reject):
            stock = await self.gateway.call("list_low_stock", {"threshold": 20, "limit": 8})
            tools_used.append("list_low_stock")
            if stock.ok:
                items = stock.data.get("items") or []
                lines.append(f"Low stock (≤20): {stock.data.get('count', len(items))} SKUs.")
                for it in items[:6]:
                    lines.append(f"• {it.get('title')} ({it.get('product_id')}) — {it.get('available')} left")
                if not items:
                    lines.append("No SKUs are at or below the restock threshold.")

        target_id = oid
        if want_pending or want_approve or want_reject or not lines:
            pending = await self.gateway.call("get_pending_orders", {"status": "placed", "limit": 8})
            tools_used.append("get_pending_orders")
            if pending.ok:
                orders = pending.data.get("orders") or []
                lines.append(f"Pending placed orders: {pending.data.get('count', len(orders))}.")
                for o in orders[:6]:
                    lines.append(
                        f"• {o.get('order_id')} · {o.get('customer_id')} · ₹{int(o.get('total_inr') or 0):,}"
                    )
                if not orders:
                    lines.append("Queue is empty.")
                if not target_id and orders:
                    target_id = orders[0].get("order_id")

        if target_id and want_approve:
            ap = await self.gateway.call("approve_order", {"order_id": target_id})
            tools_used.append("approve_order")
            if ap.ok:
                lines.append(ap.data.get("message") or f"Approved {target_id}.")
            else:
                lines.append(ap.denial_reason or "Approve failed.")
        if target_id and want_reject:
            rj = await self.gateway.call(
                "reject_order",
                {"order_id": target_id, "reason": "seller_dashboard_reject"},
            )
            tools_used.append("reject_order")
            if rj.ok:
                lines.append(rj.data.get("message") or f"Rejected {target_id}.")
            else:
                lines.append(rj.denial_reason or "Reject failed.")

        answer = "\n".join(str(x) for x in lines if x) or (
            "Seller ops: ask me to list pending orders, approve/reject a specific OR- id, or show low stock."
        )
        facts["intent"] = "admin_ops"
        await self._notify(
            event_sink,
            {
                "type": "agent_done",
                "agent": "admin",
                "summary": answer[:400],
                "terminal_state": TerminalState.COMPLETED.value,
                "framework": "owner-ops",
            },
        )
        sub = [
            SubResult(
                agent="admin",
                task="seller ops",
                summary=answer,
                steps=max(self.steps, 1),
                tools=tools_used,
                terminal_state=TerminalState.COMPLETED.value,
            ).__dict__
        ]
        return answer, TerminalState.COMPLETED, sub, facts.get("citation_ids", []), facts, ""

    async def _guided_add_product(self, text: str, low: str, facts: dict, event_sink=None):
        """Owner adds a catalogue product from chat. Deterministic: parse the
        details, create it, or ask once for what's missing — never loop."""
        import re as _re

        await self._notify(
            event_sink,
            {"type": "agent_start", "agent": "admin", "task": "add product", "framework": "owner-ops"},
        )

        # The typo/English rewrite can lowercase `text`; the raw query keeps
        # the owner's capitalisation for the title and brand.
        raw = str(facts.get("original_query") or text or "")

        title = ""
        m = _re.search(r"[\"“']([^\"”']{2,80})[\"”']", raw)
        if m:
            title = m.group(1).strip()
        else:
            m = _re.search(r"\b(?:called|named|titled)\s+([A-Za-z0-9][\w\s+.-]{1,60}?)(?=\s+(?:price|for|at|category|brand|stock|qty)\b|$)", raw, _re.I)
            if m:
                title = m.group(1).strip()

        price = 0
        m = _re.search(r"(?:price|priced|for|at|@|₹|rs\.?|inr)\s*([0-9][0-9,]{1,8})", low)
        if m:
            price = int(m.group(1).replace(",", ""))

        category = next(
            (c for c in ("laptops", "laptop", "phones", "phone", "audio", "monitors", "monitor", "accessories", "accessory") if c in low),
            "",
        )
        category = {"laptop": "laptops", "phone": "phones", "monitor": "monitors", "accessory": "accessories"}.get(category, category)

        m = _re.search(r"\bbrand\s+([A-Za-z0-9][\w-]{0,24})", raw, _re.I)
        brand = m.group(1) if m else ""
        m = _re.search(r"\b(?:stock|qty|quantity|units?)\s+(\d{1,6})", low)
        stock = int(m.group(1)) if m else 0

        tools_used: list[str] = []
        if title and price > 0:
            args = {"title": title, "price_inr": price, "initial_stock": stock}
            if category:
                args["category"] = category
            if brand:
                args["brand"] = brand
            res = await self.gateway.call("add_product", args)
            tools_used.append("add_product")
            if res.ok:
                d = res.data
                answer = (
                    f"{d.get('message')}\n"
                    f"• id: {d.get('product_id')} · SKU {d.get('sku')}\n"
                    f"• category: {d.get('category')} · brand: {d.get('brand')}\n"
                    f"• opening stock: {d.get('initial_stock')} (say \"add stock for {d.get('product_id')}\" or use the dashboard to change it)"
                )
            else:
                answer = res.denial_reason or "I couldn't add that product. Check the SKU isn't already used."
        else:
            missing = []
            if not title:
                missing.append('a title (put it in quotes, e.g. "Pixel Buds 3")')
            if price <= 0:
                missing.append("a price in INR (e.g. price 12999)")
            answer = (
                "I can add that product — I just need " + " and ".join(missing) + ".\n"
                'Example: add new product "Pixel Buds 3" price 12999 category audio brand Google stock 50'
            )

        facts["intent"] = "admin_ops"
        await self._notify(
            event_sink,
            {
                "type": "agent_done",
                "agent": "admin",
                "summary": answer[:400],
                "terminal_state": TerminalState.COMPLETED.value,
                "framework": "owner-ops",
            },
        )
        sub = [
            SubResult(
                agent="admin",
                task="add product",
                summary=answer,
                steps=max(self.steps, 1),
                tools=tools_used,
                terminal_state=TerminalState.COMPLETED.value,
            ).__dict__
        ]
        return answer, TerminalState.COMPLETED, sub, facts.get("citation_ids", []), facts, ""

    async def _run_multi(self, text: str, facts: dict, intent: str, event_sink=None):
        """Multi-agent mode: live ReAct (real DB tools) when an LLM key is set.

        Matches the working LangChain shop agent: create_react_agent + SQLite tools.
        Mock/eval keeps catalogue/order short-circuits and the native loop.
        """
        from app.agents.react_runtime import live_react_enabled, run_react_shopper

        if live_react_enabled():
            try:
                live = await run_react_shopper(self, text, facts, event_sink)
                if live:
                    return live
            except Exception:  # noqa: BLE001 — fall back to deterministic specialists
                pass
        fast = await self._fast_catalogue_browse(text, facts, intent, event_sink)
        if fast:
            return fast
        guided = await self._guided_checkout(text, facts, intent, event_sink)
        if guided:
            return guided
        orders = await self._guided_my_orders(text, facts, intent, event_sink)
        if orders:
            return orders
        owner = await self._guided_owner_ops(text, facts, intent, event_sink)
        if owner:
            return owner
        from app.agents.langgraph_runtime import run_langgraph_team
        from app.agents.teams import specialists_for

        try:
            team = await asyncio.wait_for(
                run_langgraph_team(
                    self,
                    text,
                    facts,
                    intent,
                    event_sink=event_sink,
                    specialists=specialists_for(self.persona),
                    persona=self.persona,
                ),
                timeout=16,
            )
        except asyncio.TimeoutError:
            fallback = await self._fast_catalogue_browse(text, facts, "shopping", event_sink)
            if fallback:
                return fallback
            answer = (
                "I am still working through that. Try naming a product to search, "
                "asking for the return policy, or — if you are signed in — your recent orders."
            )
            return answer, TerminalState.PARTIAL, [], [], facts, ""
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
                f"The current customer is {self.principal.customer_id}"
                + (f" ({facts.get('customer_name')})" if facts.get("customer_name") else "")
                + ". Always pass this as customer_id when a tool requires it. "
                + ("They are logged in — never ask them to sign in; call get_orders/get_payment for their account." if facts.get("logged_in") else "They are a guest — do not create_order.")
                + " Write every customer-facing sentence in English.",
            )
            if facts.get("orders"):
                brief = "; ".join(
                    f"{o.get('order_id')} {o.get('status')} INR {o.get('total_inr')}"
                    for o in (facts.get("orders") or [])[:4]
                )
                context.add(Provenance.SYSTEM, "Recent orders on file: " + brief)
        else:
            context.add(
                Provenance.SYSTEM,
                "This shopper is not logged in. You may search the catalogue and list payment methods, "
                "but you must not call create_order or get_orders. Ask them to sign in.",
            )
        if extra_meta and extra_meta.get("supervisor_hint"):
            context.add(Provenance.SYSTEM, extra_meta["supervisor_hint"])
        if extra_meta and extra_meta.get("allowed_agents"):
            context.add(
                Provenance.SYSTEM,
                "You may only delegate to these specialists: "
                + ", ".join(extra_meta["allowed_agents"])
                + ". For policy/warranty/returns always include the policy specialist.",
            )
        await self.memory.as_fragments(context)
        for role, content in await self._recent_turns():
            if role == "user":
                context.add(Provenance.USER, content[:800])
            else:
                context.add(Provenance.SYSTEM, f"Prior assistant turn: {content[:800]}")
        for c in (facts.get("chunks") or [])[:4]:
            context.add(Provenance.RETRIEVED, f"{c['heading']}\n{c['content']}", source_id=c["id"])
        for tr in self.gateway.calls[-4:]:
            context.add(
                Provenance.TOOL_OUTPUT,
                summarise_result(tr.tool, tr),
                source_id=tr.tool,
            )
        context.add(Provenance.USER, task if task == user_text else f"{user_text}\n\nYour sub-task: {task}")
        if facts.get("understood_query") and facts.get("query_rewritten"):
            context.add(
                Provenance.SYSTEM,
                "The shopper may misspell or mix Hindi/English; still answer in English. Their intent reads as: "
                + str(facts.get("understood_query")),
            )

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

    async def _ensure_english_reply(self, answer: str) -> str:
        from app.nlp.understand import needs_english_rewrite

        if not needs_english_rewrite(answer):
            return answer
        s = get_settings()
        if s.llm_provider == "mock" or s.cassette_mode == "replay":
            return answer
        try:
            completion = await self.router.complete(
                CompletionRequest(
                    prompt=(
                        "Rewrite this ShopZone assistant reply in English only. "
                        "Keep every fact, price, product name, and order id. "
                        "No Hindi or Hinglish.\n\n"
                        + (answer or "")
                    ),
                    system="You translate shop assistant replies into English. Output only the rewritten reply.",
                    purpose="chat",
                    max_tokens=400,
                    temperature=0.0,
                )
            )
            text = (completion.text or "").strip()
            if text and not needs_english_rewrite(text):
                return text
        except Exception:  # noqa: BLE001 — keep original if rewrite fails
            pass
        return answer

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
        facts = facts or {}
        answer = await self._ensure_english_reply(answer)
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
            facts=facts,
            trace_id=trace.trace_id,
            run_id=self.run_id,
            approval_id=approval_id,
            tokens_in=self.router.total_tokens_in,
            tokens_out=self.router.total_tokens_out,
            cost_inr=round(self.router.total_cost_inr, 6),
            latency_ms=latency_ms,
            guardrail_triggered=guardrail_triggered or [],
            groundedness=groundedness,
            suggestions=self._followups(str(facts.get("intent") or ""), facts, answer),
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
