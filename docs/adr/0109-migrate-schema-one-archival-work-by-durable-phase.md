---
status: accepted
---

# Migrate schema-one archival work by durable phase

An accepted archival identity freezes its request fingerprint, extractor
descriptor and idempotency binding. Replacing schema `"1"` with schema `"2"`
inside that identity would make a materially different extraction appear to be
the same request. Conversely, a record in the commit phase already owns an
immutable Prepared Archival Batch and binding digest; cancelling it would break
the recovery and exactly-once guarantees without preventing a new model call,
because extraction has already ended.

At the a8 upgrade boundary, terminal schema `"1"` records remain unchanged. A
non-terminal record in `EXTRACTION` without a bound batch becomes permanently
failed with `extractor_schema_upgrade_required` and `retryable=false`. It is not
automatically resampled or migrated. A host that still wants the source
processed must explicitly submit it through a schema `"2"` extractor with a new
idempotency key, producing a new Archival Identity.

A schema `"1"` record in `COMMIT` may finish only when its complete Prepared
Archival Batch, descriptor, binding digest and commit authorization pass the
existing integrity checks. The kernel commits those exact bytes without calling
the extractor again. Resulting artifacts retain schema `"1"` provenance and
project message-level evidence as `legacy_unavailable`; they never become
modern evidence-aware artifacts. Their recall authority follows ADR 0112 rather
than being inferred from summary text. An incomplete or conflicting commit
record is an integrity failure, not permission to reconstruct or resample the
batch.

The host-controlled upgrade procedure first stops and drains the old worker.
On restart, recovery follows the durable record phase rather than an inferred
in-memory state. No migration path mutates an old descriptor, reuses an old
identity for new extraction output or silently creates a replacement task.

## Consequences

No evidence-free extraction begins under the a8 contract, while an already
frozen atomic commit retains its recovery semantics. Operators can distinguish
an intentional schema retirement from extractor or storage outages, and every
post-upgrade schema `"2"` retry has its own auditable identity.
