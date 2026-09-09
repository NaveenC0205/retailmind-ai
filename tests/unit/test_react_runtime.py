from app.agents.react_runtime import is_stalling_reply, live_react_enabled
from app.config import normalize_database_url
from app.nlp.routing import looks_like_address_confirm, looks_like_order_list
from app.nlp.understand import rewrite_query
from app.tools.contracts import CreateOrderIn


def test_mock_provider_does_not_start_live_react():
    assert live_react_enabled() is False


def test_hold_on_and_generic_hello_are_stalling():
    assert is_stalling_reply("Please hold on for a moment while I retrieve the details.")
    assert is_stalling_reply("Hello! How can I assist you today? If you're looking for products, feel free to ask!")
    assert not is_stalling_reply("Here are your recent ShopZone orders\n• OR-20001 — shipped — ₹18,400")
    assert not is_stalling_reply("iPhone 15 — ₹69,900")


def test_supabase_uri_becomes_asyncpg_with_ssl():
    raw = "postgresql://postgres:secret@db.abcd.supabase.co:5432/postgres"
    out = normalize_database_url(raw)
    assert out.startswith("postgresql+asyncpg://")
    assert "ssl=require" in out
    assert "secret" in out


def test_postgres_scheme_alias_is_accepted():
    out = normalize_database_url("postgres://u:p@localhost:5432/shop")
    assert out.startswith("postgresql+asyncpg://")


def test_supabase_project_url_builds_async_postgres_uri():
    from app.config import supabase_postgres_url

    out = supabase_postgres_url("https://kkqzxnnfetediyuwqfxj.supabase.co", "s3cret!")
    assert "postgres.kkqzxnnfetediyuwqfxj" in out
    assert "aws-0-ap-northeast-1.pooler.supabase.com:6543" in out
    assert out.startswith("postgresql+asyncpg://")
    assert "ssl=require" in out
    assert "s3cret" in out


def test_use_existing_address_is_not_an_order_list():
    assert looks_like_address_confirm("use my existing address")
    assert not looks_like_order_list("use my existing address", "order_list")
    assert looks_like_order_list("check my existing order details")
    assert looks_like_order_list("Show my recent orders")


def test_create_order_schema_accepts_missing_address():
    parsed = CreateOrderIn.model_validate(
        {"customer_id": "CU-1001", "items": [{"product_id": "PR-P001", "qty": 1}], "address": None}
    )
    assert parsed.address == ""
    assert parsed.payment_method == "upi"


def test_rewrites_place_order_typos():
    out = rewrite_query("search fr new iphone and plave me the roder")
    assert "iphone" in out
    assert "place" in out
    assert "order" in out
    assert "search for" in out
