"""Deterministic shop-chat routing.

Substring shortcuts are how "use my existing address" became an order list.
Keep the matchers here so tests can pin every collision without the full app.
"""
from __future__ import annotations

from app.llm.mock import extract_order_id, extract_payment_method

_ADDRESS_HINTS = (
    "existing address",
    "saved address",
    "same address",
    "use my address",
    "my address",
    "delivery address",
    "shipping address",
    "deliver to",
    "ship to",
    "this address",
    "that address",
    "home address",
    "usual address",
    "address on file",
    "address on my",
)

_PLACE_NOW_HINTS = (
    "place order",
    "place an order",
    "place the order",
    "place my order",
    "place me",
    "place it",
    "place this",
    "order now",
    "order for me",
    "order this",
    "order it",
    "buy it",
    "buy this",
    "buy now",
    "get me one",
    "checkout",
    "ship it",
    "send it",
)

_WEAK_CONFIRMS = {
    "ok",
    "okay",
    "yes",
    "yeah",
    "yep",
    "yup",
    "sure",
    "go ahead",
    "proceed",
    "confirm",
    "please",
    "do it",
    "that's fine",
    "thats fine",
    "that one",
    "this one",
    "the first one",
    "sounds good",
}

_CHECKOUT_ASSISTANT_HINTS = (
    "ready to order",
    "like to pay",
    "how would you like to pay",
    "upi, card",
    "upi, card, or",
    "cash on delivery",
    "i can place",
    "sign in first",
    "requires an address",
    "saved address",
    "delivering to",
    "which one to order",
    "tell me which one",
    "item:",
    "is ready to order",
)

_ORDER_LIST_HINTS = (
    "check order",
    "check orders",
    "show order",
    "show orders",
    "see order",
    "see orders",
    "list order",
    "my orders",
    "order history",
    "all my order",
    "irders",
    "track my",
    "latest order",
    "existing order",
    "order details",
    "order detail",
    "order detils",
    "my existing order",
    "check my order",
    "recent order",
    "show my recent",
)

_NOT_ORDER_LIST = (
    "cancel",
    "refund",
    "promo",
    "discount",
    "coupon",
    "policy",
    "place order",
    "place me",
    "place it",
    "place my",
    "buy ",
    "shipment",
    "awb",
    "iphone",
    "laptop",
    "address",
    "for me",
)


def looks_like_address_confirm(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in _ADDRESS_HINTS)


def looks_like_payment_choice(text: str) -> bool:
    raw = (text or "").strip().lower().strip("!?. ")
    if raw in {"upi", "card", "cod", "gpay", "phonepe", "paytm", "visa", "debit", "credit"}:
        return True
    if looks_like_address_confirm(text):
        return False
    return extract_payment_method(text) is not None


def looks_like_place_now(text: str) -> bool:
    low = (text or "").lower()
    if looks_like_address_confirm(low):
        return True
    if any(k in low for k in ("where is", "track my", "order status", "delayed")) and "place" not in low and "buy" not in low:
        return False
    if " for me" in f" {low}" and any(k in low for k in ("order", "buy", "place", "get me")):
        return True
    return any(k in low for k in _PLACE_NOW_HINTS)


def looks_like_weak_confirm(text: str) -> bool:
    low = (text or "").strip().lower().strip("!?. ")
    return low in _WEAK_CONFIRMS


def looks_like_checkout_followup(text: str, *, allow_weak: bool = True) -> bool:
    if looks_like_address_confirm(text) or looks_like_payment_choice(text) or looks_like_place_now(text):
        return True
    return allow_weak and looks_like_weak_confirm(text)


def assistant_implies_checkout(content: str) -> bool:
    low = (content or "").lower()
    if any(h in low for h in _CHECKOUT_ASSISTANT_HINTS):
        return True
    return "₹" in (content or "") and any(k in low for k in ("pay", "upi", "order", "iphone", "place"))


def looks_like_order_list(text: str, intent: str = "") -> bool:
    """True for 'check/show my orders' — never for address/checkout follow-ups."""
    low = (text or "").lower()
    raw = (text or "").strip().lower().strip("!?. ")
    if raw in {"upi", "card", "cod", "gpay", "phonepe", "paytm"}:
        return False
    if looks_like_address_confirm(low) or looks_like_place_now(low):
        return False
    if any(k in low for k in _NOT_ORDER_LIST):
        return False
    if "return" in low and "policy" not in low and intent == "return_item":
        return False
    oid = extract_order_id(text)
    if oid and ("where is" in low or "track" in low):
        return False
    if intent in {"order_list", "order_status"} and not oid:
        if looks_like_weak_confirm(text):
            return False
        return True
    if looks_like_weak_confirm(text):
        return False
    if "my latest" in low and "order" not in low:
        return False
    return any(k in low for k in _ORDER_LIST_HINTS)