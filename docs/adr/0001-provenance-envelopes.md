# ADR 0001 — Provenance is a property of every fragment in the context

## Status
Accepted. Implemented in `backend/app/provenance.py`.

## Context
Prompt injection is usually discussed as a model problem, which makes it
unfalsifiable: you cannot write a test whose assertion is "the model was not
persuaded". Meanwhile the actual attack surface is structural — text from
sellers, from retrieved documents, from tool responses and from the customer's
own past sessions all arrive as the same undifferentiated string.

## Decision
Nothing reaches an LLM as bare text. Every fragment carries one of five
provenance levels — SYSTEM, USER, MEMORY, RETRIEVED, TOOL_OUTPUT — and is
rendered inside a labelled envelope. Only SYSTEM is trusted. Untrusted content
is sanitised on the way in so it cannot close its own envelope and open a
SYSTEM one.

## Consequences
- "Is the agent injection-resistant?" becomes "did any untrusted-provenance
  span change the tool plan?", which is a `tests/adversarial` assertion.
- Every new context source must declare its provenance. That is deliberate
  friction.
- The envelope notice is a backstop, not the defence. The load-bearing
  defences are the sanitiser, the source-trust filter (ADR 0003) and the Tool
  Gateway (ADR 0002). Never cite the notice alone in a security argument.
