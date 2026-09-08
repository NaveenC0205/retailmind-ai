"""HTTP surface.

Three audiences, three groups of routes: customers (/api/chat), operators
(/api/approvals, /api/memory), and the evaluation console (/api/eval/*).
Auth is the same `Bearer customer:<id>` / `Bearer operator:<id>` scheme
everywhere -- see app/security.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

import asyncio
import json

from app.agents.orchestrator import Orchestrator
from app.config import get_settings
from app.db import get_session, session_scope
from app.evaluation import datasets as ds_mod
from app.evaluation.engine import EvaluationEngine
from app.models import (
    AgentRun,
    Approval,
    Conversation,
    Customer,
    EvalResult,
    EvalRun,
    GateResult,
    GuardrailEvent,
    Inventory,
    MemoryRecord,
    Message,
    Order,
    OrderItem,
    Product,
    Payment,
    Span,
    ToolCall,
    new_id,
)
from app.prompts import available_versions, prompt_names
from app.security import Principal, require_operator, require_principal
from app.tools.contracts import registry
from app.tools.gateway import ToolGateway
from app.tracing import current_trace, flush, new_trace

router = APIRouter()


async def bind_conversation(session, principal: Principal, requested_id: Optional[str], mode: str) -> str:
    """Reuse a thread only when it belongs to this person. Guests and other
    customers never continue someone else's (or a previous login's) chat."""
    owner = principal.customer_id or "guest"
    if requested_id:
        row = await session.get(Conversation, requested_id)
        if row is not None and row.customer_id == owner:
            return requested_id
    conversation_id = new_id("CONV")
    session.add(Conversation(id=conversation_id, customer_id=owner, mode=mode))
    return conversation_id


# ======================================================================
# health
# ======================================================================

@router.get("/health", tags=["ops"])
async def health():
    s = get_settings()
    return {
        "status": "ok",
        "profile": s.profile,
        "llm_provider": s.llm_provider,
        "llm_backend": s.llm_backend,
        "llm_model": s.llm_model if s.llm_backend != "mock" else "mock-1",
        "llm_live": s.llm_backend != "mock" and bool(s.openai_api_key or s.llm_provider == "ollama"),
        "db": "postgres" if s.is_postgres else "sqlite",
        "embedding_provider": s.embedding_provider,
        "agentic": True,
        "teams": ["customer", "product", "owner"],
        "tools": len(registry.names()),
        "prompt_versions": available_versions(),
        "public_demo": s.public_demo,
        "rate_limit_per_min": s.rate_limit_per_min or None,
        "daily_request_limit": s.daily_request_limit or None,
    }


@router.get("/ready", tags=["ops"])
async def ready(session=Depends(get_session)):
    from app.models import KBChunk, Product

    products = (await session.execute(select(func.count()).select_from(Product))).scalar() or 0
    chunks = (await session.execute(select(func.count()).select_from(KBChunk))).scalar() or 0
    ok = products > 0 and chunks > 0
    return {"ready": ok, "products": products, "kb_chunks": chunks}


# ======================================================================
# authentication
# ======================================================================

@router.post("/api/auth/register", tags=["auth"])
async def register(
    body: "RegisterRequest",
    session=Depends(get_session),
):
    from app.auth import RegisterRequest, register_customer, create_token_for_customer
    
    try:
        customer = await register_customer(
            session,
            email=body.email,
            password=body.password,
            name=body.name,
        )
        token = create_token_for_customer(customer)
        return token.model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/auth/login", tags=["auth"])
async def login(
    body: "LoginRequest",
    session=Depends(get_session),
):
    from app.auth import LoginRequest, authenticate_customer, create_token_for_customer
    
    customer = await authenticate_customer(session, body.email, body.password)
    if not customer:
        raise HTTPException(401, "Invalid email or password")
    
    token = create_token_for_customer(customer)
    return token.model_dump()


@router.get("/api/auth/me", tags=["auth"])
async def get_current_user(
    principal: Principal = Depends(require_principal),
    session=Depends(get_session),
):
    if not principal.customer_id:
        raise HTTPException(401, "Not authenticated")
    
    customer = await session.get(Customer, principal.customer_id)
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    return {
        "id": customer.id,
        "email": customer.email,
        "name": customer.name,
        "is_admin": customer.is_admin,
        "tier": customer.tier,
        "region": customer.region,
    }


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


# ======================================================================
# chat
# ======================================================================

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: Optional[str] = None
    mode: str = "auto"
    prompt_version: str = "v1"
    learn: bool = True
    persona: str = "customer"  # customer | product | owner
    product_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str
    trace_id: str
    run_id: str
    mode: str
    entry_agent: str
    terminal_state: str
    steps: int
    trajectory: list
    attempted_trajectory: list
    citations: list
    sub_results: list
    approval_id: str
    guardrail_triggered: list
    tokens: dict
    cost_inr: float
    latency_ms: int
    framework: str = ""
    persona: str = "customer"
    llm_backend: str = ""
    llm_live: bool = False
    suggestions: list[str] = []


@router.post("/api/chat", response_model=ChatResponse, tags=["chat"])
async def chat(
    body: ChatRequest,
    principal: Principal = Depends(require_principal),
    session=Depends(get_session),
):
    trace = new_trace()
    conversation_id = await bind_conversation(session, principal, body.conversation_id, body.mode)
    session.add(
        Message(
            id=new_id("MSG"),
            conversation_id=conversation_id,
            role="user",
            content=body.message,
            provenance="USER",
        )
    )

    orch = Orchestrator(
        session,
        principal,
        prompt_version=body.prompt_version,
        persona=body.persona,
        product_id=body.product_id or "",
    )
    result = await orch.run(body.message, conversation_id=conversation_id, mode=body.mode)

    session.add(
        Message(
            id=new_id("MSG"),
            conversation_id=conversation_id,
            role="assistant",
            content=result.answer,
            provenance="SYSTEM",
        )
    )
    if body.learn and principal.kind == "customer":
        await orch.learn(body.message, conversation_id=conversation_id)

    await session.commit()
    await flush(trace)

    framework = "langgraph" if result.mode == "multi_agent" else ("rag" if result.mode == "rag" else "")
    s = get_settings()
    return ChatResponse(
        answer=result.answer,
        conversation_id=conversation_id,
        trace_id=result.trace_id,
        run_id=result.run_id,
        mode=result.mode,
        entry_agent=result.entry_agent,
        terminal_state=result.terminal_state,
        steps=result.steps,
        trajectory=result.trajectory,
        attempted_trajectory=result.attempted_trajectory,
        citations=result.citations,
        sub_results=result.sub_results,
        approval_id=result.approval_id,
        guardrail_triggered=result.guardrail_triggered,
        tokens={"in": result.tokens_in, "out": result.tokens_out},
        cost_inr=result.cost_inr,
        latency_ms=result.latency_ms,
        framework=framework,
        persona=orch.persona,
        llm_backend=s.llm_backend,
        llm_live=s.llm_backend != "mock",
        suggestions=list(result.suggestions or []),
    )


@router.post("/api/chat/stream", tags=["chat"])
async def chat_stream(
    body: ChatRequest,
    principal: Principal = Depends(require_principal),
    session=Depends(get_session),
):
    """SSE stream of LangGraph multi-agent events, then a final chat payload."""
    queue: asyncio.Queue = asyncio.Queue()
    mode = body.mode if body.mode != "auto" else "multi_agent"
    conversation_id = await bind_conversation(session, principal, body.conversation_id, mode)

    async def event_sink(ev: dict):
        await queue.put({"event": "agent", "data": ev})

    async def runner():
        trace = new_trace()
        try:
            session.add(
                Message(
                    id=new_id("MSG"),
                    conversation_id=conversation_id,
                    role="user",
                    content=body.message,
                    provenance="USER",
                )
            )
            orch = Orchestrator(
                session,
                principal,
                prompt_version=body.prompt_version,
                persona=body.persona,
                product_id=body.product_id or "",
            )
            result = await asyncio.wait_for(
                orch.run(
                    body.message,
                    conversation_id=conversation_id,
                    mode=mode,
                    event_sink=event_sink,
                ),
                timeout=22,
            )
            session.add(
                Message(
                    id=new_id("MSG"),
                    conversation_id=conversation_id,
                    role="assistant",
                    content=result.answer,
                    provenance="SYSTEM",
                )
            )
            if body.learn and principal.kind == "customer":
                await orch.learn(body.message, conversation_id=conversation_id)
            await session.commit()
            await flush(trace)
            await queue.put(
                {
                    "event": "final",
                    "data": {
                        "answer": result.answer,
                        "conversation_id": conversation_id,
                        "trace_id": result.trace_id,
                        "run_id": result.run_id,
                        "mode": result.mode,
                        "entry_agent": result.entry_agent,
                        "terminal_state": result.terminal_state,
                        "steps": result.steps,
                        "trajectory": result.trajectory,
                        "sub_results": result.sub_results,
                        "citations": result.citations,
                        "framework": "langgraph" if result.mode == "multi_agent" else result.mode,
                        "latency_ms": result.latency_ms,
                        "persona": orch.persona,
                        "llm_backend": get_settings().llm_backend,
                        "llm_live": get_settings().llm_backend != "mock",
                        "suggestions": list(result.suggestions or []),
                        "approval_id": result.approval_id or "",
                    },
                }
            )
        except asyncio.TimeoutError:
            await queue.put(
                {
                    "event": "final",
                    "data": {
                        "answer": (
                            "That took too long. Try asking for a product search, "
                            "the return policy, or your recent orders."
                        ),
                        "conversation_id": conversation_id,
                        "trace_id": "",
                        "run_id": "",
                        "mode": mode,
                        "entry_agent": "supervisor",
                        "terminal_state": "partial",
                        "steps": 0,
                        "trajectory": [],
                        "sub_results": [],
                        "citations": [],
                        "framework": "langgraph",
                        "latency_ms": 22000,
                        "persona": body.persona,
                        "suggestions": [
                            "Search for iPhone and give me the prices",
                            "What is the return policy?",
                            "Show my recent orders",
                        ],
                        "approval_id": "",
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            await queue.put({"event": "error", "data": {"message": str(exc)}})
        finally:
            await queue.put(None)

    async def generate():
        task = asyncio.create_task(runner())
        try:
            yield f"event: start\ndata: {json.dumps({'framework': 'langgraph', 'conversation_id': conversation_id})}\n\n"
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'], default=str)}\n\n"
            yield "event: done\ndata: {}\n\n"
        finally:
            await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ======================================================================
# traces
# ======================================================================

@router.get("/api/traces/{trace_id}", tags=["observability"])
async def get_trace(trace_id: str, session=Depends(get_session)):
    rows = (
        await session.execute(
            select(Span).where(Span.trace_id == trace_id).order_by(Span.started_at)
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(404, "no such trace")
    return {
        "trace_id": trace_id,
        "span_count": len(rows),
        "spans": [
            {
                "id": s.id,
                "parent_id": s.parent_id,
                "name": s.name,
                "kind": s.kind,
                "status": s.status,
                "duration_ms": s.duration_ms,
                "attributes": s.attributes,
            }
            for s in rows
        ],
    }


@router.get("/api/runs", tags=["observability"])
async def list_runs(limit: int = Query(default=25, le=200), session=Depends(get_session)):
    rows = (
        await session.execute(select(AgentRun).order_by(desc(AgentRun.created_at)).limit(limit))
    ).scalars().all()
    return {
        "runs": [
            {
                "run_id": r.id,
                "trace_id": r.trace_id,
                "mode": r.mode,
                "entry_agent": r.entry_agent,
                "terminal_state": r.terminal_state,
                "steps": r.steps,
                "latency_ms": r.latency_ms,
                "cost_inr": r.cost_inr,
                "prompt_version": r.prompt_version,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.get("/api/runs/{run_id}", tags=["observability"])
async def get_run(run_id: str, session=Depends(get_session)):
    run = await session.get(AgentRun, run_id)
    if not run:
        raise HTTPException(404, "no such run")
    tools = (
        await session.execute(select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at))
    ).scalars().all()
    guards = (
        await session.execute(select(GuardrailEvent).where(GuardrailEvent.run_id == run_id))
    ).scalars().all()
    return {
        "run": {
            "run_id": run.id, "trace_id": run.trace_id, "mode": run.mode,
            "terminal_state": run.terminal_state, "steps": run.steps,
            "latency_ms": run.latency_ms, "cost_inr": run.cost_inr,
        },
        "tool_calls": [
            {"tool": t.tool_name, "status": t.status, "verdict": t.gateway_verdict,
             "arguments": t.arguments, "denial_reason": t.denial_reason,
             "latency_ms": t.latency_ms}
            for t in tools
        ],
        "guardrail_events": [
            {"chain": g.chain, "check": g.check_name, "verdict": g.verdict, "reason": g.reason}
            for g in guards
        ],
    }


# ======================================================================
# approvals (human in the loop)
# ======================================================================

class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = ""


@router.get("/api/approvals", tags=["hitl"])
async def list_approvals(status: str = "pending", session=Depends(get_session)):
    rows = (
        await session.execute(
            select(Approval).where(Approval.status == status).order_by(desc(Approval.requested_at))
        )
    ).scalars().all()
    return {
        "approvals": [
            {
                "id": a.id, "run_id": a.run_id, "customer_id": a.customer_id,
                "tool": a.tool_name, "arguments": a.arguments,
                "reason": a.threshold_reason, "status": a.status,
                "executed": a.executed,
                "requested_at": a.requested_at.isoformat() if a.requested_at else None,
            }
            for a in rows
        ]
    }


@router.post("/api/approvals/{approval_id}", tags=["hitl"])
async def decide_approval(
    approval_id: str,
    body: ApprovalDecision,
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    from datetime import datetime

    approval = await session.get(Approval, approval_id)
    if not approval:
        raise HTTPException(404, "no such approval")
    if approval.status != "pending":
        # Idempotent: deciding twice must not execute twice.
        return {"id": approval.id, "status": approval.status, "executed": approval.executed,
                "idempotent": True}

    approval.decided_at = datetime.utcnow()
    approval.decided_by = principal.subject_id

    if body.decision == "reject":
        approval.status = "rejected"
        session.add(approval)
        await session.commit()
        return {"id": approval.id, "status": "rejected", "executed": False}

    # Approved: resume the suspended call with HITL disabled for this one
    # execution, as the human has now provided that authority.
    from app.security import customer as as_customer

    approval.status = "approved"
    gateway = ToolGateway(
        session, as_customer(approval.customer_id), run_id=approval.run_id, hitl_enabled=False
    )
    result = await gateway.call(approval.tool_name, approval.arguments)
    approval.executed = result.ok
    session.add(approval)
    await session.commit()
    return {
        "id": approval.id, "status": approval.status, "executed": approval.executed,
        "result": result.for_model(),
    }


# ======================================================================
# memory
# ======================================================================

@router.get("/api/memory", tags=["memory"])
async def list_memory(principal: Principal = Depends(require_principal), session=Depends(get_session)):
    from app.memory import MemoryStore

    rows = await MemoryStore(session, principal).read()
    return {
        "customer_id": principal.customer_id,
        "records": [
            {"id": r.id, "type": r.type, "value": r.value, "confidence": r.confidence,
             "last_confirmed_at": r.last_confirmed_at.isoformat() if r.last_confirmed_at else None}
            for r in rows
        ],
    }


@router.delete("/api/memory/{memory_id}", tags=["memory"])
async def forget_memory(
    memory_id: str,
    principal: Principal = Depends(require_principal),
    session=Depends(get_session),
):
    from app.memory import MemoryStore

    ok = await MemoryStore(session, principal).forget(memory_id)
    if not ok:
        raise HTTPException(404, "no such memory record")
    await session.commit()
    return {"forgotten": memory_id}


# ======================================================================
# tools + prompts (introspection for the console)
# ======================================================================

@router.get("/api/tools", tags=["platform"])
async def list_tools():
    return {
        "count": len(registry.names()),
        "tools": [
            {
                **t.spec(),
                "required_scopes": list(t.required_scopes),
                "ownership_enforced": t.ownership is not None,
                "hitl": t.hitl is not None,
                "timeout_ms": t.timeout_ms,
                "max_retries": t.max_retries,
                "untrusted_fields": list(t.untrusted_fields),
                "cost_class": t.cost_class,
            }
            for t in registry.all()
        ],
    }


@router.get("/api/prompts", tags=["platform"])
async def list_prompts():
    return {v: prompt_names(v) for v in available_versions()}


# ======================================================================
# products (shop)
# ======================================================================

@router.get("/api/products", tags=["shop"])
async def list_products(
    category: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    session=Depends(get_session),
):
    query = select(Product).limit(limit)
    if category:
        query = query.where(Product.category == category)
    rows = (await session.execute(query)).scalars().all()
    return {
        "products": [
            {
                "id": p.id,
                "sku": p.sku,
                "title": p.title,
                "brand": p.brand,
                "category": p.category,
                "price_inr": p.price_inr,
                "rating": p.rating,
                "attributes": p.attributes,
                "description": p.description_raw,
            }
            for p in rows
        ]
    }


@router.get("/api/products/{product_id}", tags=["shop"])
async def get_product(product_id: str, session=Depends(get_session)):
    product = await session.get(Product, product_id)
    if not product:
        raise HTTPException(404, "product not found")
    return {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "price_inr": product.price_inr,
        "rating": product.rating,
        "attributes": product.attributes,
        "description": product.description_raw,
    }


# ======================================================================
# customer orders
# ======================================================================

@router.get("/api/customers/{customer_id}/orders", tags=["shop"])
async def get_customer_orders(
    customer_id: str,
    principal: Principal = Depends(require_principal),
    session=Depends(get_session),
):
    if principal.kind != "operator":
        if not principal.customer_id:
            raise HTTPException(401, "sign in to view orders")
        if principal.customer_id != customer_id:
            raise HTTPException(403, "cannot view other customer's orders")
    
    rows = (
        await session.execute(
            select(Order).where(Order.customer_id == customer_id).order_by(desc(Order.placed_at))
        )
    ).scalars().all()
    
    return {
        "orders": [
            {
                "id": o.id,
                "customer_id": o.customer_id,
                "status": o.status,
                "total_inr": o.total_inr,
                "placed_at": o.placed_at.isoformat() if o.placed_at else None,
                "items": [
                    {"product_id": i.product_id, "qty": i.qty, "unit_price_inr": i.unit_price_inr}
                    for i in o.items
                ],
            }
            for o in rows
        ]
    }


@router.get("/api/orders/{order_id}", tags=["shop"])
async def get_order(
    order_id: str,
    principal: Principal = Depends(require_principal),
    session=Depends(get_session),
):
    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "order not found")
    if principal.kind != "operator":
        if not principal.customer_id:
            raise HTTPException(401, "sign in to view orders")
        if principal.customer_id != order.customer_id:
            raise HTTPException(403, "cannot view other customer's order")

    items = []
    for i in order.items:
        product = await session.get(Product, i.product_id)
        items.append({
            "product_id": i.product_id,
            "title": product.title if product else i.product_id,
            "brand": product.brand if product else "",
            "category": product.category if product else "",
            "qty": i.qty,
            "unit_price_inr": i.unit_price_inr,
            "line_total_inr": i.qty * i.unit_price_inr,
        })

    shipments = (
        await session.execute(select(Shipment).where(Shipment.order_id == order_id))
    ).scalars().all()
    shipping = []
    for s in shipments:
        events = (
            await session.execute(
                select(ShipmentEvent).where(ShipmentEvent.shipment_id == s.id).order_by(ShipmentEvent.occurred_at)
            )
        ).scalars().all()
        shipping.append({
            "shipment_id": s.id,
            "carrier": s.carrier,
            "awb": s.awb,
            "status": s.status,
            "exception_code": s.exception_code,
            "promised_at": s.promised_at.isoformat() if s.promised_at else None,
            "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
            "events": [
                {
                    "code": e.code,
                    "message": e.description,
                    "at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in events
            ],
        })

    payments = (
        await session.execute(select(Payment).where(Payment.order_id == order_id))
    ).scalars().all()

    status_timeline = ["placed", "packed", "shipped", "delivered"]
    current = order.status
    try:
        idx = status_timeline.index(current) if current in status_timeline else -1
    except ValueError:
        idx = -1

    return {
        "id": order.id,
        "customer_id": order.customer_id,
        "status": order.status,
        "total_inr": order.total_inr,
        "channel": order.channel,
        "placed_at": order.placed_at.isoformat() if order.placed_at else None,
        "items": items,
        "shipping": shipping,
        "payments": [
            {
                "payment_id": p.id,
                "method": p.method,
                "amount_inr": p.amount_inr,
                "status": p.status,
                "gateway_ref": p.gateway_ref,
            }
            for p in payments
        ],
        "status_timeline": status_timeline,
        "status_index": idx,
    }


class CreateOrderItem(BaseModel):
    product_id: str
    qty: int = 1
    unit_price_inr: Optional[int] = None


class CreateOrderRequest(BaseModel):
    items: list[CreateOrderItem]
    address: str = ""
    payment_method: str = "upi"


@router.post("/api/orders", tags=["shop"])
async def create_order(
    body: CreateOrderRequest,
    principal: Principal = Depends(require_principal),
    session=Depends(get_session),
):
    if not principal.customer_id:
        raise HTTPException(401, "authentication required to place order")
    
    if not body.items:
        raise HTTPException(400, "order must have at least one item")
    
    order_id = new_id("OR")
    total_inr = 0
    order_items = []
    
    for item in body.items:
        product = await session.get(Product, item.product_id)
        if not product:
            raise HTTPException(400, f"product not found: {item.product_id}")
        
        price = item.unit_price_inr if item.unit_price_inr else product.price_inr
        item_total = price * item.qty
        total_inr += item_total
        
        order_item = OrderItem(
            id=new_id("OI"),
            order_id=order_id,
            product_id=item.product_id,
            qty=item.qty,
            unit_price_inr=price,
        )
        order_items.append(order_item)
    
    order = Order(
        id=order_id,
        customer_id=principal.customer_id,
        status="placed",
        total_inr=total_inr,
        channel="web",
    )
    
    session.add(order)
    for item in order_items:
        session.add(item)
    
    payment = Payment(
        id=new_id("PAY"),
        order_id=order_id,
        method=body.payment_method,
        amount_inr=total_inr,
        status="captured",
    )
    session.add(payment)
    
    await session.commit()
    
    return {
        "order_id": order_id,
        "status": "placed",
        "total_inr": total_inr,
        "items_count": len(order_items),
        "message": "Order placed successfully",
    }


# ======================================================================
# admin orders
# ======================================================================

@router.get("/api/admin/orders", tags=["admin"])
async def admin_list_orders(
    status: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    query = select(Order).order_by(desc(Order.placed_at)).limit(limit)
    if status:
        query = query.where(Order.status == status)
    rows = (await session.execute(query)).scalars().all()
    
    return {
        "orders": [
            {
                "id": o.id,
                "customer_id": o.customer_id,
                "status": o.status,
                "total_inr": o.total_inr,
                "placed_at": o.placed_at.isoformat() if o.placed_at else None,
                "items": [
                    {"product_id": i.product_id, "title": i.product_id, "qty": i.qty}
                    for i in o.items
                ],
            }
            for o in rows
        ]
    }


class OrderStatusUpdate(BaseModel):
    status: str


@router.patch("/api/admin/orders/{order_id}/status", tags=["admin"])
async def update_order_status(
    order_id: str,
    body: OrderStatusUpdate,
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "order not found")
    
    valid_statuses = ["placed", "packed", "shipped", "delivered", "cancelled", "returned"]
    if body.status not in valid_statuses:
        raise HTTPException(400, f"invalid status, must be one of {valid_statuses}")
    
    order.status = body.status
    await session.commit()
    
    return {"id": order.id, "status": order.status, "updated": True}


@router.post("/api/admin/orders/{order_id}/cancel", tags=["admin"])
async def cancel_order(
    order_id: str,
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "order not found")
    
    order.status = "cancelled"
    await session.commit()
    
    return {"id": order.id, "status": "cancelled"}


# ======================================================================
# admin inventory
# ======================================================================

@router.get("/api/admin/inventory", tags=["admin"])
async def admin_list_inventory(
    warehouse: Optional[str] = None,
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    query = select(Inventory, Product).join(Product, Inventory.product_id == Product.id)
    if warehouse:
        query = query.where(Inventory.warehouse == warehouse)
    
    rows = (await session.execute(query)).all()
    
    return {
        "inventory": [
            {
                "product_id": inv.product_id,
                "product_title": prod.title,
                "sku": prod.sku,
                "warehouse": inv.warehouse,
                "qty_available": inv.qty_available,
                "qty_reserved": inv.qty_reserved,
            }
            for inv, prod in rows
        ]
    }


class InventoryUpdate(BaseModel):
    qty_change: int
    reason: str = "adjustment"


@router.patch("/api/admin/inventory/{product_id}", tags=["admin"])
async def update_inventory(
    product_id: str,
    body: InventoryUpdate,
    warehouse: str = "BLR-1",
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    inv = (
        await session.execute(
            select(Inventory).where(
                Inventory.product_id == product_id,
                Inventory.warehouse == warehouse
            )
        )
    ).scalar_one_or_none()
    
    if not inv:
        raise HTTPException(404, "inventory record not found")
    
    inv.qty_available = max(0, inv.qty_available + body.qty_change)
    await session.commit()
    
    return {
        "product_id": product_id,
        "warehouse": warehouse,
        "qty_available": inv.qty_available,
        "change": body.qty_change,
        "reason": body.reason,
    }


# ======================================================================
# admin products
# ======================================================================

class CreateProductRequest(BaseModel):
    sku: str
    title: str
    brand: str
    category: str
    price_inr: int
    rating: float = 4.0
    description: str = ""
    attributes: dict = {}


class UpdateProductRequest(BaseModel):
    title: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    price_inr: Optional[int] = None
    rating: Optional[float] = None
    description: Optional[str] = None
    attributes: Optional[dict] = None


@router.get("/api/admin/products", tags=["admin"])
async def admin_list_products(
    category: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    query = select(Product).limit(limit)
    if category:
        query = query.where(Product.category == category)
    rows = (await session.execute(query)).scalars().all()
    
    return {
        "products": [
            {
                "id": p.id,
                "sku": p.sku,
                "title": p.title,
                "brand": p.brand,
                "category": p.category,
                "price_inr": p.price_inr,
                "rating": p.rating,
                "attributes": p.attributes,
                "description": p.description_raw,
            }
            for p in rows
        ]
    }


@router.post("/api/admin/products", tags=["admin"])
async def create_product(
    body: CreateProductRequest,
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    existing = (
        await session.execute(select(Product).where(Product.sku == body.sku))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(400, f"product with SKU {body.sku} already exists")
    
    product = Product(
        id=new_id("PR"),
        sku=body.sku,
        title=body.title,
        brand=body.brand,
        category=body.category,
        price_inr=body.price_inr,
        rating=body.rating,
        description_raw=body.description,
        attributes=body.attributes,
    )
    session.add(product)
    
    inventory = Inventory(
        product_id=product.id,
        warehouse="BLR-1",
        qty_available=0,
        qty_reserved=0,
    )
    session.add(inventory)
    
    await session.commit()
    
    return {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "created": True,
    }


@router.put("/api/admin/products/{product_id}", tags=["admin"])
async def update_product(
    product_id: str,
    body: UpdateProductRequest,
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    product = await session.get(Product, product_id)
    if not product:
        raise HTTPException(404, "product not found")
    
    if body.title is not None:
        product.title = body.title
    if body.brand is not None:
        product.brand = body.brand
    if body.category is not None:
        product.category = body.category
    if body.price_inr is not None:
        product.price_inr = body.price_inr
    if body.rating is not None:
        product.rating = body.rating
    if body.description is not None:
        product.description_raw = body.description
    if body.attributes is not None:
        product.attributes = body.attributes
    
    await session.commit()
    
    return {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "updated": True,
    }


@router.delete("/api/admin/products/{product_id}", tags=["admin"])
async def delete_product(
    product_id: str,
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    product = await session.get(Product, product_id)
    if not product:
        raise HTTPException(404, "product not found")
    
    inventory_records = (
        await session.execute(select(Inventory).where(Inventory.product_id == product_id))
    ).scalars().all()
    for inv in inventory_records:
        await session.delete(inv)
    
    await session.delete(product)
    await session.commit()
    
    return {"id": product_id, "deleted": True}


class BulkProductItem(BaseModel):
    sku: str
    title: str
    brand: str
    category: str
    price_inr: int
    rating: float = 4.0
    description: str = ""
    qty_available: int = 0


class BulkUploadRequest(BaseModel):
    products: list[BulkProductItem]


@router.post("/api/admin/products/bulk", tags=["admin"])
async def bulk_upload_products(
    body: BulkUploadRequest,
    principal: Principal = Depends(require_operator),
    session=Depends(get_session),
):
    created = 0
    updated = 0
    errors = []
    
    for item in body.products:
        try:
            existing = (
                await session.execute(select(Product).where(Product.sku == item.sku))
            ).scalar_one_or_none()
            
            if existing:
                existing.title = item.title
                existing.brand = item.brand
                existing.category = item.category
                existing.price_inr = item.price_inr
                existing.rating = item.rating
                existing.description_raw = item.description
                updated += 1
            else:
                product = Product(
                    id=new_id("PR"),
                    sku=item.sku,
                    title=item.title,
                    brand=item.brand,
                    category=item.category,
                    price_inr=item.price_inr,
                    rating=item.rating,
                    description_raw=item.description,
                )
                session.add(product)
                
                inventory = Inventory(
                    product_id=product.id,
                    warehouse="BLR-1",
                    qty_available=item.qty_available,
                    qty_reserved=0,
                )
                session.add(inventory)
                created += 1
        except Exception as e:
            errors.append({"sku": item.sku, "error": str(e)})
    
    await session.commit()
    
    return {
        "created": created,
        "updated": updated,
        "errors": errors,
        "total_processed": created + updated,
    }


# ======================================================================
# evaluation console
# ======================================================================

@router.get("/api/datasets", tags=["evaluation"])
async def list_datasets():
    out = []
    for name in ds_mod.available():
        try:
            d = ds_mod.load(name)
            out.append(
                {"name": name, "kind": d.kind, "version": d.version, "items": len(d),
                 "checksum": d.checksum, "categories": d.categories(),
                 "problems": ds_mod.validate(d)}
            )
        except Exception as exc:  # noqa: BLE001
            out.append({"name": name, "error": str(exc)})
    return {"datasets": out}


class EvalRequest(BaseModel):
    dataset: str
    suite: Optional[str] = None
    prompt_version: str = "v1"
    provider: str = "mock"
    model: Optional[str] = None
    use_judge: bool = False
    concurrency: int = Field(default=4, ge=1, le=16)


@router.post("/api/eval/run", tags=["evaluation"])
async def run_eval(body: EvalRequest):
    dataset = ds_mod.load(body.dataset)
    engine = EvaluationEngine(
        session_scope,
        prompt_version=body.prompt_version,
        provider=body.provider,
        model=body.model,
        execution_tier="mocked" if body.provider.startswith("mock") else "live",
        use_judge=body.use_judge,
    )
    report = await engine.run(dataset, suite=body.suite, concurrency=body.concurrency)
    return {
        "summary": report.summary(),
        "gates": report.gate_report.as_dict() if report.gate_report else None,
        "failures": [
            {
                "item_id": o.item_id,
                "category": o.category,
                "error": o.error,
                "trace_id": o.trace_id,
                "answer": (o.result.answer[:400] if o.result else ""),
                "failed": [{"name": s.name, "value": s.value, "detail": s.detail} for s in o.failures()],
            }
            for o in report.failures()[:50]
        ],
    }


@router.get("/api/eval/runs", tags=["evaluation"])
async def list_eval_runs(limit: int = Query(default=25, le=200), session=Depends(get_session)):
    rows = (
        await session.execute(select(EvalRun).order_by(desc(EvalRun.started_at)).limit(limit))
    ).scalars().all()
    return {"runs": [{"id": r.id, **r.summary} for r in rows]}


@router.get("/api/eval/runs/{run_id}", tags=["evaluation"])
async def get_eval_run(run_id: str, session=Depends(get_session)):
    run = await session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(404, "no such eval run")
    results = (
        await session.execute(select(EvalResult).where(EvalResult.eval_run_id == run_id))
    ).scalars().all()
    gates = (
        await session.execute(select(GateResult).where(GateResult.eval_run_id == run_id))
    ).scalars().all()
    return {
        "run": {"id": run.id, **run.summary},
        "gates": [
            {"gate": g.gate_name, "metric": g.metric, "observed": g.observed,
             "threshold": g.threshold, "comparator": g.comparator, "passed": g.passed,
             "blocks": g.blocks}
            for g in gates
        ],
        "results": [
            {"item_id": r.item_id, "evaluator": r.evaluator_name, "score": r.score,
             "passed": r.passed, "trace_id": r.trace_id, "detail": r.detail}
            for r in results
        ],
    }


# ======================================================================
# feedback (thumbs up/down)
# ======================================================================

class FeedbackRequest(BaseModel):
    run_id: str
    conversation_id: str = ""
    rating: int = Field(ge=1, le=5)  # 1=bad, 5=good
    category: str = ""  # wrong_answer, slow, helpful, unhelpful
    comment: str = ""


@router.post("/api/feedback", tags=["feedback"])
async def submit_feedback(
    body: FeedbackRequest,
    principal: Principal = Depends(require_principal),
    session=Depends(get_session),
):
    from app.models import Feedback, AgentRun
    
    run = await session.get(AgentRun, body.run_id)
    feedback = Feedback(
        id=new_id("FB"),
        run_id=body.run_id,
        conversation_id=body.conversation_id,
        customer_id=principal.customer_id or "guest",
        rating=body.rating,
        category=body.category,
        comment=body.comment,
        message_text="",
        answer_text=run.summary.get("answer", "")[:500] if run and run.summary else "",
        mode=run.mode if run else "",
    )
    session.add(feedback)
    await session.commit()
    return {"id": feedback.id, "status": "recorded"}


@router.get("/api/feedback", tags=["feedback"])
async def list_feedback(
    limit: int = Query(default=50, le=500),
    rating: Optional[int] = None,
    session=Depends(get_session),
):
    from app.models import Feedback
    
    query = select(Feedback).order_by(desc(Feedback.created_at)).limit(limit)
    if rating:
        query = query.where(Feedback.rating == rating)
    rows = (await session.execute(query)).scalars().all()
    
    return {
        "feedback": [
            {
                "id": f.id,
                "run_id": f.run_id,
                "rating": f.rating,
                "category": f.category,
                "comment": f.comment,
                "mode": f.mode,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in rows
        ],
        "summary": {
            "total": len(rows),
            "positive": sum(1 for f in rows if f.rating >= 4),
            "negative": sum(1 for f in rows if f.rating <= 2),
        }
    }


# ======================================================================
# regression detection
# ======================================================================

@router.get("/api/eval/regression/{run_id}", tags=["evaluation"])
async def check_regression(
    run_id: str,
    baseline_id: Optional[str] = None,
    session=Depends(get_session),
):
    from app.evaluation.regression import RegressionDetector
    
    detector = RegressionDetector(session)
    report = await detector.compare(run_id, baseline_id)
    return report.to_dict()


# ======================================================================
# model benchmarking
# ======================================================================

class BenchmarkRequest(BaseModel):
    dataset: str = "benchmark/model_comparison"
    models: list[str] = Field(default=["openai:gpt-5.4"])
    concurrency: int = Field(default=2, ge=1, le=8)


@router.post("/api/benchmark/run", tags=["benchmark"])
async def run_benchmark(body: BenchmarkRequest):
    from app.evaluation.benchmark import ModelBenchmark
    from app.models import BenchmarkRun
    
    benchmark = ModelBenchmark(session_scope)
    result = await benchmark.run(
        dataset_name=body.dataset,
        models=body.models,
        concurrency=body.concurrency
    )
    return result.to_dict()


@router.get("/api/benchmark/runs", tags=["benchmark"])
async def list_benchmark_runs(
    limit: int = Query(default=25, le=100),
    session=Depends(get_session),
):
    from app.models import BenchmarkRun
    
    rows = (
        await session.execute(
            select(BenchmarkRun).order_by(desc(BenchmarkRun.started_at)).limit(limit)
        )
    ).scalars().all()
    return {
        "runs": [
            {
                "id": r.id,
                "dataset": r.dataset_name,
                "models": r.models_compared,
                "winner_accuracy": r.winner_accuracy,
                "winner_latency": r.winner_latency,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            for r in rows
        ]
    }
