---
status: accepted
---

# Freeze adjudication journal baselines

A Relationship Processing Run freezes the direct-event journal count, the adjudication journal count, and a canonical fingerprint of both exact prefixes before extraction becomes durable. Adjudication and recovery evaluate the frozen candidate batch only against those prefixes; accepted events produced inside the batch are added in deterministic dependency-resolution order.

`recorded_at` is event data, not an append sequence. It may come from another device, an import, or a drifting clock, so it cannot establish whether an event existed when a decision was made. A later backdated event must not change an earlier decision, and a prior future-stamped event must remain visible to it.

MemoryPack carries the direct-event journal order once for the relationship and already carries adjudications in commit order. The explicit direct-event IDs are authoritative even when a trusted host has appended the same event ID and payload to both journals. Import reconstructs each run's two prefixes from its high-water marks, verifies the baseline fingerprint, then uses the production adjudicator to replay every `accepted`, `corroborated`, `rejected`, and `ignored` result before any target write. Causal import considers only the head of each journal, so satisfying a cross-journal dependency cannot reorder either journal internally.

When a target relationship already exists, each target journal and its incoming counterpart must be prefix-compatible. A shorter side may be extended, but divergent journal branches are rejected rather than silently merged because merging would invalidate one side's frozen run baselines. Prefix compatibility alone is not sufficient: the longer direct prefix and longer adjudication prefix are also validated as one complete temporal lifecycle, and complete reflection provenance is checked against that merged adjudication view so a target extension cannot introduce a second accepted source for the same reflection event.

## Consequences

Each run adds constant-size baseline metadata instead of copying all prior event IDs, avoiding quadratic Pack growth. First-party Engine event-writing APIs, adjudication, export, and exact-identity import share the relationship-processing guard so a new run observes a stable boundary and migration cannot interleave a foreign prefix. Storage adapters must preserve append order for both journals; replacing those journals with a unified durable sequence remains a compatible future optimization.

The high-water marks and fingerprint establish structural and causal self-consistency, not authenticity. They are unkeyed values stored inside the Pack, so a party able to rewrite the entire Pack can also recompute them. Authenticating an export requires a host-managed signature or MAC (and, where needed, encryption and key management) outside this kernel contract.
