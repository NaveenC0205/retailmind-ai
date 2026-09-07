"""Tool implementations + registration.

Handlers are plain async functions over the business tables. They never do
authorization -- that is the gateway's job and duplicating it here is how
"defence in depth" turns into "neither layer really checks".
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Customer,
    Inventory,
    MemoryRecord,
    Order,
    OrderItem,
    Payment,
    Product,
    Promotion,
    Refund,
    Return,
    Shipment,
    ShipmentEvent,
    SupportTicket,
    new_id,
)
from app.tools.contracts import (
    AddProductIn,
    ApproveOrderIn,
    CancelOrderIn,
    CompareProductsIn,
    CouponIn,
    CreateOrderIn,
    CreateReturnIn,
    CreateTicketIn,
    CustomerIdIn,
    EmptyIn,
    GetOrdersIn,
    GetPendingOrdersIn,
    KBSearchIn,
    LowStockIn,
    OrderIdIn,
    PaymentIdIn,
    ProductIdIn,
    RecommendationsIn,
    RejectOrderIn,
    ReturnEligibilityIn,
    SearchProductsIn,
    ShipmentIdIn,
    TicketIdIn,
    ToolContract,
    registry,
)


class ToolFailure(RuntimeError):
    """Typed, agent-safe failure. The message reaches the model; internals never do."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _product_row(p: Product) -> dict:
    return {
        "product_id": p.id,
        "sku": p.sku,
        "title": p.title,
        "brand": p.brand,
        "category": p.category,
        "price_inr": p.price_inr,
        "rating": p.rating,
        "attributes": p.attributes,
        # Seller-authored. Declared in untrusted_fields on the contract.
        "description": p.description_raw,
    }


# ----------------------------------------------------------------------
# ownership resolvers
# ----------------------------------------------------------------------

async def own_order(session, principal, args: dict) -> Optional[str]:
    row = await session.get(Order, args.get("order_id", ""))
    return row.customer_id if row else None


async def own_customer_arg(session, principal, args: dict) -> Optional[str]:
    return args.get("customer_id")


async def own_shop_catalogue(session, principal, args: dict) -> Optional[str]:
    """The catalogue belongs to the shop, not to any customer. Only an
    operator passes owns_customer for this sentinel owner."""
    return "shop-catalogue"


async def own_shipment(session, principal, args: dict) -> Optional[str]:
    ship = await session.get(Shipment, args.get("shipment_id", ""))
    if not ship:
        return None
    order = await session.get(Order, ship.order_id)
    return order.customer_id if order else None


async def own_payment(session, principal, args: dict) -> Optional[str]:
    pay = await session.get(Payment, args.get("payment_id", ""))
    if not pay:
        return None
    order = await session.get(Order, pay.order_id)
    return order.customer_id if order else None


async def own_ticket(session, principal, args: dict) -> Optional[str]:
    t = await session.get(SupportTicket, args.get("ticket_id", ""))
    return t.customer_id if t else None


# ----------------------------------------------------------------------
# product
# ----------------------------------------------------------------------

def _normalize_search_query(q: str) -> str:
    from app.nlp.understand import rewrite_query

    return rewrite_query(q or "")


def _token_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b or abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _search_relevance(p: Product, q: str, terms: list[str]) -> int:
    hay = f"{p.title} {p.brand} {p.category} {json.dumps(p.attributes or {}, default=str)}".lower()
    title_words = re.findall(r"[a-z0-9]+", f"{p.title} {p.brand}".lower())
    score = 0
    for t in terms:
        if t in hay:
            score += 3
        elif len(t) >= 4 and any(t[:4] in part for part in hay.split()):
            score += 1
        elif len(t) >= 4:
            best = min((_token_distance(t, w) for w in title_words), default=99)
            if best <= 2:
                score += 2
    if "iphone" in q or "apple" in q:
        if p.category == "phones" and p.brand.lower() == "apple":
            score += 8
        if "iphone" in (p.title or "").lower():
            score += 10
    if "phone" in q and p.category == "phones":
        score += 2
    return score


