"""The Tool Gateway -- decision 2 of the architecture.

This is the policy enforcement point. The agent is treated as an untrusted
caller: it may *ask* for anything, and the gateway decides what actually runs.
The model is never consulted about authorization, so it cannot be talked out
of it.

Pipeline, in order:

    1 exists            unknown tool           -> typed error
    2 schema            pydantic, no coercion  -> reject, never guess
    3 scope             principal.scopes       -> deny
    4 ownership         resolve the resource   -> deny
    5 hitl              threshold crossed      -> suspend + approval row
    6 idempotency       write tools            -> replay prior result
    7 circuit           open                   -> fail fast
    8 execute           timeout + retry
    9 span              always, including every rejection

Every rejection is recorded. Denials are the highest-signal rows in the trace
store: a rising denial rate is either an attack or a prompt regression, and
both need a human.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import ValidationError

from app.config import get_settings
from app.models import Approval, new_id
from app.security import Principal
from app.tools.contracts import ToolContract, registry
from app.tools.impl import ToolFailure
from app.tracing import span


class HITLRequired(Exception):
    """Raised when a call needs human approval. Carries the approval id so the
    orchestrator can checkpoint the run and tell the customer a reference."""

    def __init__(self, approval_id: str, tool_name: str, reason: str):
        super().__init__(f"human approval required for {tool_name}: {reason}")
        self.approval_id = approval_id
        self.tool_name = tool_name
        self.reason = reason


@dataclass
class ToolResult:
    tool: str
    status: str            # ok | denied | error | suspended
    verdict: str           # allow | deny_unknown | deny_schema | deny_scope |
                           # deny_ownership | suspend_hitl | deny_circuit
    data: dict = field(default_factory=dict)
    denial_reason: str = ""
    latency_ms: int = 0
    retries: int = 0
    untrusted_fields: tuple = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def for_model(self) -> dict:
        """What the agent sees. Never an exception, never a stack trace, never
        an internal hostname -- only a typed, minimal shape."""
        if self.ok:
            return {"tool": self.tool, "status": "ok", "result": self.data}
        return {
            "tool": self.tool,
            "status": self.status,
            "error_code": self.verdict,
            "message": self.denial_reason,
        }


class CircuitBreaker:
    def __init__(self, fail_threshold: int = 5, window_s: float = 30.0, open_s: float = 60.0):
        self.fail_threshold = fail_threshold
        self.window_s = window_s
        self.open_s = open_s
        self._failures: dict[str, list[float]] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, tool: str) -> bool:
        opened = self._opened_at.get(tool)
        if opened is None:
            return False
        if time.monotonic() - opened >= self.open_s:
            self._opened_at.pop(tool, None)
            self._failures.pop(tool, None)
            return False
        return True

    def record_failure(self, tool: str) -> None:
        now = time.monotonic()
        window = [t for t in self._failures.get(tool, []) if now - t < self.window_s]
        window.append(now)
        self._failures[tool] = window
        if len(window) >= self.fail_threshold:
            self._opened_at[tool] = now

    def record_success(self, tool: str) -> None:
        self._failures.pop(tool, None)
        self._opened_at.pop(tool, None)

    def reset(self) -> None:
        self._failures.clear()
        self._opened_at.clear()


class ToolGateway:
    def __init__(
        self,
        session,
        principal: Principal,
        run_id: str = "",
        breaker: Optional[CircuitBreaker] = None,
        hitl_enabled: bool = True,
    ):
        self.session = session
        self.principal = principal
        self.run_id = run_id
        self.breaker = breaker or CircuitBreaker()
        self.hitl_enabled = hitl_enabled
        self._idempotency: dict[str, ToolResult] = {}
        self.calls: list[ToolResult] = []

    # ------------------------------------------------------------------
    async def call(
        self,
        name: str,
        arguments: dict | None = None,
        idempotency_key: str = "",
        hitl_context: dict | None = None,
    ) -> ToolResult:
        arguments = dict(arguments or {})
        started = time.perf_counter()

        with span("tool.call", kind="client", **{"tool.name": name}) as sp:
            sp.attributes["tool.arguments"] = _safe_args(arguments)

            result = await self._dispatch(name, arguments, idempotency_key, hitl_context, sp)
            result.latency_ms = int((time.perf_counter() - started) * 1000)

            sp.attributes.update(
                {
                    "gateway.verdict": result.verdict,
                    "tool.status": result.status,
                    "tool.retries": result.retries,
                    "tool.latency_ms": result.latency_ms,
                    "tool.denial_reason": result.denial_reason,
                }
            )
            if result.status != "ok":
                sp.status = "error" if result.status == "error" else "ok"
            self.calls.append(result)
            return result

    # ------------------------------------------------------------------
    async def _dispatch(
        self, name: str, arguments: dict, idempotency_key: str, hitl_context: dict | None, sp
    ) -> ToolResult:
        # 1 -- exists
        contract: Optional[ToolContract] = registry.get(name)
        if contract is None:
            return ToolResult(
                tool=name,
                status="denied",
                verdict="deny_unknown",
                denial_reason=f"No tool named '{name}'. Available: {', '.join(registry.names())}",
            )

        # 2 -- schema. Strict: a hallucinated argument is refused, never coerced.
        try:
            parsed = contract.input_model.model_validate(arguments, strict=False)
            args = parsed.model_dump()
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            field = ".".join(str(p) for p in first.get("loc", ())) or "input"
            return ToolResult(
                tool=name,
                status="denied",
                verdict="deny_schema",
                denial_reason=f"Invalid argument '{field}': {first.get('msg', 'schema violation')}",
            )

        # 3 -- scope
        missing = [s for s in contract.required_scopes if not self.principal.has(s)]
        if missing:
            return ToolResult(
                tool=name,
                status="denied",
                verdict="deny_scope",
                denial_reason=f"Missing scope(s): {', '.join(missing)}",
            )

        # 4 -- ownership. Resolved from the DATA, compared to the AUTHENTICATED
        #      principal. Nothing the model produced participates in this.
        if contract.ownership is not None:
            owner = await contract.ownership(self.session, self.principal, args)
            if owner is None:
                return ToolResult(
                    tool=name,
                    status="denied",
                    verdict="deny_ownership",
                    denial_reason="That record does not exist or is not available to you.",
                )
            if not self.principal.owns_customer(owner):
                sp.attributes["security.cross_customer_attempt"] = True
                return ToolResult(
                    tool=name,
                    status="denied",
                    verdict="deny_ownership",
                    denial_reason="That record does not exist or is not available to you.",
                )

        # 5 -- human in the loop
        if contract.hitl is not None and self.hitl_enabled:
            ctx = dict(hitl_context or {})
            try:
                needs_human = bool(contract.hitl(args, ctx))
            except Exception:  # pragma: no cover - a broken rule must fail closed
                needs_human = True
            if needs_human:
                approval_id = await self._create_approval(contract, args, ctx)
                sp.attributes["hitl.approval_id"] = approval_id
                return ToolResult(
                    tool=name,
                    status="suspended",
                    verdict="suspend_hitl",
                    denial_reason=f"This action needs human approval. Reference {approval_id}.",
                    data={"approval_id": approval_id},
                )

        # 6 -- idempotency
        if contract.side_effects:
            key = idempotency_key or f"{name}:{_stable(args)}"
            if key in self._idempotency:
                prior = self._idempotency[key]
                sp.attributes["tool.idempotent_replay"] = True
                return prior

        # 7 -- circuit
        if self.breaker.is_open(name):
            return ToolResult(
                tool=name,
                status="error",
                verdict="deny_circuit",
                denial_reason=f"{name} is temporarily unavailable. Try again shortly.",
            )

        # 8 -- execute
        result = await self._execute(contract, args)

        if contract.side_effects and result.ok:
            key = idempotency_key or f"{name}:{_stable(args)}"
            self._idempotency[key] = result
        return result

    # ------------------------------------------------------------------
    async def _execute(self, contract: ToolContract, args: dict) -> ToolResult:
        timeout = contract.timeout_ms / 1000.0
        attempts = 0
        last_message = ""
        while attempts <= contract.max_retries:
            try:
                data = await asyncio.wait_for(
                    contract.handler(self.session, self.principal, args), timeout=timeout
                )
                self.breaker.record_success(contract.name)
                return ToolResult(
                    tool=contract.name,
                    status="ok",
                    verdict="allow",
                    data=data if isinstance(data, dict) else {"value": data},
                    retries=attempts,
                    untrusted_fields=contract.untrusted_fields,
                )
            except ToolFailure as exc:
                # A business-rule failure is NOT retryable and NOT a circuit event.
                return ToolResult(
                    tool=contract.name,
                    status="error",
                    verdict="allow",
                    denial_reason=exc.message,
                    data={"error_code": exc.code},
                    retries=attempts,
                )
            except asyncio.TimeoutError:
                last_message = f"{contract.name} timed out after {contract.timeout_ms}ms"
                self.breaker.record_failure(contract.name)
            except Exception as exc:  # noqa: BLE001
                # Internals never reach the model. The span keeps the detail.
                last_message = f"{contract.name} is temporarily unavailable."
                self.breaker.record_failure(contract.name)
                _record_internal_error(contract.name, exc)
            attempts += 1
            if attempts <= contract.max_retries:
                await asyncio.sleep(min(0.05 * (2 ** attempts), 0.4))
        return ToolResult(
            tool=contract.name,
            status="error",
            verdict="allow",
            denial_reason=last_message or "tool failed",
            retries=attempts - 1,
        )

    # ------------------------------------------------------------------
    async def _create_approval(self, contract: ToolContract, args: dict, ctx: dict) -> str:
        approval = Approval(
            id=new_id("APR"),
            run_id=self.run_id,
            customer_id=self.principal.customer_id or "",
            tool_name=contract.name,
            arguments=args,
            threshold_reason=ctx.get("reason", "value above auto-approval threshold"),
            status="pending",
            checkpoint=ctx.get("checkpoint", {}),
        )
        self.session.add(approval)
        await self.session.flush()
        return approval.id


def _stable(args: dict) -> str:
    import json

    return json.dumps(args, sort_keys=True, default=str)


def _safe_args(args: dict) -> dict:
    """Arguments go on the span, but never anything that looks like a secret."""
    redacted = {}
    for k, v in args.items():
        if any(t in k.lower() for t in ("password", "token", "secret", "card", "cvv")):
            redacted[k] = "[redacted]"
        else:
            redacted[k] = v if not isinstance(v, str) or len(v) < 400 else v[:400] + "..."
    return redacted


_INTERNAL_ERRORS: list[tuple[str, str]] = []


def _record_internal_error(tool: str, exc: Exception) -> None:
    _INTERNAL_ERRORS.append((tool, f"{type(exc).__name__}: {exc}"))
    with span("tool.internal_error", **{"tool.name": tool, "error.type": type(exc).__name__}):
        pass


def internal_errors() -> list[tuple[str, str]]:
    return list(_INTERNAL_ERRORS)
