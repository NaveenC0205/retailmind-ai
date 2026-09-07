"""Tool contracts.

Every tool declares the full contract the doc calls for: schema, scopes,
ownership rule, side effects, HITL threshold, timeout, retry policy, failure
behaviour, and which of its output fields are third-party-writable and must be
tagged untrusted when they enter a prompt.

The contract is data, so the gateway can enforce it and the test suite can
iterate over it. `tests/unit/test_tool_contracts.py` walks this registry and
asserts every write tool has an ownership rule -- adding a tool without one is
a test failure, not a code review comment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# input schemas
# ----------------------------------------------------------------------

class SearchProductsIn(BaseModel):
    query: str = ""
    category: Optional[str] = None
    brand: Optional[str] = None
    min_price_inr: Optional[int] = Field(default=None, ge=0)
    max_price_inr: Optional[int] = Field(default=None, ge=0)
    min_rating: Optional[float] = Field(default=None, ge=0, le=5)
    # Feature filters for electronics (RAM, storage, ANC, etc.)
    min_ram_gb: Optional[int] = Field(default=None, ge=0)
    min_storage_gb: Optional[int] = Field(default=None, ge=0)
    requires_anc: Optional[bool] = None
    attribute_contains: Optional[str] = Field(
        default=None,
        description="Match a keyword inside product attributes/features JSON (e.g. 'M3', 'IPS', 'wireless')",
    )
    limit: int = Field(default=5, ge=1, le=25)


class ProductIdIn(BaseModel):
    product_id: str = Field(min_length=1)


class CompareProductsIn(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=5)


class CustomerIdIn(BaseModel):
    customer_id: str = Field(min_length=1)


class GetOrdersIn(BaseModel):
    customer_id: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class OrderIdIn(BaseModel):
    order_id: str = Field(min_length=1)


class CancelOrderIn(BaseModel):
    order_id: str = Field(min_length=1)
    reason: str = "customer_request"


class CreateOrderItemIn(BaseModel):
    product_id: str = Field(min_length=1)
    qty: int = Field(default=1, ge=1, le=10)


class CreateOrderIn(BaseModel):
    customer_id: str = Field(min_length=1)
    items: list[CreateOrderItemIn] = Field(min_length=1, max_length=20)
    payment_method: str = Field(default="upi", description="upi, card, or cod")
    address: str = Field(default="")


class ShipmentIdIn(BaseModel):
    shipment_id: str = Field(min_length=1)


class PaymentIdIn(BaseModel):
    payment_id: str = Field(min_length=1)


class ReturnEligibilityIn(BaseModel):
    order_id: str = Field(min_length=1)
    order_item_id: str = ""


class CreateReturnIn(BaseModel):
    order_id: str = Field(min_length=1)
    order_item_id: str = ""
    reason: str = "not_as_described"


class CreateTicketIn(BaseModel):
    customer_id: str = Field(min_length=1)
    order_id: Optional[str] = None
    category: str = "general"
    summary: str = ""
    priority: str = "normal"


class TicketIdIn(BaseModel):
    ticket_id: str = Field(min_length=1)


class EmptyIn(BaseModel):
    pass


class CouponIn(BaseModel):
    code: str = Field(min_length=1)
    order_total_inr: int = Field(default=0, ge=0)


class RecommendationsIn(BaseModel):
    customer_id: str = Field(min_length=1)
    category: Optional[str] = None
    limit: int = Field(default=3, ge=1, le=10)


class KBSearchIn(BaseModel):
    query: str = Field(min_length=1)
    family: Optional[str] = None
    top_k: int = Field(default=6, ge=1, le=20)


class GetPendingOrdersIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    status: str = Field(default="placed")


class ApproveOrderIn(BaseModel):
    order_id: str = Field(min_length=1)


class RejectOrderIn(BaseModel):
    order_id: str = Field(min_length=1)
    reason: str = Field(default="admin_rejected")


class LowStockIn(BaseModel):
    threshold: int = Field(default=20, ge=0, le=10000)
    limit: int = Field(default=10, ge=1, le=50)


# ----------------------------------------------------------------------
# contract
# ----------------------------------------------------------------------

OwnershipResolver = Callable[[Any, Any, dict], Awaitable[Optional[str]]]
"""(session, principal, args) -> owning customer_id, or None if not found."""

HitlRule = Callable[[dict, dict], bool]
"""(args, context) -> whether this call needs human approval."""


@dataclass
class ToolContract:
    name: str
    description: str
    input_model: type[BaseModel]
    required_scopes: tuple[str, ...]
    handler: Callable[..., Awaitable[dict]]
    side_effects: bool = False
    ownership: Optional[OwnershipResolver] = None
    hitl: Optional[HitlRule] = None
    timeout_ms: int = 3000
    max_retries: int = 2
    retry_on: tuple[str, ...] = ("timeout", "upstream_error")
    circuit_fail_threshold: int = 5
    untrusted_fields: tuple[str, ...] = ()
    cost_class: str = "read"

    def spec(self) -> dict:
        """What the agent is told about this tool. Descriptions are part of the
        product: an ambiguous description is a *documentation* bug that
        presents as a model bug."""
        schema = self.input_model.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
            "writes": self.side_effects,
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract) -> ToolContract:
        if contract.name in self._tools:
            raise ValueError(f"duplicate tool: {contract.name}")
        self._tools[contract.name] = contract
        return contract

    def get(self, name: str) -> Optional[ToolContract]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[ToolContract]:
        return [self._tools[n] for n in self.names()]

    def specs(self, names: Optional[list[str]] = None) -> list[dict]:
        chosen = names or self.names()
        return [self._tools[n].spec() for n in chosen if n in self._tools]

    def writes(self) -> list[ToolContract]:
        return [t for t in self.all() if t.side_effects]


registry = ToolRegistry()
