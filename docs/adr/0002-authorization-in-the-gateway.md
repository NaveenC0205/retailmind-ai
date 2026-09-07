# ADR 0002 — Authorization lives in the Tool Gateway, never in the prompt

## Status
Accepted. Implemented in `backend/app/tools/gateway.py`.

## Context
The tempting design is to tell the agent to check ownership before acting. It
reads well in a prompt and it fails the first time someone writes a
sufficiently confident sentence.

## Decision
The gateway resolves the calling principal from the authenticated request and
compares it against the resource it looks up itself. The model is not
consulted. There is no tool parameter through which a caller can express "act
as someone else".

## Consequences
Two independent test surfaces, and conflating them is the most common mistake
in agent testing:

- **Agent-plan tests** assert the agent *intends* the right sequence — verify
  ownership before cancelling. A failure is a quality regression.
- **Gateway tests** assert that a wrong plan is *refused*. A failure is a
  security incident.

A system that passes the first and fails the second is one clever prompt from
a breach. A system that passes the second and fails the first is merely
annoying. `tests/adversarial` runs against a model that always falls for the
attack, precisely so the second surface is the one under test.

Denial messages are identical for "not yours" and "does not exist", so the
gateway is not an enumeration oracle.
