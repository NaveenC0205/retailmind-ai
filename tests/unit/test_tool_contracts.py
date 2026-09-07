"""Contract-level invariants, asserted by walking the registry.

These are the tests that stop the architecture eroding. Adding a write tool
without an ownership rule is a test failure here, not a code-review comment
someone might miss.
"""
import pytest

from app.tools.contracts import registry
from app.tools.impl import _register_all

pytestmark = pytest.mark.unit

_register_all()


def test_registry_is_populated():
    assert len(registry.names()) >= 20


@pytest.mark.parametrize("tool", registry.writes(), ids=lambda t: t.name)
def test_every_write_tool_enforces_ownership(tool):
    assert tool.ownership is not None, (
        f"{tool.name} mutates state but declares no ownership rule -- "
        "the gateway would let any principal call it for any record"
    )


@pytest.mark.parametrize("tool", registry.all(), ids=lambda t: t.name)
def test_every_tool_declares_scopes_timeout_and_retries(tool):
    assert tool.required_scopes, f"{tool.name} declares no scopes"
    assert tool.timeout_ms > 0
    assert tool.max_retries >= 0


@pytest.mark.parametrize("tool", registry.all(), ids=lambda t: t.name)
def test_every_tool_description_is_useful_to_a_model(tool):
    """An ambiguous tool description is a documentation bug that presents as a
    model bug. Fifty characters is a low bar and it still catches stubs."""
    assert len(tool.description) >= 50, f"{tool.name}: description too thin"
    assert tool.description.strip().endswith("."), f"{tool.name}: description is not a sentence"


@pytest.mark.parametrize("tool", registry.all(), ids=lambda t: t.name)
def test_tool_spec_is_serialisable_for_a_prompt(tool):
    spec = tool.spec()
    assert spec["name"] == tool.name
    assert "properties" in spec["parameters"]


def test_free_text_returned_by_third_parties_is_declared_untrusted():
    """Seller-authored fields must be declared so the context assembler wraps
    them as untrusted without anyone having to remember to."""
    for name in ("search_products", "get_product", "compare_products"):
        assert "description" in registry.get(name).untrusted_fields


def test_high_value_cancellation_requires_a_human():
    assert registry.get("cancel_order").hitl is not None


def test_policy_retrieval_is_read_only():
    assert not registry.get("retrieve_policy").side_effects
