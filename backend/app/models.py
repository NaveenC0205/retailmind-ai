"""ORM models for the three logical schemas: retail / ai / eval.

On SQLite they live in one file with a table-name prefix. On Postgres the
prefix becomes a real schema. The separation matters because the ai_ and
eval_ planes get dropped and rebuilt constantly while retail_ data is seeded
once and treated as the system of record.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    # Naive UTC: SQLite and Postgres TIMESTAMP WITHOUT TIME ZONE both accept it.
    # asyncpg rejects tz-aware values against TIMESTAMP WITHOUT TIME ZONE.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list: JSON}


# ======================================================================
# retail — business systems (the things the tools actually talk to)
# ======================================================================

class Customer(Base):
    __tablename__ = "retail_customers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    tier: Mapped[str] = mapped_column(String, default="standard")
    region: Mapped[str] = mapped_column(String, default="IN-KA")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Product(Base):
    __tablename__ = "retail_products"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    sku: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    brand: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    price_inr: Mapped[int] = mapped_column(Integer)
    rating: Mapped[float] = mapped_column(Float, default=4.0)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # Seller-authored free text. NEVER trusted. Tagged RETRIEVED/untrusted
    # whenever it enters a prompt. This is the indirect-injection surface.
    description_raw: Mapped[str] = mapped_column(Text, default="")


class Inventory(Base):
    __tablename__ = "retail_inventory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("retail_products.id"))
    warehouse: Mapped[str] = mapped_column(String, default="BLR-1")
    qty_available: Mapped[int] = mapped_column(Integer, default=0)
    qty_reserved: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = "retail_orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("retail_customers.id"), index=True)
    status: Mapped[str] = mapped_column(String)  # placed|packed|shipped|delivered|cancelled
    total_inr: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String, default="web")
    placed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    items: Mapped[list["OrderItem"]] = relationship(lazy="selectin")


class OrderCheckout(Base):
    """Checkout details and retry identity; separate table keeps existing orders compatible."""
    __tablename__ = "retail_order_checkouts"
    order_id: Mapped[str] = mapped_column(ForeignKey("retail_orders.id"), primary_key=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class OrderItem(Base):
    __tablename__ = "retail_order_items"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("retail_orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("retail_products.id"))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_inr: Mapped[int] = mapped_column(Integer)


class Shipment(Base):
    __tablename__ = "retail_shipments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("retail_orders.id"), index=True)
    carrier: Mapped[str] = mapped_column(String)
    awb: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    exception_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    promised_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ShipmentEvent(Base):
    __tablename__ = "retail_shipment_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("retail_shipments.id"), index=True)
    code: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Payment(Base):
    __tablename__ = "retail_payments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("retail_orders.id"), index=True)
    method: Mapped[str] = mapped_column(String)
    amount_inr: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="captured")
    gateway_ref: Mapped[str] = mapped_column(String, default="")


class Refund(Base):
    __tablename__ = "retail_refunds"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("retail_payments.id"), index=True)
    amount_inr: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="initiated")
    reason: Mapped[str] = mapped_column(String, default="")
    initiated_by: Mapped[str] = mapped_column(String, default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Return(Base):
    __tablename__ = "retail_returns"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("retail_orders.id"), index=True)
    order_item_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="requested")
    reason: Mapped[str] = mapped_column(String, default="")
    window_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SupportTicket(Base):
    __tablename__ = "retail_support_tickets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("retail_customers.id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String)
    priority: Mapped[str] = mapped_column(String, default="normal")
    status: Mapped[str] = mapped_column(String, default="open")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String, default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Promotion(Base):
    __tablename__ = "retail_promotions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)  # percent|flat
    value: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)


# ======================================================================
# ai — runtime plane
# ======================================================================

class Conversation(Base):
    __tablename__ = "ai_conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    channel: Mapped[str] = mapped_column(String, default="web")
    mode: Mapped[str] = mapped_column(String, default="auto")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Message(Base):
    __tablename__ = "ai_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("ai_conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String)  # user|assistant|system
    content: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String, default="USER")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class KBDocument(Base):
    __tablename__ = "ai_kb_documents"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    family: Mapped[str] = mapped_column(String, index=True)
    filename: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer, default=1)
    effective_from: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_trust: Mapped[str] = mapped_column(String, default="trusted")
    checksum: Mapped[str] = mapped_column(String, default="")


class KBChunk(Base):
    __tablename__ = "ai_kb_chunks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("ai_kb_documents.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    page: Mapped[int] = mapped_column(Integer, default=1)
    heading: Mapped[str] = mapped_column(String, default="")
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentRun(Base):
    __tablename__ = "ai_agent_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    message_id: Mapped[str] = mapped_column(String)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    mode: Mapped[str] = mapped_column(String)
    entry_agent: Mapped[str] = mapped_column(String)
    terminal_state: Mapped[str] = mapped_column(String, default="running")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str] = mapped_column(String, index=True)
    prompt_version: Mapped[str] = mapped_column(String, default="v1")
    model_config_name: Mapped[str] = mapped_column(String, default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentStep(Base):
    __tablename__ = "ai_agent_steps"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ai_agent_runs.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    agent_name: Mapped[str] = mapped_column(String)
    action_type: Mapped[str] = mapped_column(String)  # tool|delegate|retrieve|answer
    reasoning_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolCall(Base):
    __tablename__ = "ai_tool_calls"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    step_id: Mapped[str] = mapped_column(String, index=True)
    tool_name: Mapped[str] = mapped_column(String, index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String)  # ok|denied|error|suspended
    gateway_verdict: Mapped[str] = mapped_column(String, default="allow")
    denial_reason: Mapped[str] = mapped_column(String, default="")
    retries: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Retrieval(Base):
    __tablename__ = "ai_retrievals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    query: Mapped[str] = mapped_column(Text)
    strategy: Mapped[str] = mapped_column(String, default="hybrid")
    chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    scores: Mapped[list] = mapped_column(JSON, default=list)
    reranked_ids: Mapped[list] = mapped_column(JSON, default=list)
    dropped_untrusted: Mapped[list] = mapped_column(JSON, default=list)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class Citation(Base):
    __tablename__ = "ai_citations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    chunk_id: Mapped[str] = mapped_column(String)
    document_id: Mapped[str] = mapped_column(String)
    claim_span: Mapped[str] = mapped_column(Text, default="")
    validated: Mapped[bool] = mapped_column(Boolean, default=False)


class MemoryRecord(Base):
    __tablename__ = "ai_memory_records"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    source_conversation_id: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decay_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class GuardrailEvent(Base):
    __tablename__ = "ai_guardrail_events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    chain: Mapped[str] = mapped_column(String)  # input|output|tool
    check_name: Mapped[str] = mapped_column(String)
    verdict: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String, default="")
    matched_span: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class Approval(Base):
    __tablename__ = "ai_approvals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    tool_name: Mapped[str] = mapped_column(String)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    threshold_reason: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String, default="")


class Span(Base):
    """Flat span table. Stands in for the OTel collector in the local profile;
    in the docker profile the same spans are exported over OTLP as well."""
    __tablename__ = "ai_spans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String, index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String, default="internal")
    status: Mapped[str] = mapped_column(String, default="ok")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


# ======================================================================
# eval — quality plane
# ======================================================================

class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String, index=True)
    dataset_version: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    model_config_name: Mapped[str] = mapped_column(String)
    execution_tier: Mapped[str] = mapped_column(String, default="mocked")
    git_sha: Mapped[str] = mapped_column(String, default="")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class EvalResult(Base):
    __tablename__ = "eval_results"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    eval_run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    item_id: Mapped[str] = mapped_column(String, index=True)
    evaluator_name: Mapped[str] = mapped_column(String, index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str] = mapped_column(String, default="")


class GateResult(Base):
    __tablename__ = "eval_gate_results"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    eval_run_id: Mapped[str] = mapped_column(String, index=True)
    gate_name: Mapped[str] = mapped_column(String)
    metric: Mapped[str] = mapped_column(String)
    comparator: Mapped[str] = mapped_column(String)
    threshold: Mapped[float] = mapped_column(Float)
    observed: Mapped[float] = mapped_column(Float)
    ci_low: Mapped[float] = mapped_column(Float, default=0.0)
    ci_high: Mapped[float] = mapped_column(Float, default=0.0)
    blocks: Mapped[str] = mapped_column(String, default="merge")
    passed: Mapped[bool] = mapped_column(Boolean, default=False)


class RedTeamFinding(Base):
    __tablename__ = "eval_redteam_findings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    eval_run_id: Mapped[str] = mapped_column(String, index=True)
    attack_id: Mapped[str] = mapped_column(String)
    family: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(String)  # blocked|leaked|executed
    severity: Mapped[str] = mapped_column(String)
    trace_id: Mapped[str] = mapped_column(String, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Feedback(Base):
    """User feedback on AI responses (thumbs up/down)."""
    __tablename__ = "eval_feedback"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1=thumbs down, 5=thumbs up
    category: Mapped[str] = mapped_column(String, default="")  # wrong_answer, slow, helpful, etc.
    comment: Mapped[str] = mapped_column(Text, default="")
    message_text: Mapped[str] = mapped_column(Text, default="")
    answer_text: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BenchmarkRun(Base):
    """Model benchmark comparison run."""
    __tablename__ = "eval_benchmark_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String)
    models_compared: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    winner_accuracy: Mapped[str] = mapped_column(String, default="")
    winner_latency: Mapped[str] = mapped_column(String, default="")
    winner_cost: Mapped[str] = mapped_column(String, default="")


ALL_TABLES = [m.__tablename__ for m in Base.__subclasses__()]
