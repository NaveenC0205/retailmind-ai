from app.agents.react_runtime import is_stalling_reply, live_react_enabled
from app.config import normalize_database_url


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
