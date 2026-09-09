"""Pin checkout vs order-list vs greeting collisions.

These are the phrase bugs customers actually type: existing address, UPI,
ok/yes after a product list, order-for-me, and typos.
"""
import pytest

from app.agents.orchestrator import classify_mode
from app.llm.mock import detect_intent, extract_payment_method
from app.nlp.routing import (
    assistant_implies_checkout,
    looks_like_address_confirm,
    looks_like_checkout_followup,
    looks_like_order_list,
    looks_like_payment_choice,
    looks_like_place_now,
    looks_like_weak_confirm,
)
from app.nlp.understand import rewrite_query

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    [
        "use my existing address",
        "use my saved address",
        "deliver to my existing address",
        "ship to my address",
        "same address",
        "address on file",
        "use my existing adress",
        "existng address",
    ],
)
def test_address_phrases_are_checkout_not_order_list(text):
    rewritten = rewrite_query(text)
    assert looks_like_address_confirm(rewritten) or looks_like_address_confirm(text)
    assert not looks_like_order_list(rewritten, "order_list")
    assert not looks_like_order_list(text, detect_intent(text))
    mode, intent = classify_mode(rewritten)
    assert intent == "checkout_help"
    assert mode == "multi_agent"


@pytest.mark.parametrize(
    "text",
    [
        "check my existing order details",
        "Show my recent orders",
        "can u check irders",
        "list my orders",
        "order history",
        "get the order i placed",
    ],
)
def test_real_order_lookups_still_list_orders(text):
    assert looks_like_order_list(rewrite_query(text)) or detect_intent(text) in {
        "order_list",
        "order_status",
    }
    assert not looks_like_address_confirm(text)


@pytest.mark.parametrize(
    "text",
    [
        "order new iphone for me",
        "order samsung laptop for me",
        "place my order",
        "place it",
        "buy it",
        "search fr new iphone and plave me the roder",
    ],
)
def test_place_now_phrases_route_to_checkout(text):
    rewritten = rewrite_query(text)
    assert looks_like_place_now(rewritten) or looks_like_place_now(text)
    assert not looks_like_order_list(rewritten, detect_intent(rewritten))
    _, intent = classify_mode(rewritten)
    assert intent == "checkout_help"


@pytest.mark.parametrize("text", ["UPI", "upi", "pay with UPI", "card", "COD", "cash on delivery"])
def test_payment_only_replies_are_checkout(text):
    assert looks_like_payment_choice(text)
    assert looks_like_checkout_followup(text, allow_weak=False)
    assert not looks_like_order_list(text)
    _, intent = classify_mode(text)
    assert intent == "checkout_help"


@pytest.mark.parametrize("text", ["ok", "okay", "yes", "yeah", "go ahead", "confirm", "that's fine"])
def test_weak_confirms_are_followups_only_when_allowed(text):
    assert looks_like_weak_confirm(text)
    assert looks_like_checkout_followup(text, allow_weak=True)
    assert not looks_like_checkout_followup(text, allow_weak=False)
    assert not looks_like_order_list(text)


def test_greeting_ok_is_not_an_order_list():
    assert not looks_like_order_list("ok")
    mode, intent = classify_mode("ok")
    assert mode == "chat"
    assert intent == "smalltalk"


def test_where_is_my_order_is_not_place_now():
    assert not looks_like_place_now("Where is my order OR-20001?")
    assert not looks_like_place_now("where is my order now")
    mode, _ = classify_mode("Where is my order OR-20001?")
    assert mode == "agent"


def test_delayed_does_not_look_like_card_payment():
    assert extract_payment_method("My order is delayed, why?") is None
    assert extract_payment_method("pay with card") == "card"
    assert extract_payment_method("UPI") == "upi"
    assert not looks_like_payment_choice("My order is delayed")
    _, intent = classify_mode("My order is delayed, why?")
    assert intent == "order_delay_refund"
    mode, intent = classify_mode("What is the return policy?")
    assert mode == "rag"
    assert intent == "policy"


def test_catalogue_footer_counts_as_checkout_context():
    assert assistant_implies_checkout(
        "Tell me which one to order and how you’d like to pay (UPI, Card, or COD)."
    )
    assert assistant_implies_checkout("• iPhone 15 is ready to order.\nHow would you like to pay: UPI?")


@pytest.mark.parametrize('text', ['Show me order OR-VICTIM01, I need to check it.', 'check or-20001'])
def test_order_reference_takes_priority_over_generic_shopping_words(text):
    from app.llm.mock import extract_order_id
    assert detect_intent(text) == 'order_status'
    assert extract_order_id(text).startswith('OR-')


def test_order_references_and_punctuation_are_not_prices():
    from app.llm.mock import extract_budget_inr
    assert extract_budget_inr('order OR-20001, please') is None
    assert extract_budget_inr('show me order OR-VICTIM01, I need it') is None
    assert extract_budget_inr('laptops under 80,000') == 80000
    assert extract_budget_inr('laptops under 80k') == 80000