async def search_products(session, principal, args: dict) -> dict:
    stmt = select(Product)
    q = _normalize_search_query(args.get("query") or "")
    # Infer Apple phones from iPhone-ish queries even with typos.
    category = args.get("category")
    brand = args.get("brand")
    if not category and "iphone" in q:
        category = "phones"
        brand = brand or "Apple"
    if category:
        stmt = stmt.where(Product.category == category)
    if brand:
        stmt = stmt.where(Product.brand == brand)
    if args.get("min_price_inr") is not None:
        stmt = stmt.where(Product.price_inr >= args["min_price_inr"])
    if args.get("max_price_inr"):
        stmt = stmt.where(Product.price_inr <= args["max_price_inr"])
    if args.get("min_rating"):
        stmt = stmt.where(Product.rating >= args["min_rating"])
    rows = (await session.execute(stmt)).scalars().all()

    min_ram = args.get("min_ram_gb")
    min_storage = args.get("min_storage_gb")
    requires_anc = args.get("requires_anc")
    attr_kw = (args.get("attribute_contains") or "").lower().strip()

    def attrs_ok(p: Product) -> bool:
        a = p.attributes or {}
        if min_ram is not None and int(a.get("ram_gb") or 0) < int(min_ram):
            return False
        if min_storage is not None and int(a.get("storage_gb") or 0) < int(min_storage):
            return False
        if requires_anc is True and not a.get("anc"):
            return False
        if requires_anc is False and a.get("anc"):
            return False
        if attr_kw:
            hay = json.dumps(a, default=str).lower()
            if attr_kw not in hay:
                return False
        return True

    rows = [p for p in rows if attrs_ok(p)]

    if q:
        stop = {"the", "and", "for", "give", "me", "search", "show", "find", "with", "from", "prices", "price"}
        terms = [t for t in q.split() if len(t) > 2 and t not in stop]
        rows = sorted(rows, key=lambda p: (_search_relevance(p, q, terms), p.rating), reverse=True)
        rows = [p for p in rows if _search_relevance(p, q, terms) > 0] or rows
    else:
        rows = sorted(rows, key=lambda p: p.rating, reverse=True)

    limit = int(args.get("limit", 5))
    return {"count": len(rows[:limit]), "products": [_product_row(p) for p in rows[:limit]]}


async def get_product(session, principal, args: dict) -> dict:
    p = await session.get(Product, args["product_id"])
    if not p:
        raise ToolFailure("not_found", f"No product with id {args['product_id']}")
    return _product_row(p)


async def compare_products(session, principal, args: dict) -> dict:
    ids = args["product_ids"]
    rows = (await session.execute(select(Product).where(Product.id.in_(ids)))).scalars().all()
    if not rows:
        raise ToolFailure("not_found", "None of those product ids exist")
    axes = ["price_inr", "rating"]
    attr_keys: set[str] = set()
    for p in rows:
        attr_keys |= set((p.attributes or {}).keys())
    table = []
    for p in rows:
        row = {"product_id": p.id, "title": p.title, "brand": p.brand}
        for a in axes:
            row[a] = getattr(p, a)
        for k in sorted(attr_keys):
            row[k] = (p.attributes or {}).get(k)
        table.append(row)
    cheapest = min(rows, key=lambda p: p.price_inr)
    best_rated = max(rows, key=lambda p: p.rating)
    return {
        "comparison": table,
        "cheapest_product_id": cheapest.id,
        "best_rated_product_id": best_rated.id,
    }


async def check_inventory(session, principal, args: dict) -> dict:
    rows = (
        await session.execute(select(Inventory).where(Inventory.product_id == args["product_id"]))
    ).scalars().all()
    total = sum(r.qty_available - r.qty_reserved for r in rows)
    return {
        "product_id": args["product_id"],
        "available": max(0, total),
        "in_stock": total > 0,
        "warehouses": [{"warehouse": r.warehouse, "available": r.qty_available} for r in rows],
    }


# ----------------------------------------------------------------------
# customer
# ----------------------------------------------------------------------

async def get_customer(session, principal, args: dict) -> dict:
    c = await session.get(Customer, args["customer_id"])
    if not c:
        raise ToolFailure("not_found", "No such customer")
    return {"customer_id": c.id, "name": c.name, "tier": c.tier, "region": c.region}


async def get_customer_preferences(session, principal, args: dict) -> dict:
    rows = (
        await session.execute(
            select(MemoryRecord)
            .where(MemoryRecord.customer_id == args["customer_id"])
            .where(MemoryRecord.archived_at.is_(None))
        )
    ).scalars().all()
    now = datetime.utcnow()
    live = [r for r in rows if not r.decay_at or r.decay_at > now]
    return {
        "customer_id": args["customer_id"],
        "preferences": [
            {"type": r.type, "value": r.value, "confidence": r.confidence} for r in live
        ],
    }


