"""Principal resolution.

DESIGN RULE (see docs/adr/0002-authorization-in-the-gateway.md):

The principal is derived from the authenticated request, never from anything
the model produced or the customer typed. There is no code path by which a
conversation can change who the caller is. Every ownership check in the Tool
Gateway compares a resource against *this* object.

Auth here is a demo bearer scheme (`Bearer customer:<id>`), deliberately thin
so tests can construct principals directly. Swap in real JWT verification and
nothing downstream changes -- that is the point of the seam.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import Header, HTTPException, status

CUSTOMER_SCOPES = frozenset(
    {
        "products:read",
        "orders:read",
        "orders:write",
        "shipping:read",
        "payments:read",
        "returns:read",
        "returns:write",
        "support:read",
        "support:write",
        "promotions:read",
        "kb:read",
        "memory:read",
        "recommendations:read",
    }
)

# Read-only scopes. Used by the "browsing" persona in tests to prove that a
# missing scope is refused by the gateway rather than argued about by the model.
GUEST_SCOPES = frozenset({"products:read", "kb:read", "promotions:read"})

OPERATOR_SCOPES = CUSTOMER_SCOPES | {"approvals:write", "eval:write", "eval:read", "products:write"}


@dataclass(frozen=True)
class Principal:
    subject_id: str
    kind: str = "customer"  # customer | operator | guest
    customer_id: Optional[str] = None
    scopes: frozenset = field(default_factory=lambda: CUSTOMER_SCOPES)
    budget_inr: float = 5.0

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    def owns_customer(self, customer_id: str) -> bool:
        if self.kind == "operator":
            return True
        return self.customer_id is not None and self.customer_id == customer_id


def customer(customer_id: str, scopes: frozenset | None = None) -> Principal:
    return Principal(
        subject_id=customer_id,
        kind="customer",
        customer_id=customer_id,
        scopes=scopes if scopes is not None else CUSTOMER_SCOPES,
    )


def guest() -> Principal:
    return Principal(subject_id="guest", kind="guest", customer_id=None, scopes=GUEST_SCOPES)


def operator(op_id: str = "op-1") -> Principal:
    return Principal(subject_id=op_id, kind="operator", customer_id=None, scopes=OPERATOR_SCOPES)


def parse_bearer(token: str) -> Principal:
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token in ("", "guest"):
        return guest()
    
    # Try JWT token first (does not contain ":" in valid format)
    if ":" not in token and len(token) > 20:
        try:
            from app.auth import decode_token
            token_data = decode_token(token)
            if token_data:
                scopes = OPERATOR_SCOPES if token_data.is_admin else CUSTOMER_SCOPES
                return Principal(
                    subject_id=token_data.customer_id,
                    kind="operator" if token_data.is_admin else "customer",
                    customer_id=token_data.customer_id,
                    scopes=scopes,
                )
        except Exception:
            pass
    
    # Fall back to demo bearer format (customer:CU-1001)
    if ":" not in token:
        raise ValueError("malformed token")
    kind, _, ident = token.partition(":")
    kind = kind.lower()
    if kind == "customer":
        return customer(ident)
    if kind == "operator":
        return operator(ident)
    if kind == "guest":
        return guest()
    raise ValueError(f"unknown principal kind: {kind}")


async def require_principal(authorization: str = Header(default="")) -> Principal:
    """FastAPI dependency. Absent credentials => guest, not an error: the
    catalogue and policy KB are public. Malformed credentials => 401."""
    if not authorization:
        return guest()
    try:
        return parse_bearer(authorization)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")


async def require_operator(authorization: str = Header(default="")) -> Principal:
    p = await require_principal(authorization)
    if p.kind != "operator":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operator scope required")
    return p
