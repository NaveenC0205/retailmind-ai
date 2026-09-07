# ADR 0005 — Gates compare paired, with confidence intervals

## Status
Accepted. Implemented in `backend/app/evaluation/gates.py`.

## Context
A 200-item dataset carries roughly ±3–4 percentage points of noise at 90%
accuracy. A gate that fires on any drop fires constantly, the team disables
it, and the programme is back to opinions.

## Decision
Two gate shapes.

- **Hard-zero** (unauthorized execution, cross-customer access): any
  occurrence fails. No statistics. One breach is one breach.
- **Statistical**: compared *paired* against a baseline on identical items,
  with a percentile bootstrap confidence interval on the difference. Pairing
  is the single largest variance reduction available, because the same item is
  hard or easy for both systems.

Every run records dataset version and checksum, prompt version, model config,
seed and item count, so any number can be reproduced.

## Consequences
- Reports say "the interval includes zero" instead of "regression".
- An underpowered result is annotated as underpowered rather than silently
  passing.
- Mock-model caveat: `prompts/v2` is deliberately hallucination-prone, and the
  mock LLM simulates that effect so the demo runs offline. That is a
  *simulation of a known effect*, not evidence of it. Run the comparison
  against a real model before drawing conclusions about prompt wording.