# ----------------------------------------------------------------------
# order
# ----------------------------------------------------------------------

def _order_row(o: Order, titles: dict[str, str] | None = None) -> dict:
    titles = titles or {}
    return {
        "order_id": o.id,
        "status": o.status,
        "total_inr": o.total_inr,
        "placed_at": o.placed_at.isoformat() if o.placed_at else None,
        "items": [
            {
                "order_item_id": i.id,
                "product_id": i.product_id,
                "title": titles.get(i.product_id, i.product_id),
                "qty": i.qty,
                "unit_price_inr": i.unit_price_inr,
            }
            for i in (o.items or [])
        ],
    }


async def _titles_for(session, product_ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pid in {p for p in product_ids if p}:
        p = await session.get(Product, pid)
        if p:
            out[pid] = p.title
    return out


async def get_orders(session, principal, args: dict) -> dict:
    rows = (
        await session.execute(
            select(Order)
            .where(Order.customer_id == args["customer_id"])
            .order_by(Order.placed_at.desc())
            .limit(int(args.get("limit", 5)))
        )
    ).scalars().all()
    pids = [i.product_id for o in rows for i in (o.items or [])]
    titles = await _titles_for(session, pids)
    return {"count": len(rows), "orders": [_order_row(o, titles) for o in rows]}


async def get_order(session, principal, args: dict) -> dict:
    o = await session.get(Order, args["order_id"])
    if not o:
        raise ToolFailure("not_found", f"No order with id {args['order_id']}")
    titles = await _titles_for(session, [i.product_id for i in (o.items or [])])
    return _order_row(o, titles)


async def cancel_order(session, principal, args: dict) -> dict:
    o = await session.get(Order, args["order_id"])
    if not o:
        raise ToolFailure("not_found", f"No order with id {args['order_id']}")
    if o.status in ("shipped", "delivered"):
        return {
            "order_id": o.id,
            "cancelled": False,
            "reason": "already_shipped",
            "message": "This order has already shipped and cannot be cancelled. A return can be started after delivery.",
        }
    if o.status == "cancelled":
        return {"order_id": o.id, "cancelled": True, "idempotent": True}
    o.status = "cancelled"
    session.add(o)
    return {"order_id": o.id, "cancelled": True, "refund_initiated": True}


async def create_order(session, principal, args: dict) -> dict:
    items = args.get("items", [])
    if not items:
        raise ToolFailure("invalid_input", "Order must have at least one item")
    
    customer_id = args["customer_id"]
    customer = await session.get(Customer, customer_id)
    if not customer:
        raise ToolFailure("not_found", f"No customer with id {customer_id}")
    
    order_id = new_id("OR")
    total_inr = 0
    order_items = []
    
    for item in items:
        product = await session.get(Product, item["product_id"])
        if not product:
            raise ToolFailure("not_found", f"No product with id {item['product_id']}")
        
        qty = int(item.get("qty", 1))
        item_total = product.price_inr * qty
        total_inr += item_total
        
        oi = OrderItem(
            id=new_id("OI"),
            order_id=order_id,
            product_id=item["product_id"],
            qty=qty,
            unit_price_inr=product.price_inr,
        )
        order_items.append(oi)
    
    order = Order(
        id=order_id,
        customer_id=customer_id,
        status="placed",
        total_inr=total_inr,
        channel="agent",
    )
    session.add(order)
    for oi in order_items:
        session.add(oi)
    
    payment = Payment(
        id=new_id("PAY"),
        order_id=order_id,
        method=args.get("payment_method", "upi"),
        amount_inr=total_inr,
        status="captured",
    )
    session.add(payment)

    item_out = []
    for oi in order_items:
        p = await session.get(Product, oi.product_id)
        item_out.append(
            {
                "product_id": oi.product_id,
                "title": p.title if p else oi.product_id,
                "qty": oi.qty,
                "unit_price_inr": oi.unit_price_inr,
            }
        )

    method = payment.method
    return {
        "order_id": order_id,
        "status": "placed",
        "total_inr": total_inr,
        "payment_method": method,
        "payment_id": payment.id,
        "payment_status": payment.status,
        "items_count": len(order_items),
        "items": item_out,
        "message": (
            f"Order {order_id} placed. Total Rs {total_inr}. "
            f"Paid via {method} ({payment.status})."
        ),
    }


PAYMENT_METHODS = (
    {"id": "upi", "label": "UPI", "description": "Google Pay, PhonePe, Paytm — instant capture"},
    {"id": "card", "label": "Credit / Debit card", "description": "Visa, Mastercard, RuPay"},
    {"id": "cod", "label": "Cash on delivery", "description": "Pay the courier in cash when the parcel arrives"},
)


async def list_payment_methods(session, principal, args: dict) -> dict:
    """Catalogue of checkout payment options the agent must offer the shopper."""
    return {
        "methods": list(PAYMENT_METHODS),
        "default": "upi",
        "message": "Choose UPI, Card, or Cash on delivery (COD) to complete checkout.",
    }


# ----------------------------------------------------------------------
# shipping
# ----------------------------------------------------------------------

async def get_shipment(session, principal, args: dict) -> dict:
    rows = (
        await session.execute(select(Shipment).where(Shipment.order_id == args["order_id"]))
    ).scalars().all()
    if not rows:
        raise ToolFailure("not_found", "No shipment for that order yet")
    s = rows[0]
    return {
        "shipment_id": s.id,
        "order_id": s.order_id,
        "carrier": s.carrier,
        "awb": s.awb,
        "status": s.status,
        "exception_code": s.exception_code,
        "promised_at": s.promised_at.isoformat() if s.promised_at else None,
        "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
        "days_late": _days_late(s),
    }


def _days_late(s: Shipment) -> int:
    if not s.promised_at:
        return 0
    end = s.delivered_at or datetime.utcnow()
    return max(0, (end - s.promised_at).days)


async def track_shipment(session, principal, args: dict) -> dict:
    s = await session.get(Shipment, args["shipment_id"])
    if not s:
        raise ToolFailure("not_found", "No such shipment")
    events = (
        await session.execute(
            select(ShipmentEvent)
            .where(ShipmentEvent.shipment_id == s.id)
            .order_by(ShipmentEvent.occurred_at)
        )
    ).scalars().all()
    return {
        "shipment_id": s.id,
        "status": s.status,
        "exception_code": s.exception_code,
        "days_late": _days_late(s),
        "events": [
            {"code": e.code, "description": e.description, "at": e.occurred_at.isoformat()}
            for e in events
        ],
    }


# ----------------------------------------------------------------------
# payment
# ----------------------------------------------------------------------

async def get_payment(session, principal, args: dict) -> dict:
    rows = (
        await session.execute(select(Payment).where(Payment.order_id == args["order_id"]))
    ).scalars().all()
    if not rows:
        raise ToolFailure("not_found", "No payment for that order")
    p = rows[0]
    return {
        "payment_id": p.id,
        "order_id": p.order_id,
        "method": p.method,
        "amount_inr": p.amount_inr,
        "status": p.status,
    }


async def get_refund(session, principal, args: dict) -> dict:
    rows = (
        await session.execute(select(Refund).where(Refund.payment_id == args["payment_id"]))
    ).scalars().all()
    if not rows:
        return {"payment_id": args["payment_id"], "refunds": [], "message": "No refund raised yet."}
    return {
        "payment_id": args["payment_id"],
        "refunds": [
            {"refund_id": r.id, "amount_inr": r.amount_inr, "status": r.status, "reason": r.reason}
            for r in rows
        ],
    }


# ----------------------------------------------------------------------
# returns
# ----------------------------------------------------------------------

RETURN_WINDOW_DAYS = {"electronics": 10, "laptops": 10, "phones": 10, "audio": 10,
                      "monitors": 10, "apparel": 30, "default": 7}


async def check_return_eligibility(session, principal, args: dict) -> dict:
    o = await session.get(Order, args["order_id"])
    if not o:
        raise ToolFailure("not_found", "No such order")
    ships = (await session.execute(select(Shipment).where(Shipment.order_id == o.id))).scalars().all()
    delivered_at = ships[0].delivered_at if ships else None
    if o.status != "delivered" or not delivered_at:
        return {
            "order_id": o.id,
            "eligible": False,
            "reason": "not_delivered",
            "message": "Returns can only be started after delivery.",
        }
    item_id = args.get("order_item_id") or (o.items[0].id if o.items else "")
    item = next((i for i in (o.items or []) if i.id == item_id), None)
    category = "default"
    if item:
        p = await session.get(Product, item.product_id)
        category = p.category if p else "default"
    window = RETURN_WINDOW_DAYS.get(category, RETURN_WINDOW_DAYS["default"])
    ends = delivered_at + timedelta(days=window)
    eligible = datetime.utcnow() <= ends
    return {
        "order_id": o.id,
        "order_item_id": item_id,
        "category": category,
        "window_days": window,
        "window_ends_at": ends.isoformat(),
        "eligible": eligible,
        "reason": "within_window" if eligible else "window_expired",
    }


async def create_return(session, principal, args: dict) -> dict:
    elig = await check_return_eligibility(session, principal, args)
    if not elig.get("eligible"):
        return {"created": False, "reason": elig.get("reason"), "message": elig.get("message", "Not eligible for return.")}
    r = Return(
        id=new_id("RT"),
        order_id=args["order_id"],
        order_item_id=elig["order_item_id"],
        status="requested",
        reason=args.get("reason", "not_as_described"),
        window_ends_at=datetime.fromisoformat(elig["window_ends_at"]),
    )
    session.add(r)
    return {"created": True, "return_id": r.id, "status": r.status}


# ----------------------------------------------------------------------
# support
# ----------------------------------------------------------------------

async def create_support_ticket(session, principal, args: dict) -> dict:
    t = SupportTicket(
        id=new_id("TKT"),
        customer_id=args["customer_id"],
        order_id=args.get("order_id") or None,
        category=args.get("category", "general"),
        priority=args.get("priority", "normal"),
        summary=(args.get("summary") or "")[:500],
        created_by="agent",
    )
    session.add(t)
    return {"ticket_id": t.id, "status": t.status, "category": t.category}


async def get_support_ticket(session, principal, args: dict) -> dict:
    t = await session.get(SupportTicket, args["ticket_id"])
    if not t:
        raise ToolFailure("not_found", "No such ticket")
    return {
        "ticket_id": t.id,
        "status": t.status,
        "category": t.category,
        "priority": t.priority,
        "summary": t.summary,
    }


# ----------------------------------------------------------------------
# promotions
# ----------------------------------------------------------------------

async def get_active_promotions(session, principal, args: dict) -> dict:
    now = datetime.utcnow()
    rows = (await session.execute(select(Promotion))).scalars().all()
    live = [p for p in rows if p.starts_at <= now <= p.ends_at]
    return {
        "count": len(live),
        "promotions": [
            {"code": p.code, "kind": p.kind, "value": p.value, "conditions": p.conditions}
            for p in live
        ],
    }


async def validate_coupon(session, principal, args: dict) -> dict:
    rows = (
        await session.execute(select(Promotion).where(Promotion.code == args["code"].upper()))
    ).scalars().all()
    if not rows:
        return {"code": args["code"], "valid": False, "reason": "unknown_code"}
    p = rows[0]
    now = datetime.utcnow()
    if not (p.starts_at <= now <= p.ends_at):
        return {"code": p.code, "valid": False, "reason": "expired"}
    min_total = int((p.conditions or {}).get("min_total_inr", 0))
    total = int(args.get("order_total_inr", 0))
    if total and total < min_total:
        return {"code": p.code, "valid": False, "reason": "below_minimum", "min_total_inr": min_total}
    discount = total * p.value // 100 if p.kind == "percent" else p.value
    return {"code": p.code, "valid": True, "discount_inr": discount, "kind": p.kind}


# ----------------------------------------------------------------------
# recommendations
# ----------------------------------------------------------------------

async def get_recommendations(session, principal, args: dict) -> dict:
    prefs = await get_customer_preferences(session, principal, {"customer_id": args["customer_id"]})
    brand = next(
        (p["value"] for p in prefs["preferences"] if p["type"] == "brand_preference"), None
    )
    stmt = select(Product)
    if args.get("category"):
        stmt = stmt.where(Product.category == args["category"])
    rows = (await session.execute(stmt)).scalars().all()
    rows = sorted(rows, key=lambda p: ((p.brand == brand), p.rating), reverse=True)
    limit = int(args.get("limit", 3))
    return {
        "applied_preference": {"brand_preference": brand} if brand else {},
        "recommendations": [_product_row(p) for p in rows[:limit]],
    }


# ----------------------------------------------------------------------
# knowledge base
# ----------------------------------------------------------------------

async def search_knowledge_base(session, principal, args: dict) -> dict:
    from app.rag.retrieve import Retriever

    hits = await Retriever(session).search(
        args["query"],
        rerank_k=int(args.get("top_k", 6)),
        families=[args["family"]] if args.get("family") else None,
        min_trust="untrusted",
    )
    return {
        "query": args["query"],
        "chunks": [
            {
                "id": h.chunk_id,
                "document_id": h.document_id,
                "heading": h.heading,
                "content": h.content,
                "trust": h.trust,
                "score": h.rerank_score,
            }
            for h in hits
        ],
    }


async def retrieve_policy(session, principal, args: dict) -> dict:
    """Policy lookup. Trust floor is `trusted`: seller copy and product
    descriptions can never answer a policy question, no matter how well they
    match the query. This one line is the RAG-poisoning defence."""
    from app.rag.retrieve import Retriever

    hits = await Retriever(session).search(
        args["query"],
        rerank_k=int(args.get("top_k", 6)),
        families=[args["family"]] if args.get("family") else None,
        min_trust="trusted",
    )
    return {
        "query": args["query"],
        "chunks": [
            {
                "id": h.chunk_id,
                "document_id": h.document_id,
                "heading": h.heading,
                "content": h.content,
                "trust": h.trust,
                "score": h.rerank_score,
            }
            for h in hits
        ],
    }


# ----------------------------------------------------------------------
# admin tools
# ----------------------------------------------------------------------

async def get_pending_orders(session, principal, args: dict) -> dict:
    """Get orders with a specific status (default: placed) for admin review."""
    status = args.get("status", "placed")
    limit = int(args.get("limit", 10))
    
    rows = (
        await session.execute(
            select(Order)
            .where(Order.status == status)
            .order_by(Order.placed_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    
    return {
        "status_filter": status,
        "count": len(rows),
        "orders": [
            {
                "order_id": o.id,
                "customer_id": o.customer_id,
                "status": o.status,
                "total_inr": o.total_inr,
                "placed_at": o.placed_at.isoformat() if o.placed_at else None,
                "items_count": len(o.items) if o.items else 0,
            }
            for o in rows
        ],
    }


async def approve_order(session, principal, args: dict) -> dict:
    """Approve an order and move it to packed status."""
    order = await session.get(Order, args["order_id"])
    if not order:
        raise ToolFailure("not_found", f"No order with id {args['order_id']}")
    
    if order.status != "placed":
        return {
            "order_id": order.id,
            "approved": False,
            "reason": f"Order is already in '{order.status}' status, cannot approve",
        }
    
    order.status = "packed"
    session.add(order)
    
    return {
        "order_id": order.id,
        "approved": True,
        "new_status": "packed",
        "message": f"Order {order.id} approved and moved to packed status",
    }


async def reject_order(session, principal, args: dict) -> dict:
    """Reject/cancel an order with a reason."""
    order = await session.get(Order, args["order_id"])
    if not order:
        raise ToolFailure("not_found", f"No order with id {args['order_id']}")
    
    if order.status in ("shipped", "delivered"):
        return {
            "order_id": order.id,
            "rejected": False,
            "reason": f"Order is already '{order.status}', cannot reject",
        }
    
    if order.status == "cancelled":
        return {
            "order_id": order.id,
            "rejected": True,
            "idempotent": True,
            "message": "Order was already cancelled",
        }
    
    order.status = "cancelled"
    session.add(order)
    
    return {
        "order_id": order.id,
        "rejected": True,
        "new_status": "cancelled",
        "rejection_reason": args.get("reason", "admin_rejected"),
        "message": f"Order {order.id} has been rejected/cancelled",
    }


async def list_low_stock(session, principal, args: dict) -> dict:
    """Seller inventory: products whose available qty is at or below threshold."""
    threshold = int(args.get("threshold", 20))
    limit = int(args.get("limit", 10))
    rows = (await session.execute(select(Inventory))).scalars().all()
    by_product: dict[str, int] = {}
    warehouses: dict[str, list] = {}
    for r in rows:
        avail = max(0, int(r.qty_available) - int(r.qty_reserved or 0))
        by_product[r.product_id] = by_product.get(r.product_id, 0) + avail
        warehouses.setdefault(r.product_id, []).append(
            {"warehouse": r.warehouse, "available": avail}
        )
    low = [(pid, qty) for pid, qty in by_product.items() if qty <= threshold]
    low.sort(key=lambda x: x[1])
    items = []
    for pid, qty in low[:limit]:
        p = await session.get(Product, pid)
        items.append(
            {
                "product_id": pid,
                "title": p.title if p else pid,
                "brand": p.brand if p else "",
                "category": p.category if p else "",
                "available": qty,
                "warehouses": warehouses.get(pid, []),
            }
        )
    return {"threshold": threshold, "count": len(items), "items": items}


async def add_product(session, principal, args: dict) -> dict:
    """Seller-only: create a catalogue product with an inventory row."""
    if principal.kind != "operator":
        raise ToolFailure("forbidden", "Only the shop owner can add products.")
    title = str(args.get("title") or "").strip()
    price = int(args.get("price_inr") or 0)
    if not title or price <= 0:
        raise ToolFailure("invalid_input", "A product needs at least a title and a price in INR.")
    category = str(args.get("category") or "accessories").strip().lower()
    if category not in ("laptops", "phones", "audio", "monitors", "accessories"):
        category = "accessories"
    sku = str(args.get("sku") or "").strip().upper() or f"SKU-{new_id('X')[-8:].upper()}"
    existing = (
        await session.execute(select(Product).where(Product.sku == sku))
    ).scalar_one_or_none()
    if existing:
        raise ToolFailure("invalid_input", f"A product with SKU {sku} already exists ({existing.id}).")
    product = Product(
        id=new_id("PR"),
        sku=sku,
        title=title,
        brand=str(args.get("brand") or "ShopZone").strip() or "ShopZone",
        category=category,
        price_inr=price,
        rating=0.0,
        description_raw=str(args.get("description") or ""),
        attributes=args.get("attributes") or {},
    )
    session.add(product)
    session.add(
        Inventory(
            product_id=product.id,
            warehouse="BLR-1",
            qty_available=int(args.get("initial_stock") or 0),
            qty_reserved=0,
        )
    )
    await session.commit()
    return {
        "created": True,
        "product_id": product.id,
        "sku": product.sku,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "price_inr": product.price_inr,
        "initial_stock": int(args.get("initial_stock") or 0),
        "message": f"Added {product.title} ({product.id}) to the catalogue at ₹{price:,}.",
    }


# ======================================================================
# registration
# ======================================================================

def _register_all() -> None:
    if registry.names():
        return

    s = get_settings()

    def hitl_high_value_cancel(args: dict, ctx: dict) -> bool:
        return int(ctx.get("order_total_inr", 0)) > s.hitl_refund_threshold_inr

    R = registry.register
    R(ToolContract("search_products", "Search electronics catalogue by text, category (laptops/phones/audio/monitors/accessories), brand, min/max price (INR), min rating, min_ram_gb, min_storage_gb, requires_anc, or attribute_contains (feature keyword). Returns matching products with price, rating, attributes and description.", SearchProductsIn, ("products:read",), search_products, untrusted_fields=("description",)))
    R(ToolContract("get_product", "Fetch one product by its product_id, including attributes and the seller-written description.", ProductIdIn, ("products:read",), get_product, untrusted_fields=("description",)))
    R(ToolContract("compare_products", "Compare 2-5 products side by side on price, rating and shared attributes.", CompareProductsIn, ("products:read",), compare_products, untrusted_fields=("description",)))
    R(ToolContract("check_inventory", "Check stock availability for one product across warehouses.", ProductIdIn, ("products:read",), check_inventory))

    R(ToolContract("get_customer", "Fetch the calling customer's own profile: name, loyalty tier and region. Cannot be used to look up anyone else.", CustomerIdIn, ("orders:read",), get_customer, ownership=own_customer_arg))
    R(ToolContract("get_customer_preferences", "Fetch the calling customer's stored long-term preferences.", CustomerIdIn, ("memory:read",), get_customer_preferences, ownership=own_customer_arg))

    R(ToolContract("get_orders", "List the calling customer's own recent orders, newest first, with status, total and line items.", GetOrdersIn, ("orders:read",), get_orders, ownership=own_customer_arg))
    R(ToolContract("get_order", "Fetch one order by order_id, including its line items.", OrderIdIn, ("orders:read",), get_order, ownership=own_order))
    R(ToolContract("cancel_order", "Cancel an order that has not yet shipped. Fails safely if it has.", CancelOrderIn, ("orders:write",), cancel_order, side_effects=True, ownership=own_order, hitl=hitl_high_value_cancel, cost_class="write_financial"))
    R(ToolContract("create_order", "Place an order for the logged-in customer. Requires customer_id, items [{product_id, qty}], and payment_method: upi, card, or cod. Returns order id, total, payment method and line items.", CreateOrderIn, ("orders:write",), create_order, side_effects=True, ownership=own_customer_arg, cost_class="write_financial"))

    R(ToolContract("get_shipment", "Fetch the shipment for an order: carrier, AWB, status, promised date and lateness.", OrderIdIn, ("shipping:read",), get_shipment, ownership=own_order))
    R(ToolContract("track_shipment", "Fetch the full scan history for a shipment by shipment_id.", ShipmentIdIn, ("shipping:read",), track_shipment, ownership=own_shipment))

    R(ToolContract("get_payment", "Fetch the payment record for one of the calling customer's orders: method, amount and capture status.", OrderIdIn, ("payments:read",), get_payment, ownership=own_order))
    R(ToolContract("get_refund", "List any refunds already raised against a payment, with amount, status and reason. Does not create a refund.", PaymentIdIn, ("payments:read",), get_refund, ownership=own_payment))

    R(ToolContract("check_return_eligibility", "Check whether an order item is within its return window. Does not create anything.", ReturnEligibilityIn, ("returns:read",), check_return_eligibility, ownership=own_order))
    R(ToolContract("create_return", "Create a return request for a delivered, in-window order item.", CreateReturnIn, ("returns:write",), create_return, side_effects=True, ownership=own_order, cost_class="write_financial"))

    R(ToolContract("create_support_ticket", "Raise a support ticket for the calling customer so a human can follow up.", CreateTicketIn, ("support:write",), create_support_ticket, side_effects=True, ownership=own_customer_arg, untrusted_fields=("summary",)))
    R(ToolContract("get_support_ticket", "Fetch one of the calling customer's support tickets by ticket_id, with its status, category and summary.", TicketIdIn, ("support:read",), get_support_ticket, ownership=own_ticket))

    R(ToolContract("get_active_promotions", "List every promotion currently active site-wide, with its code, discount and minimum order value.", EmptyIn, ("promotions:read",), get_active_promotions))
    R(ToolContract("validate_coupon", "Check whether a coupon code is valid for an order total, and what it is worth.", CouponIn, ("promotions:read",), validate_coupon))
    R(ToolContract("list_payment_methods", "List checkout payment options the customer can choose: UPI, credit/debit card, and cash on delivery (COD).", EmptyIn, ("promotions:read",), list_payment_methods))

    R(ToolContract("get_recommendations", "Recommend products for the calling customer, applying their stored preferences.", RecommendationsIn, ("recommendations:read",), get_recommendations, ownership=own_customer_arg, untrusted_fields=("description",)))

    R(ToolContract("search_knowledge_base", "Search all knowledge-base content, including untrusted seller copy. Use for product questions.", KBSearchIn, ("kb:read",), search_knowledge_base, untrusted_fields=("content",)))
    R(ToolContract("retrieve_policy", "Retrieve company policy. Only trusted, signed policy documents are searched.", KBSearchIn, ("kb:read",), retrieve_policy, untrusted_fields=("content",)))
    
    R(ToolContract("get_pending_orders", "List orders pending review (admin only). Returns orders with specified status, defaults to 'placed'.", GetPendingOrdersIn, ("orders:read",), get_pending_orders))
    R(ToolContract("approve_order", "Approve an order and move it to packed status (admin only).", ApproveOrderIn, ("orders:write",), approve_order, side_effects=True, cost_class="write_financial", ownership=own_order))
    R(ToolContract("reject_order", "Reject/cancel an order with a reason (admin only).", RejectOrderIn, ("orders:write",), reject_order, side_effects=True, cost_class="write_financial", ownership=own_order))
    R(ToolContract("list_low_stock", "List catalogue products whose available warehouse stock is at or below a threshold. Seller inventory specialist.", LowStockIn, ("products:read",), list_low_stock))
    R(ToolContract("add_product", "Add a new product to the catalogue (shop owner only). Requires title and price_inr; optional category (laptops/phones/audio/monitors/accessories), brand, sku, description, initial_stock.", AddProductIn, ("products:write",), add_product, side_effects=True, cost_class="write_financial", ownership=own_shop_catalogue))


_register_all()
