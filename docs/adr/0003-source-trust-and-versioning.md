# ADR 0003 — Knowledge-base documents carry trust and effective dates

## Status
Accepted. Implemented in `backend/app/rag/ingest.py` and `retrieve.py`.

## Context
Two failures dominate production RAG and neither is visible in an
end-to-end accuracy number:

1. The system answers correctly from a *superseded* policy.
2. Third-party content — a seller description — is treated as authority.

## Decision
Every document carries `source_trust` (trusted / semi / untrusted) and an
`effective_from` / `effective_to` window. `retrieve_policy` searches trusted
documents only. Seller copy is indexed as untrusted so product questions still
work, and can never answer a policy question.

## Consequences
- RAG poisoning becomes a one-line defence with a direct test.
- The dataset gains a `must_not_cite` field, which is what catches
  stale-policy answers. It is the field most teams omit.
- The corpus needs a version axis from day one; retrofitting one means
  reprocessing everything.
