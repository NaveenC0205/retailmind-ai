# ADR 0004 — Three execution tiers, with cassettes as the seam

## Status
Accepted. Implemented in `backend/app/llm/mock.py` and `router.py`.

## Context
Agent tests that call a real model are slow, stochastic and expensive. Teams
respond by running them nightly, then by ignoring them, and AI quality
regressions reach production unchallenged.

## Decision
    mocked     stub LLM + stub tools        every commit  seconds   deterministic
    recorded   cassette replay by hash      every PR      minutes   deterministic
    live       real models + services       nightly       ~30 min   stochastic

The mock LLM has two modes. `guarded` reads only the USER envelope. `naive`
obeys instructions found inside untrusted envelopes — a fully compromised
model, used by the adversarial suite on purpose.

## Consequences
- The agent suite runs on every commit, so people keep running it.
- Recorded behaviour drifts from live behaviour; the nightly live run is what
  catches it.
- An attack suite that only runs against a well-behaved model proves the model
  behaved. Running it against a compromised model proves the architecture
  holds. `tests/adversarial/test_attack_corpus.py` includes a control test
  asserting the compromised model is still compromised — otherwise every test
  in that file could pass for the wrong reason.
