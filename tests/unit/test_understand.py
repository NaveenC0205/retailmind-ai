import pytest

from app.llm.mock import detect_intent, extract_category
from app.nlp.understand import rewrite_query, understand

pytestmark = pytest.mark.unit


def test_rewrites_iphone_typos_and_missing_letters():
    for raw in ("oiphone", "iphne", "ifone", "i phone", "iphon prce"):
        out = rewrite_query(raw)
        assert "iphone" in out
    assert "price" in rewrite_query("iphon prce")
    assert "search" in rewrite_query("serch for oiphone")


def test_rewrites_hinglish_price_questions():
    u = understand("iphone ki keemat kya hai")
    assert "iphone" in u.rewritten
    assert "price" in u.rewritten
    assert detect_intent("iphone ki keemat kya hai") == "shopping"
    assert extract_category("oiphone dikhao") == "phones"


def test_devanagari_product_words():
    out = rewrite_query("आईफोन की कीमत")
    assert "iphone" in out
    assert "price" in out


def test_rewrite_is_idempotent():
    once = rewrite_query("serch oiphone prces")
    assert rewrite_query(once) == once


def test_preserves_order_ids():
    out = rewrite_query("Where is my order OR-20001?")
    assert "OR-20001" in out


def test_place_order_typos_route_to_checkout():
    assert detect_intent("i tot place order") == "checkout_help"
    assert detect_intent("place oder") == "checkout_help"
