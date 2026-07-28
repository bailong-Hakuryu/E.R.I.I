---
status: superseded by ADR-0077
---

# Stabilize archival before layered consolidation

E.R.I.I. will complete trustworthy archival lifecycle semantics and idempotent archival delivery before adding Episode and Relationship Chapter consolidation. Although accepted Relationship Events already have their own event-level idempotency, higher narrative projections amplify missing, partial, or duplicated source material into durable interpretations; separating `0.4.0a5` outcome truthfulness from `0.4.0a6` end-to-end retry idempotency establishes a consistent provenance boundary before `0.4.0a7` introduces rebuildable consolidation.
