"""Guardrail chains.

Ordered lists of independent checks. Each returns a Verdict; the chain
resolves to the most restrictive one. Cheap deterministic checks run before
expensive model-based ones.

Every check here is CODE, not a paragraph in a prompt. That is deliberate: a
check written as code has a fixed input, a fixed output and a unit test. A
check written as "please be safe" has none of those.

The number that matters is not detection rate alone. An injection classifier
that refuses "ignore the blue one, show me the black laptop" has 100%
detection and has destroyed the product. Every detector below is measured on
BOTH datasets/adversarial (true positives) and datasets/safety (false
positives), and both numbers gate CI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from app.tracing import span


class Verdict(str, Enum):
    ALLOW = "allow"
    REWRITE = "rewrite"
    ESCALATE = "escalate"
    REFUSE = "refuse"


_SEVERITY = {Verdict.ALLOW: 0, Verdict.REWRITE: 1, Verdict.ESCALATE: 2, Verdict.REFUSE: 3}


@dataclass
class CheckResult:
    check: str
    verdict: Verdict
    reason: str = ""
    matched: str = ""
    rewritten: Optional[str] = None


@dataclass
class ChainResult:
    verdict: Verdict
    text: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.verdict in (Verdict.REFUSE, Verdict.ESCALATE)

    def reasons(self) -> list[str]:
        return [r.reason for r in self.results if r.verdict is not Verdict.ALLOW]

    def triggered(self) -> list[str]:
        return [r.check for r in self.results if r.verdict is not Verdict.ALLOW]


REFUSAL_MESSAGE = (
    "I can't help with that. I can answer questions about your own orders, "
    "our policies, and our products."
)
ESCALATION_MESSAGE = (
    "I'd rather a person handled this with you. I've flagged it for our support "
    "team and someone will pick it up."
)


# ======================================================================
# detectors
# ======================================================================

# --- PII ---------------------------------------------------------------
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
AADHAAR_RE = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
PHONE_IN_RE = re.compile(r"\b(?:\+91[ -]?)?[6-9]\d{9}\b")


def _luhn(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) < 13:
        return False
    checksum, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


def detect_pii(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for m in CARD_RE.finditer(text):
        if _luhn(m.group(0)):
            found.append(("card_number", m.group(0)))
    for m in AADHAAR_RE.finditer(text):
        raw = re.sub(r"[ -]", "", m.group(0))
        if len(raw) == 12 and not any(raw in c for _, c in found):
            found.append(("national_id", m.group(0)))
    for m in EMAIL_RE.finditer(text):
        found.append(("email", m.group(0)))
    for m in PHONE_IN_RE.finditer(text):
        found.append(("phone", m.group(0)))
    return found


def mask_pii(text: str) -> tuple[str, list[str]]:
    kinds: list[str] = []
    out = text
    for kind, value in detect_pii(text):
        kinds.append(kind)
        if kind == "card_number":
            digits = re.sub(r"\D", "", value)
            out = out.replace(value, f"[card ending {digits[-4:]}]")
        elif kind == "email":
            local, _, domain = value.partition("@")
            out = out.replace(value, f"{local[:2]}***@{domain}")
        elif kind == "phone":
            out = out.replace(value, f"[phone ending {re.sub(r'[^0-9]', '', value)[-4:]}]")
        else:
            out = out.replace(value, "[redacted-id]")
    return out, kinds


# --- prompt injection ---------------------------------------------------
# Each pattern requires an imperative AND an instruction-target noun. That
# pairing is what keeps "ignore the blue one" out of the false-positive column.
INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override_instructions", re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}?"
        r"\b(previous|prior|above|earlier|initial|original|all|your|the)\b[^.\n]{0,20}?"
        r"\b(instruction|instructions|prompt|prompts|rule|rules|directive|directives|"
        r"guideline|guidelines|policy|policies|restriction|restrictions)\b", re.I)),
    ("prompt_extraction", re.compile(
        r"\b(reveal|show|print|output|repeat|display|tell me|what (?:is|are))\b[^.\n]{0,30}?"
        r"\b(your |the )?(system prompt|system message|initial prompt|initial instructions|"
        r"your instructions|your rules|your prompt|hidden prompt)\b", re.I)),
    ("persona_override", re.compile(
        r"\b(you are now|from now on you are|act as (?:an? )?(?:admin|administrator|root|developer|dan)|"
        r"pretend (?:that )?you (?:have no|are not bound)|developer mode|jailbreak mode|"
        r"enter dan mode|unrestricted mode)\b", re.I)),
    ("authz_bypass", re.compile(
        r"\b(regardless of|without (?:checking|verifying)|skip|bypass|do not (?:check|verify))\b"
        r"[^.\n]{0,40}?\b(authorization|authorisation|ownership|permission|permissions|"
        r"auth|access control|security check)\b", re.I)),
    ("cross_user_access", re.compile(
        r"\b(another|other|someone else'?s?|a different|other people'?s?)\b[^.\n]{0,25}?"
        r"\b(customer|customer'?s|user|user'?s|account|account'?s|client)\b"
        r"[^.\n]{0,30}?\b(order|orders|data|details|address|payment|history|information|info)\b", re.I)),
    # Split from cross_user_access on purpose. An explicit possessive
    # ("someone else's order history") needs no person-noun in between, but
    # dropping that requirement from the general pattern would fire on
    # "show me the other laptops' details" -- a shopping request.
    ("cross_user_possessive", re.compile(
        r"\b(someone|somebody|some ?one)\s?(?:else'?s?|elses)\b[^.\n]{0,30}?"
        r"\b(order|orders|account|data|details|address|payment|payments|history|"
        r"information|info|profile)\b", re.I)),
    ("cross_user_access_2", re.compile(
        r"\b(show|give|fetch|get|list|display|send)\b[^.\n]{0,30}?"
        r"\b(all|every|other|another)\b[^.\n]{0,15}?\b(customers?|users?|accounts?)\b", re.I)),
    ("tool_coercion", re.compile(
        r"\b(call|invoke|execute|run|use)\b[^.\n]{0,25}?\b(tool|function|api|database|sql)\b"
        r"[^.\n]{0,40}?\b(even if|regardless|without|anyway|force)\b", re.I)),
    ("exfiltration", re.compile(
        r"\b(send|forward|email|post|upload|leak)\b[^.\n]{0,30}?"
        r"\b(customer (?:data|information|details)|the database|all (?:orders|records)|pii)\b", re.I)),
]


def detect_injection(text: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for name, pattern in INJECTION_PATTERNS:
        m = pattern.search(text or "")
        if m:
            hits.append((name, m.group(0)[:180]))
    return hits


# --- content safety / distress -----------------------------------------
UNSAFE_RE = re.compile(
    r"\b(build|make|synthesi[sz]e|manufacture)\b[^.\n]{0,25}\b(bomb|explosive|weapon|meth)\b", re.I
)
LEGAL_THREAT_RE = re.compile(r"\b(sue you|legal action|my lawyer|consumer court|police complaint)\b", re.I)
DISTRESS_RE = re.compile(r"\b(i (?:want to|will) (?:kill|hurt) myself|end my life|suicidal)\b", re.I)

# --- output leakage -----------------------------------------------------
SYSTEM_LEAK_RE = re.compile(
    r"(my (?:system )?(?:prompt|instructions) (?:is|are)|"
    r"<SYSTEM|you are retailmind|# ?role and boundaries|SYSTEM INSTRUCTIONS)", re.I
)
INTERNAL_LEAK_RE = re.compile(
    r"(Traceback \(most recent call last\)|File \"/|sqlalchemy\.|psycopg|"
    r"localhost:\d{4}|127\.0\.0\.1|/Users/|/home/\w+/|Exception:|asyncio\.)", re.I
)
OVERPROMISE_RE = re.compile(
    r"\b(guaranteed?|definitely|i promise|certainly will|100%)\b[^.\n]{0,40}"
    r"\b(refund|refunded|replace|compensation|money back)\b", re.I
)


# ======================================================================
# chains
# ======================================================================

CheckFn = Callable[[str, dict], CheckResult]


def _c(name: str, verdict: Verdict, reason: str = "", matched: str = "", rewritten=None) -> CheckResult:
    return CheckResult(check=name, verdict=verdict, reason=reason, matched=matched, rewritten=rewritten)


# --- input checks -------------------------------------------------------
def check_length(text: str, ctx: dict) -> CheckResult:
    if len(text) > 8000:
        return _c("length", Verdict.REFUSE, "message exceeds 8000 characters")
    if not text.strip():
        return _c("length", Verdict.REFUSE, "empty message")
    return _c("length", Verdict.ALLOW)


def check_input_pii(text: str, ctx: dict) -> CheckResult:
    masked, kinds = mask_pii(text)
    if kinds:
        return _c(
            "pii_input", Verdict.REWRITE,
            f"masked {', '.join(sorted(set(kinds)))} before the model saw it",
            rewritten=masked,
        )
    return _c("pii_input", Verdict.ALLOW)


def check_injection(text: str, ctx: dict) -> CheckResult:
    hits = detect_injection(text)
    if hits:
        names = ", ".join(n for n, _ in hits)
        return _c("prompt_injection", Verdict.REFUSE, f"injection pattern: {names}", hits[0][1])
    return _c("prompt_injection", Verdict.ALLOW)


def check_content_safety(text: str, ctx: dict) -> CheckResult:
    if DISTRESS_RE.search(text):
        return _c("content_safety", Verdict.ESCALATE, "user distress signal")
    if UNSAFE_RE.search(text):
        return _c("content_safety", Verdict.REFUSE, "unsafe request")
    if LEGAL_THREAT_RE.search(text):
        return _c("content_safety", Verdict.ESCALATE, "legal escalation signal")
    return _c("content_safety", Verdict.ALLOW)


def check_topic_scope(text: str, ctx: dict) -> CheckResult:
    # Deliberately permissive. Scope enforcement belongs in the system prompt
    # and in what tools exist -- refusing off-topic questions at the guardrail
    # is a false-positive factory.
    return _c("topic_scope", Verdict.ALLOW)


INPUT_CHAIN: list[CheckFn] = [
    check_length,
    check_input_pii,
    check_injection,
    check_content_safety,
    check_topic_scope,
]


# --- output checks ------------------------------------------------------
def check_output_pii(text: str, ctx: dict) -> CheckResult:
    masked, kinds = mask_pii(text)
    if kinds:
        return _c("pii_output", Verdict.REWRITE, f"redacted {', '.join(sorted(set(kinds)))}", rewritten=masked)
    return _c("pii_output", Verdict.ALLOW)


def check_system_leak(text: str, ctx: dict) -> CheckResult:
    m = SYSTEM_LEAK_RE.search(text)
    if m:
        return _c("system_prompt_leak", Verdict.REFUSE, "response appears to disclose system instructions", m.group(0))
    return _c("system_prompt_leak", Verdict.ALLOW)


def check_internal_leak(text: str, ctx: dict) -> CheckResult:
    m = INTERNAL_LEAK_RE.search(text)
    if m:
        return _c("internal_leak", Verdict.REWRITE, "stripped internal detail from response",
                  m.group(0), rewritten=INTERNAL_LEAK_RE.sub("[internal detail removed]", text))
    return _c("internal_leak", Verdict.ALLOW)


def check_citations(text: str, ctx: dict) -> CheckResult:
    """Every [chunk-id] in the answer must be a chunk that was actually
    retrieved this turn. A citation to something not retrieved is a fabricated
    citation, which is worse than no citation at all."""
    cited = set(re.findall(r"\[([a-zA-Z0-9_\-]+-c?\d+)\]", text))
    if not cited:
        return _c("citation_validity", Verdict.ALLOW)
    available = set(ctx.get("retrieved_chunk_ids") or [])
    if not available:
        return _c("citation_validity", Verdict.REWRITE, "answer cited sources but nothing was retrieved",
                  rewritten=re.sub(r"\[[a-zA-Z0-9_\-]+-c?\d+\]", "", text).strip())
    bogus = cited - available
    if bogus:
        return _c("citation_validity", Verdict.REWRITE, f"removed fabricated citations: {sorted(bogus)}",
                  rewritten=_strip_citations(text, bogus))
    return _c("citation_validity", Verdict.ALLOW)


def _strip_citations(text: str, ids: set) -> str:
    out = text
    for cid in ids:
        out = out.replace(f"[{cid}]", "")
    return re.sub(r"\s{2,}", " ", out).strip()


def check_groundedness(text: str, ctx: dict) -> CheckResult:
    """Cheap lexical groundedness gate for policy answers.

    Not a substitute for the NLI / judge evaluators in evaluation/ -- this is
    the runtime backstop that catches an answer with no support at all.
    """
    if not ctx.get("requires_grounding"):
        return _c("groundedness", Verdict.ALLOW)
    chunks = ctx.get("retrieved_texts") or []
    if not chunks:
        return _c("groundedness", Verdict.ALLOW)
    from app.rag.embed import tokenize

    corpus = set()
    for c in chunks:
        corpus |= set(tokenize(c))
    ans = [t for t in tokenize(text) if len(t) > 4]
    if not ans:
        return _c("groundedness", Verdict.ALLOW)
    supported = sum(1 for t in ans if t in corpus) / len(ans)
    ctx["groundedness_score"] = round(supported, 4)
    if supported < 0.25:
        return _c("groundedness", Verdict.REWRITE,
                  f"answer poorly supported by retrieved context ({supported:.2f})",
                  rewritten=(
                      "I don't have a policy document that clearly covers that, so I'd "
                      "rather not guess. I can raise a support ticket for a human to confirm."
                  ))
    return _c("groundedness", Verdict.ALLOW)


def check_overpromise(text: str, ctx: dict) -> CheckResult:
    m = OVERPROMISE_RE.search(text)
    if m:
        return _c("policy_conformance", Verdict.REWRITE, "softened an unconditional commitment",
                  m.group(0), rewritten=OVERPROMISE_RE.sub("may be eligible for a refund", text))
    return _c("policy_conformance", Verdict.ALLOW)


OUTPUT_CHAIN: list[CheckFn] = [
    check_output_pii,
    check_system_leak,
    check_internal_leak,
    check_citations,
    check_groundedness,
    check_overpromise,
]


# ======================================================================
# runner
# ======================================================================

def run_chain(chain_name: str, checks: list[CheckFn], text: str, ctx: dict | None = None) -> ChainResult:
    ctx = ctx if ctx is not None else {}
    current = text
    results: list[CheckResult] = []
    worst = Verdict.ALLOW
    for check in checks:
        with span("guardrail.check", chain=chain_name, check=check.__name__) as sp:
            res = check(current, ctx)
            sp.attributes.update({"check": res.check, "verdict": res.verdict.value, "reason": res.reason})
        results.append(res)
        if res.verdict is Verdict.REWRITE and res.rewritten is not None:
            current = res.rewritten
        if _SEVERITY[res.verdict] > _SEVERITY[worst]:
            worst = res.verdict
        if worst is Verdict.REFUSE:
            break
    return ChainResult(verdict=worst, text=current, results=results)


def run_input_chain(text: str, ctx: dict | None = None) -> ChainResult:
    return run_chain("input", INPUT_CHAIN, text, ctx)


def run_output_chain(text: str, ctx: dict | None = None) -> ChainResult:
    return run_chain("output", OUTPUT_CHAIN, text, ctx)
