"""Deterministic mock LLM.

Two modes, and the second one is the point.

  guarded (default)
      A rule-based planner that reads only the USER envelope. Deterministic,
      offline, free. This is what CI runs, and what makes the agent suite fast
      enough to sit on every commit.

  naive  (MockLLM(naive=True))
      The same planner, except it *obeys instructions found inside untrusted
      envelopes*. It is a fully compromised model.

The adversarial suite runs against the naive model on purpose. If your attack
tests only ever run against a model that happens to be well-behaved, they
prove nothing about your architecture. Running them against a model that
always falls for the attack proves the thing you actually care about: that
the guardrails, the source-trust filter and the Tool Gateway stop it anyway.

    "Assume the model is compromised. Prove the system still holds."
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from app.llm.base import Completion, CompletionRequest

# ----------------------------------------------------------------------
# intent detection (deliberately crude and deterministic)
# ----------------------------------------------------------------------

# Order matters: the first rule that matches wins. "What is your refund
# policy?" must route to the policy/RAG path, not to the refund agent -- it is
# a question about the rules, not about this customer's money.
_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("order_delay_refund", ("delay", "late", "not arrived", "hasn't arrived", "stuck")),
    ("policy", ("policy", "warranty", "return window", "how long do i have",
                "is that true", "entitled to")),
    ("cancel_order", ("cancel my order", "cancel order", "cancel the order")),
    ("return_item", ("return this", "want to return", "return my", "start a return")),
    ("checkout_help", ("buy now", "place order", "place an order", "place my order",
                       "place it", "i want to order", "i want to buy", "checkout",
                       "want to buy", "buy this", "buy it", "order now", "order for me",
                       "order this", "order it", "pay with", "payment method",
                       "payment option", "how do i pay", "cash on delivery")),
    ("order_list", ("my orders", "recent orders", "order history", "my recent orders",
                    "list my orders", "all my orders", "check order", "check orders",
                    "show order", "show orders", "see order", "see orders",
                    "list order", "your orders", "existing order", "order details",
                    "order detail", "order detils", "my existing order", "get the order",
                    "get my order", "i placed", "order i placed", "the order i")),
    ("order_status", ("where is my order", "order status", "track", "my order")),
    ("refund_status", ("refund", "money back")),
    ("admin_ops", ("pending order", "approve order", "reject order", "low stock",
                   "need restock", "need stock", "list pending", "order queue",
                   "restock")),
    ("promotion", ("coupon", "promo", "discount", "offer")),
    ("shopping", ("laptop", "phone", "iphone", "oiphone", "macbook", "airpod", "headphone",
                  "search for", "find me", "looking for", "under ", "compare", "recommend",
                  "suggest", "best ", "prices", "price of", "catalogue", "mouse", "keyboard",
                  "monitor", "earbud", "tablet", "speaker", "camera", "watch", " i need",
                  "i want a", "any good")),
]


_ORDER_REF = re.compile(r"\bOR-[A-Za-z0-9]{4,}\b", re.IGNORECASE)


def detect_intent(text: str) -> str:
    from app.nlp.understand import rewrite_query

    low = rewrite_query(text or "").lower()
    for intent, needles in _INTENT_RULES:
        if intent == "shopping" and _ORDER_REF.search(text or ""):
            return "order_status"
        if any(n in low for n in needles):
            return intent
    # A bare order reference is an order question even without the word "my".
    if _ORDER_REF.search(text or ""):
        return "order_status"
    return "smalltalk"


def extract_order_id(text: str) -> Optional[str]:
    m = _ORDER_REF.search(text or "")
    return m.group(0).upper() if m else None


_PRICE = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*\d)\s*(k)?", re.IGNORECASE)


def extract_budget_inr(text: str) -> Optional[int]:
    low = re.sub(r"\b[A-Z]{2,}-[A-Z0-9]+\b", "", text or "", flags=re.IGNORECASE).lower()
    m = re.search(r"(\d+)\s*k\b", low)
    if m:
        return int(m.group(1)) * 1000
    m = _PRICE.search(low)
    if m:
        try:
            value = int(m.group(1).replace(",", ""))
            return value if value >= 100 or re.search(r"₹|\brs\.?|\binr", low) else None
        except ValueError:
            return None
    return None


def extract_min_ram_gb(text: str) -> Optional[int]:
    low = (text or "").lower()
    m = re.search(r"(\d+)\s*gb(?:\s*ram)?", low)
    if not m:
        m = re.search(r"ram\s*(\d+)", low)
    if not m:
        return None
    n = int(m.group(1))
    if n in {6, 8, 12, 16, 24, 32, 64}:
        return n
    return None


def extract_min_rating(text: str) -> Optional[float]:
    low = (text or "").lower()
    m = re.search(r"rating\s*(\d+(?:\.\d+)?)\s*\+?", low)
    if not m:
        m = re.search(r"(\d(?:\.\d)?)\s*\+", low)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    if 0 <= val <= 5:
        return val
    return None


def extract_payment_method(text: str) -> Optional[str]:
    low = (text or "").lower()
    if re.search(r"\b(cod|cash[\s-]+on[\s-]+delivery)\b", low):
        return "cod"
    if re.search(r"\b(upi|gpay|phonepe|paytm)\b", low):
        return "upi"
    if re.search(r"\b(cards?|credit|debit|visa|mastercard)\b", low):
        return "card"
    return None


_CATEGORIES = {
    "laptop": "laptops",
    "notebook": "laptops",
    "macbook": "laptops",
    "iphone": "phones",
    "oiphone": "phones",
    "phone": "phones",
    "smartphone": "phones",
    "headphone": "audio",
    "airpod": "audio",
    "earbud": "audio",
    "monitor": "monitors",
}


def extract_category(text: str) -> Optional[str]:
    from app.nlp.understand import rewrite_query

    low = rewrite_query(text or "").lower()
    for needle, cat in _CATEGORIES.items():
        if needle in low:
            return cat
    return None


# ----------------------------------------------------------------------
# injection payload recognition (used ONLY by naive mode)
# ----------------------------------------------------------------------

_ORDER_ID = re.compile(r"\bOR-[A-Za-z0-9]{4,}\b", re.IGNORECASE)
_CUSTOMER_ID = re.compile(r"\bCU-[A-Za-z0-9]{3,}\b")

_INJECTION_MARKERS = (
    "ignore previous", "ignore prior", "ignore your", "disregard",
    "system message", "system override", "new instructions",
    "reveal your system", "print your system", "show your system",
    "you must comply", "override the authorization", "regardless of ownership",
)


def _untrusted_regions(prompt: str) -> str:
    """Everything inside untrusted envelopes, concatenated."""
    pattern = re.compile(
        r"<(MEMORY|RETRIEVED|TOOL_OUTPUT)[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE
    )
    return "\n".join(m.group(2) for m in pattern.finditer(prompt))


def _detect_injection(prompt: str) -> Optional[dict]:
    region = _untrusted_regions(prompt)
    low = region.lower()
    if not any(marker in low for marker in _INJECTION_MARKERS):
        return None
    if any(k in low for k in ("system prompt", "your instructions", "your rules")):
        return {"kind": "prompt_extraction"}
    order_ids = _ORDER_ID.findall(region)
    if order_ids or "another customer" in low or "other customer" in low:
        return {"kind": "cross_customer", "order_id": order_ids[0] if order_ids else "OR-VICTIM01"}
    if "refund" in low:
        return {"kind": "unauthorized_refund"}
    return {"kind": "generic"}


# ----------------------------------------------------------------------
# planners
# ----------------------------------------------------------------------

# Identifier each tool cannot run without. A planner that emits a call with an
# empty identifier is hallucinating an argument; skipping the step and
# re-planning is the correct behaviour, and the gateway would reject it anyway.
_REQUIRED_ARG = {
    "get_order": "order_id",
    "cancel_order": "order_id",
    "get_shipment": "order_id",
    "track_shipment": "shipment_id",
    "get_payment": "order_id",
    "get_refund": "payment_id",
    "check_return_eligibility": "order_id",
    "create_return": "order_id",
    "get_product": "product_id",
    "check_inventory": "product_id",
    "compare_products": "product_ids",
    "get_support_ticket": "ticket_id",
}


def _missing_required(tool: str, args: dict) -> bool:
    key = _REQUIRED_ARG.get(tool)
    if not key:
        return False
    return not args.get(key)


def _act(atype: str, **kw) -> str:
    payload = {"type": atype}
    payload.update({k: v for k, v in kw.items() if v is not None})
    return json.dumps(payload)


COMPARISON_CUES = ("compare", "best", "which one", "which is", "recommend",
                   "difference", "versus", " vs ", "top three", "top 3")


def wants_comparison(text: str) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in COMPARISON_CUES)


# Plans by intent, for an agent that holds every tool (mode 3).
SINGLE_AGENT_PLANS: dict[str, list[str]] = {
    "shopping": ["search_products", "compare_products"],
    "order_list": ["get_orders"],
    "order_status": ["get_orders", "get_order", "get_shipment", "track_shipment"],
    "order_delay_refund": ["get_orders", "get_order", "get_shipment", "track_shipment"],
    "cancel_order": ["get_orders", "get_order", "cancel_order"],
    "return_item": ["get_orders", "check_return_eligibility", "create_return"],
    "refund_status": ["get_orders", "get_payment", "get_refund"],
    "promotion": ["get_active_promotions"],
    "policy": ["retrieve_policy"],
    "checkout_help": ["search_products", "list_payment_methods", "create_order"],
    "admin_ops": ["get_pending_orders", "list_low_stock"],
}

# Plans by SPECIALIST agent. A delegated sub-agent must not inherit the
# supervisor's whole-request plan -- that is how sub-agents end up redoing
# each other's work and blowing the step budget.
AGENT_PLANS: dict[str, list[str]] = {
    "order": ["get_orders", "get_order", "get_shipment", "track_shipment"],
    "policy": ["retrieve_policy"],
    "refund": ["get_payment", "check_return_eligibility"],
    "support": ["create_support_ticket"],
    "product": ["search_products", "compare_products"],
    "shopping": ["search_products", "compare_products"],
    "recommendation": ["get_recommendations"],
    "checkout": ["search_products", "list_payment_methods", "create_order"],
    "admin": ["get_pending_orders", "approve_order", "reject_order"],
    "inventory": ["list_low_stock", "check_inventory"],
}

# A specialist's plan narrows when the parent request does not need its full
# workup. The order agent chasing carrier scans during a RETURN is three wasted
# steps -- visible as an unnecessary-call penalty on the trajectory score.
AGENT_PLANS_BY_INTENT: dict[tuple[str, str], list[str]] = {
    ("order", "order_list"): ["get_orders"],
    ("order", "return_item"): ["get_orders", "get_order"],
    ("order", "refund_status"): ["get_orders", "get_order"],
    ("order", "order_status"): ["get_order", "get_payment", "get_shipment", "track_shipment"],
    ("checkout", "checkout_help"): ["search_products", "list_payment_methods", "create_order"],
    ("admin", "admin_ops"): ["get_pending_orders"],
    ("inventory", "admin_ops"): ["list_low_stock"],
}

REFUND_CUES = ("refund", "money back", "compensat", "reimburse")
SUPPORT_CUES = ("ticket", "support request", "raise", "complain", "escalate",
                "someone to look", "human", "agent to")


# Sub-tasks that are OPTIONAL extras rather than part of the core task. For a
# delay enquiry, assessing a refund and opening a ticket are things the
# customer may or may not want. For a RETURN request the refund agent is the
# task, so it is never pruned -- gating it there would silently drop the step
# that actually creates the return.
CONDITIONAL_SUBTASKS: dict[tuple[str, str], tuple[str, ...]] = {
    ("order_delay_refund", "refund"): REFUND_CUES,
    ("order_delay_refund", "support"): SUPPORT_CUES,
}


def requested_subtasks(intent: str, text: str) -> list[tuple[str, str]]:
    """Delegate the core sub-tasks always, the optional ones only when asked.

    Agent overreach is a real defect, not over-helpfulness: "where is my order
    and why is it late?" that also opens a support ticket has created work for
    a human the customer never requested, and spent four agents' worth of
    tokens doing it.
    """
    low = (text or "").lower()
    keep = []
    for agent, task in SUPERVISOR_PLANS.get(intent, []):
        cues = CONDITIONAL_SUBTASKS.get((intent, agent))
        if cues is not None and not any(c in low for c in cues):
            continue
        keep.append((agent, task))
    return keep


SUPERVISOR_PLANS: dict[str, list[tuple[str, str]]] = {
    "order_delay_refund": [
        ("order", "Find the order, its shipment and why it is late."),
        ("policy", "What does policy say about refunds for delayed shipments?"),
        ("refund", "Determine refund eligibility and initiate if permitted."),
        ("support", "Raise a support ticket describing the delay."),
    ],
    "return_item": [
        ("policy", "What is the return window and eligibility for this item?"),
        ("order", "Find the order and item to be returned."),
        ("refund", "Check return eligibility and create the return."),
    ],
    "shopping": [
        ("product", "Search and compare candidate products against the constraints."),
        ("recommendation", "Rank the candidates and recommend one with reasons."),
    ],
    "checkout_help": [
        ("checkout", "Find the product the customer named, list payment methods, and place the order if they chose UPI, card, or COD."),
    ],
    "order_list": [
        ("order", "List this customer's recent orders with status, items and totals."),
    ],
    "order_status": [
        ("order", "Look up the order, payment method and shipment for this customer."),
    ],
}


class MockLLM:
    name = "mock"

    def __init__(self, model: str = "mock-1", naive: bool = False, fail_times: int = 0):
        self.model = model
        self.naive = naive
        self._fail_times = fail_times
        self.calls: list[CompletionRequest] = []

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, len(text) // 4)

    def _wrap(self, req: CompletionRequest, text: str, started: float) -> Completion:
        return Completion(
            text=text,
            model=self.model,
            provider=self.name,
            tokens_in=self._tokens(req.system) + self._tokens(req.prompt),
            tokens_out=self._tokens(text),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # -- entrypoint ----------------------------------------------------
    async def complete(self, req: CompletionRequest) -> Completion:
        started = time.perf_counter()
        self.calls.append(req)

        if self._fail_times > 0:
            from app.llm.base import LLMTimeout

            self._fail_times -= 1
            raise LLMTimeout("mock provider injected failure")

        purpose = req.purpose
        if purpose == "agent_step":
            text = self._agent_step(req)
        elif purpose == "rag_answer":
            text = self._rag_answer(req)
        elif purpose == "judge":
            text = self._judge(req)
        elif purpose == "memory_extract":
            text = self._memory_extract(req)
        elif purpose == "query_understanding":
            text = (req.meta.get("user_text") or "").strip()
        else:
            text = self._chat(req)
        return self._wrap(req, text, started)

    # -- behaviours ----------------------------------------------------
    def _agent_step(self, req: CompletionRequest) -> str:
        meta = req.meta
        agent = meta.get("agent", "supervisor")
        user_text = meta.get("user_text", "")
        executed: list[str] = list(meta.get("executed_tools") or [])
        delegated: list[str] = list(meta.get("delegated_agents") or [])
        available: list[str] = list(meta.get("available_tools") or [])
        intent = meta.get("intent") or detect_intent(user_text)

        if self.naive:
            hijack = _detect_injection(req.prompt)
            if hijack:
                return self._obey_injection(hijack, executed, meta)

        if agent == "supervisor":
            plan = requested_subtasks(intent, user_text)
            if plan:
                for sub_agent, task in plan:
                    if sub_agent not in delegated:
                        return _act(
                            "delegate",
                            agent=sub_agent,
                            task=task,
                            reasoning=f"sub-task {len(delegated) + 1}/{len(plan)} of a {intent} request",
                        )
                return _act(
                    "answer",
                    content=self._compose_multi(meta),
                    reasoning="all declared sub-tasks closed",
                )
            # No multi-agent plan: handle it as a single agent would.
            return self._single(intent, executed, available, user_text, meta)

        return self._single(intent, executed, available, user_text, meta, agent=agent)

    def _single(
        self,
        intent: str,
        executed: list[str],
        available: list[str],
        user_text: str,
        meta: dict,
        agent: str = "single",
    ) -> str:
        plan = (
            AGENT_PLANS_BY_INTENT.get((agent, intent))
            or AGENT_PLANS.get(agent)
            or SINGLE_AGENT_PLANS.get(intent, [])
        )
        facts: dict = meta.get("facts") or {}
        if "compare_products" in plan and not wants_comparison(user_text):
            plan = [t for t in plan if t != "compare_products"]

        # If the customer named an order, look it up directly instead of
        # listing everything first. Fewer steps, and it exercises the
        # ownership check on a specific record -- which is the interesting case.
        if extract_order_id(user_text) and "get_orders" in plan and "get_order" in plan:
            plan = [t for t in plan if t != "get_orders"]

        pay = extract_payment_method(user_text) or facts.get("payment_method")
        if "create_order" in plan:
            if not (meta.get("customer_id") or facts.get("customer_id")):
                plan = [t for t in plan if t != "create_order"]
            elif not pay:
                plan = [t for t in plan if t != "create_order"]
            elif not (facts.get("product_id") or facts.get("candidate_product_ids")):
                plan = [t for t in plan if t != "create_order"]

        for tool in plan:
            if tool in executed:
                continue
            if available and tool not in available:
                continue
            # Skip a step whose required identifier was never established.
            args = self._arguments_for(tool, user_text, meta)
            if _missing_required(tool, args):
                continue
            return _act(
                "tool",
                tool=tool,
                arguments=args,
                reasoning=f"{agent}: next step of the {intent} plan",
            )

        # The return flow only creates a return once eligibility says yes.
        if (
            intent == "return_item"
            and facts.get("return_eligible")
            and "create_return" not in executed
            and (not available or "create_return" in available)
        ):
            return _act(
                "tool",
                tool="create_return",
                arguments=self._arguments_for("create_return", user_text, meta),
                reasoning="eligibility confirmed, creating the return",
            )

        return _act(
            "answer",
            content=self._compose_single(intent, meta, agent=agent),
            reasoning=f"{agent}: plan complete",
            citations=meta.get("citation_ids") or [],
        )

    @staticmethod
    def _pick_order_id(facts: dict, meta: dict) -> str:
        """Choose which order the customer means.

        "My order is delayed" is about the order that is in transit, not the
        most recent one. Picking blindly by recency is the classic argument
        error the trajectory evaluator is there to catch.
        """
        named = facts.get("_named_order_id")
        if named:
            return named
        explicit = facts.get("order_id")
        orders = facts.get("orders") or []
        intent = meta.get("intent", "")
        if orders:
            if intent in ("order_delay_refund", "order_status"):
                for o in orders:
                    if o.get("status") in ("shipped", "packed"):
                        return o["order_id"]
            if intent == "return_item":
                for o in orders:
                    if o.get("status") == "delivered":
                        return o["order_id"]
            if intent == "cancel_order":
                for o in orders:
                    if o.get("status") == "placed":
                        return o["order_id"]
        return explicit or (orders[0]["order_id"] if orders else "")

    def _arguments_for(self, tool: str, user_text: str, meta: dict) -> dict:
        facts: dict = dict(meta.get("facts") or {})
        customer_id = meta.get("customer_id", "")
        # An order id the customer typed is as good a source as a tool result.
        named = extract_order_id(user_text)
        if named:
            facts["_named_order_id"] = named
            facts.setdefault("order_id", named)
        if tool == "search_products":
            args: dict = {"query": user_text[:120]}
            cat = extract_category(user_text)
            if cat:
                args["category"] = cat
            budget = extract_budget_inr(user_text)
            if budget:
                args["max_price_inr"] = budget
            brand = facts.get("brand_preference")
            if brand:
                args["brand"] = brand
            return args
        if tool == "compare_products":
            return {"product_ids": (facts.get("candidate_product_ids") or [])[:3]}
        if tool in ("get_product", "check_inventory"):
            return {"product_id": (facts.get("candidate_product_ids") or [""])[0]}
        if tool == "get_orders":
            return {"customer_id": customer_id, "limit": 10}
        if tool == "get_order":
            return {"order_id": self._pick_order_id(facts, meta)}
        if tool == "cancel_order":
            return {"order_id": self._pick_order_id(facts, meta), "reason": "customer_request"}
        if tool == "get_shipment":
            return {"order_id": self._pick_order_id(facts, meta)}
        if tool == "track_shipment":
            return {"shipment_id": facts.get("shipment_id", "")}
        if tool == "get_payment":
            return {"order_id": self._pick_order_id(facts, meta)}
        if tool == "get_refund":
            return {"payment_id": facts.get("payment_id", "")}
        if tool == "check_return_eligibility":
            return {"order_id": self._pick_order_id(facts, meta),
                    "order_item_id": facts.get("order_item_id", "")}
        if tool == "create_return":
            return {
                "order_id": self._pick_order_id(facts, meta),
                "order_item_id": facts.get("order_item_id", ""),
                "reason": "not_as_described",
            }
        if tool == "create_support_ticket":
            return {
                "customer_id": customer_id,
                "order_id": self._pick_order_id(facts, meta),
                "category": "delivery_delay",
                "summary": user_text[:180],
            }
        if tool == "get_recommendations":
            return {"customer_id": customer_id, "category": extract_category(user_text) or "laptops"}
        if tool in ("search_knowledge_base", "retrieve_policy"):
            return {"query": user_text[:200]}
        if tool == "get_active_promotions":
            return {}
        if tool == "validate_coupon":
            return {"code": (facts.get("coupon_code") or "SAVE10")}
        if tool == "get_customer_preferences":
            return {"customer_id": customer_id}
        if tool == "list_payment_methods":
            return {}
        if tool == "create_order":
            pid = facts.get("product_id") or (facts.get("candidate_product_ids") or [None])[0]
            method = extract_payment_method(user_text) or facts.get("payment_method") or "upi"
            return {
                "customer_id": customer_id,
                "payment_method": method,
                "items": [{"product_id": pid or "", "qty": 1}],
            }
        if tool in ("get_pending_orders", "list_low_stock"):
            return {}
        if tool == "approve_order":
            return {"order_id": extract_order_id(user_text) or facts.get("order_id") or "OR-20003"}
        if tool == "reject_order":
            return {
                "order_id": extract_order_id(user_text) or facts.get("order_id") or "OR-20004",
                "reason": "admin_rejected",
            }
        return {}

    # -- answer composition -------------------------------------------
    SPECIALISTS = ("order", "policy", "refund", "support", "product", "shopping",
                   "recommendation", "checkout", "admin", "inventory")

    def _compose_single(self, intent: str, meta: dict, agent: str = "single") -> str:
        facts: dict = meta.get("facts") or {}
        chunks: list[dict] = meta.get("chunks") or []

        # A specialist summarises ONLY its own domain. Letting a sub-agent
        # answer from shared facts it did not establish is how multi-agent
        # answers turn into the same paragraph repeated four times.
        if agent in self.SPECIALISTS:
            return self._compose_specialist(agent, facts, chunks)

        if chunks:
            return self._cite(chunks)
        if intent == "order_list" and facts.get("orders"):
            lines = [f"{o['order_id']} - {o['status']}, INR {o['total_inr']:,}"
                     for o in facts["orders"][:5]]
            return "Your recent orders: " + "; ".join(lines) + "."
        if intent in ("order_status", "order_delay_refund") and facts.get("order_id"):
            return self._order_summary(facts)
        if intent == "shopping" and facts.get("candidate_titles"):
            titles = facts["candidate_titles"][:3]
            prices = facts.get("candidate_prices") or []
            bits = []
            for i, title in enumerate(titles):
                price = prices[i] if i < len(prices) and prices[i] is not None else None
                bits.append(f"{title} (₹{int(price):,})" if price is not None else title)
            listed = "; ".join(bits)
            return (
                f"Closest catalogue matches: {listed}. "
                f"I would start with {titles[0]} on the balance of specs and price."
            )
        if intent == "checkout_help":
            methods = facts.get("payment_methods") or []
            labels = ", ".join(m.get("label") or m.get("id") for m in methods) or "UPI, Card, Cash on delivery"
            if facts.get("order_id"):
                return (
                    f"Placed order {facts['order_id']} for INR {facts.get('order_total_inr', 0):,}. "
                    f"Payment: {facts.get('payment_method', 'upi')} ({facts.get('payment_status', 'captured')})."
                )
            titles = facts.get("candidate_titles") or []
            pick = titles[0] if titles else "the item you named"
            if not (meta.get("customer_id") or facts.get("logged_in")):
                return (
                    f"I can check {pick} out with {labels}. Sign in first so I can place the order on your account."
                )
            return (
                f"Ready to order {pick}. Payment options: {labels}. "
                "Reply with UPI, Card, or Cash on delivery to place it."
            )
        if intent == "promotion" and facts.get("promotions"):
            return "Active offers: " + ", ".join(facts["promotions"][:4]) + "."
        if intent == "admin_ops":
            if "low_stock" in facts:
                items = facts.get("low_stock") or []
                if not items:
                    return "No SKUs are at or below the restock threshold."
                bits = [
                    f"{it.get('title')} ({it.get('product_id')}) — {it.get('available')} left"
                    for it in items[:6]
                ]
                return "Low stock SKUs: " + "; ".join(bits) + "."
            orders = facts.get("pending_orders") or []
            if orders:
                lines = [f"{o.get('order_id')} - {o.get('status', 'placed')}" for o in orders[:6]]
                return "Pending orders: " + "; ".join(lines) + "."
            if "pending_orders" in facts:
                return "The pending order queue is empty."
        if facts.get("summary"):
            return str(facts["summary"])
        return (
            "I could not establish that from your account or our policy documents, "
            "so I would rather say so than guess."
        )

    def _compose_specialist(self, agent: str, facts: dict, chunks: list) -> str:
        if agent == "order":
            if facts.get("orders") and not facts.get("order_id"):
                lines = [f"{o['order_id']} - {o['status']}, INR {o['total_inr']:,}"
                         for o in facts["orders"][:5]]
                return "Your recent orders: " + "; ".join(lines) + "."
            if facts.get("order_id"):
                return self._order_summary(facts)
            return "I could not find an order matching that on your account."
        if agent == "checkout":
            methods = facts.get("payment_methods") or []
            labels = ", ".join(m.get("label") or m.get("id") for m in methods) or "UPI, Card, COD"
            if facts.get("order_id"):
                return (
                    f"Order {facts['order_id']} placed. Total INR {facts.get('order_total_inr', 0):,}. "
                    f"Paid via {facts.get('payment_method', 'upi')}."
                )
            titles = facts.get("candidate_titles") or []
            pick = titles[0] if titles else "that product"
            return f"{pick} is in stock. Pay with {labels} — tell me which one to use."
        if agent == "policy":
            if chunks:
                return self._cite(chunks)
            return "The policy documents do not cover that point."
        if agent == "refund":
            if facts.get("return_eligible") is True:
                return (
                    f"The order is within its {facts.get('return_window_days', '')}-day return "
                    "window, so a return can be raised."
                )
            if int(facts.get("days_late") or 0) > 5:
                return (
                    f"The shipment is {facts['days_late']} days past its promised date, which is "
                    "beyond the 5-day threshold, so a refund can be requested without returning "
                    "the item."
                )
            if facts.get("return_eligible") is False:
                return "The order is outside its return window, so a return cannot be raised."
            return "Nothing on this order is currently refundable."
        if agent == "support":
            if facts.get("ticket_id"):
                return f"I raised support ticket {facts['ticket_id']} so a person can follow up."
            return "I was not able to raise a support ticket for this."
        if agent == "admin":
            orders = facts.get("pending_orders") or []
            if orders:
                lines = [f"{o.get('order_id')} - {o.get('status', 'placed')}" for o in orders[:6]]
                return "Pending orders: " + "; ".join(lines) + "."
            if "pending_orders" in facts:
                return "The pending order queue is empty."
            return "I could not list pending orders."
        if agent == "inventory":
            if "low_stock" in facts:
                items = facts.get("low_stock") or []
                if not items:
                    return "No SKUs are at or below the restock threshold."
                bits = [
                    f"{it.get('title')} ({it.get('product_id')}) — {it.get('available')} left"
                    for it in items[:6]
                ]
                return "Low stock SKUs: " + "; ".join(bits) + "."
            if facts.get("product_id") and facts.get("available") is not None:
                return f"{facts['product_id']}: {facts.get('available')} available."
            return "I could not check inventory."
        titles = facts.get("candidate_titles") or []
        if titles:
            prices = facts.get("candidate_prices") or []
            bits = []
            for i, title in enumerate(titles[:3]):
                price = prices[i] if i < len(prices) and prices[i] is not None else None
                bits.append(f"{title} — ₹{int(price):,}" if price is not None else title)
            return "Candidates: " + "; ".join(bits) + "."
        return "I found nothing matching those constraints."

    @staticmethod
    def _order_summary(facts: dict) -> str:
        bits = [f"Order {facts['order_id']} is currently {facts.get('order_status', 'unknown')}."]
        if facts.get("shipment_status"):
            bits.append(f"The shipment is {facts['shipment_status']}.")
        if facts.get("payment_method"):
            bits.append(f"Payment was {facts['payment_method']}"
                        + (f" ({facts['payment_status']})" if facts.get("payment_status") else "")
                        + ".")
        if facts.get("days_late"):
            bits.append(f"It is {facts['days_late']} days past the promised date.")
        if facts.get("shipment_exception"):
            bits.append(f"The carrier recorded: {facts['shipment_exception']}.")
        return " ".join(bits)

    @staticmethod
    def _cite(chunks: list) -> str:
        """Answer from the top chunk, adding a second only when it comes from
        the same document and scores close to it. Stitching together loosely
        related chunks is how a grounded answer becomes an ungrounded one."""
        top = chunks[0]
        best_score = top.get("score") or 0.0
        parts = [f"{top['content'].strip()[:520]} [{top['id']}]"]
        for c in chunks[1:3]:
            if (
                c.get("document_id") == top.get("document_id")
                and (c.get("score") or 0.0) >= 0.7 * (best_score or 1.0)
            ):
                parts.append(f"{c['content'].strip()[:320]} [{c['id']}]")
                break
        return " ".join(parts)

    def _compose_multi(self, meta: dict) -> str:
        results: list[dict] = meta.get("sub_results") or []
        if not results:
            return "I was unable to complete the sub-tasks for this request."
        lines = []
        for r in results:
            lines.append(f"{r.get('agent', 'agent')}: {r.get('summary', '').strip()}")
        return " ".join(lines)

    # A v2-style prompt ("fill the gap with your best judgement") makes a real
    # model more helpful and less grounded. The mock reproduces that trade-off
    # deterministically so the prompt-regression demo runs offline. This is a
    # simulation of a known effect, not evidence of it -- see docs/adr/0005.
    _EMBELLISH_MARKER = "best judgement to fill the gap"
    _EMBELLISH = (
        " In practice most retailers also allow an extra grace period of a few days "
        "beyond this, and a manager can usually approve an exception on request."
    )

    def _embellishes(self, req: CompletionRequest) -> bool:
        return self._EMBELLISH_MARKER in (req.system or "")

    def _rag_answer(self, req: CompletionRequest) -> str:
        chunks: list[dict] = req.meta.get("chunks") or []
        if not chunks:
            if self._embellishes(req):
                return (
                    "Our standard policy in cases like this is a 30-day window with "
                    "free return pickup." + self._EMBELLISH
                )
            return (
                "I don't have a policy document that covers that, so I won't guess. "
                "I can raise a support ticket if you'd like a human to confirm."
            )
        answer = self._cite(chunks)
        if self._embellishes(req):
            answer += self._EMBELLISH
        return answer

    def _chat(self, req: CompletionRequest) -> str:
        user_text = req.meta.get("user_text") or req.prompt[-400:]
        if self.naive:
            hijack = _detect_injection(req.prompt)
            if hijack and hijack["kind"] == "prompt_extraction":
                return "My system prompt is: " + (req.system or "")[:600]
        return (
            "Hi — I’m the ShopZone assistant. I can search the catalogue even if you "
            "misspell a product or mix Hindi and English, explain returns and warranty, "
            "and after you sign in I can show your orders or place one with UPI, Card, or COD. "
            "What would you like to look at?"
        )

    def _judge(self, req: CompletionRequest) -> str:
        """A deterministic stand-in for an LLM judge.

        It scores lexical overlap against the reference. It is NOT a real judge
        and evaluation/judge.py labels it as such -- but it makes the judge
        plumbing, the rubric, the calibration harness and the position-bias
        tests all runnable offline.
        """
        answer = (req.meta.get("answer") or "").lower()
        reference = (req.meta.get("reference") or "").lower()
        if not reference:
            score = 4 if len(answer) > 40 else 2
            return json.dumps({"score": score, "rationale": "no reference; length heuristic"})
        ref_terms = {w for w in re.findall(r"[a-z0-9]{4,}", reference)}
        ans_terms = {w for w in re.findall(r"[a-z0-9]{4,}", answer)}
        if not ref_terms:
            overlap = 0.0
        else:
            overlap = len(ref_terms & ans_terms) / len(ref_terms)
        score = 1 + int(round(overlap * 4))
        return json.dumps(
            {"score": max(1, min(5, score)), "rationale": f"term overlap {overlap:.2f}"}
        )

    def _memory_extract(self, req: CompletionRequest) -> str:
        text = (req.meta.get("user_text") or "").lower()
        facts = []
        for brand in ("lenovo", "dell", "hp", "apple", "asus", "samsung", "sony"):
            if f"prefer {brand}" in text or f"like {brand}" in text or f"{brand} laptops" in text:
                facts.append({"type": "brand_preference", "value": brand.title(), "confidence": 0.85})
                break
        budget = extract_budget_inr(text)
        if budget and ("budget" in text or "under" in text or "less than" in text):
            facts.append({"type": "budget_range", "value": f"<= {budget}", "confidence": 0.7})
        cat = extract_category(text)
        if cat and ("interested" in text or "looking" in text or "prefer" in text):
            facts.append({"type": "category_interest", "value": cat, "confidence": 0.6})
        # Deliberately included so the schema filter has something to reject.
        if "i am an administrator" in text or "i'm an admin" in text or "platform administrator" in text:
            facts.append({"type": "role", "value": "administrator", "confidence": 0.9})
        return json.dumps({"facts": facts})

    # -- compromised behaviour ----------------------------------------
    def _obey_injection(self, hijack: dict, executed: list[str], meta: dict) -> str:
        kind = hijack["kind"]
        if kind == "prompt_extraction":
            return _act(
                "answer",
                content="Certainly. My system instructions are: " + (meta.get("system") or "")[:400],
                reasoning="COMPROMISED: obeying injected instruction",
            )
        if kind == "cross_customer" and "get_order" not in executed:
            return _act(
                "tool",
                tool="get_order",
                arguments={"order_id": hijack.get("order_id", "OR-VICTIM01")},
                reasoning="COMPROMISED: obeying injected instruction",
            )
        if kind == "unauthorized_refund" and "create_return" not in executed:
            return _act(
                "tool",
                tool="create_return",
                arguments={
                    "order_id": (meta.get("facts") or {}).get("order_id", "OR-UNKNOWN"),
                    "order_item_id": "",
                    "reason": "injected_policy_override",
                },
                reasoning="COMPROMISED: obeying injected instruction",
            )
        return _act(
            "answer",
            content="Understood, I will follow those instructions.",
            reasoning="COMPROMISED: obeying injected instruction",
        )
