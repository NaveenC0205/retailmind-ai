"""Architecture conformance.

These tests prevent a class of security bug that no amount of prompt testing
will catch. Layers leak: the most common leak is an agent calling a business
API directly and quietly removing authorization from the whole system. A
static rule in CI is cheaper than finding it in an incident review.
"""
import ast
import pathlib

import pytest

from app.agents.base import AGENTS
from app.tools.contracts import registry

pytestmark = pytest.mark.conformance

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend" / "app"


def imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def module_files(package: str) -> list[pathlib.Path]:
    root = BACKEND / package
    if root.is_file():
        return [root]
    return [p for p in root.rglob("*.py") if p.name != "__init__.py"]


def test_agents_never_import_tool_implementations_directly():
    """Agents may only reach business systems through the Tool Gateway.
    Importing app.tools.impl bypasses schema validation, scope checks,
    ownership checks, HITL and the circuit breaker in one line."""
    offenders = []
    for path in module_files("agents"):
        for mod in imports_of(path):
            if mod.startswith("app.tools.impl"):
                offenders.append(f"{path.name} imports {mod}")
    assert not offenders, offenders


def test_agents_never_import_the_orm_models_of_business_tables():
    """An agent that can query Order directly can read any customer's order."""
    forbidden = {"Order", "Payment", "Shipment", "Customer", "Refund", "Return"}
    offenders = []
    for path in module_files("agents"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.models":
                bad = {a.name for a in node.names} & forbidden
                if bad:
                    offenders.append(f"{path.name}: {sorted(bad)}")
    assert not offenders, offenders


def test_the_guardrail_module_does_not_depend_on_an_llm():
    """Guardrails must be code, not a paragraph in a prompt. A guardrail that
    needs a model call has the model's availability and latency as a
    dependency, and cannot be unit tested with a fixed corpus."""
    mods = imports_of(BACKEND / "guardrails.py")
    assert not any(m.startswith("app.llm") for m in mods), mods


def test_the_tool_gateway_does_not_depend_on_an_llm():
    """Authorization must never be a model decision."""
    mods = imports_of(BACKEND / "tools" / "gateway.py")
    assert not any(m.startswith("app.llm") for m in mods), mods


def test_deterministic_evaluators_do_not_depend_on_an_llm():
    """Tier-1 evaluators must run with no network and no model, or they cannot
    sit on every commit."""
    mods = imports_of(BACKEND / "evaluation" / "evaluators.py")
    assert not any(m.startswith("app.llm") for m in mods), mods


def test_the_supervisor_holds_no_business_tools():
    supervisor = AGENTS["supervisor"]
    assert supervisor.tools == ()
    assert supervisor.delegates_to


def test_read_only_agents_hold_no_write_tools():
    for name in ("policy", "product", "recommendation"):
        assert not AGENTS[name].can_write, f"{name} gained a write tool"


def test_every_agent_tool_exists_in_the_registry():
    for name, spec in AGENTS.items():
        for tool in spec.tools:
            assert registry.get(tool) is not None, f"{name} references unknown tool {tool}"


def test_every_delegation_target_exists():
    for name, spec in AGENTS.items():
        for child in spec.delegates_to:
            assert child in AGENTS, f"{name} delegates to unknown agent {child}"


def test_prompts_are_files_not_string_literals_in_code():
    """Prompts live in prompts/<version>/ so eval_runs.prompt_version means
    something and a regression is attributable."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if path.name in ("mock.py",):
            continue
        text = path.read_text(encoding="utf-8")
        if "You are a helpful assistant" in text or "You are RetailMind" in text:
            offenders.append(path.name)
    assert not offenders, offenders
