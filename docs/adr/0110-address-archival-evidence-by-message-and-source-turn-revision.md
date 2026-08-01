---
status: accepted
---

# Address archival evidence by message and Source Turn revision

`TurnMessage` already has a stable `message_id`, while the containing
`TurnRecord` owns the canonical `source_revision`. Relationship-event evidence
already addresses visible messages with this pair. Adding a separate message
revision for a8 would create two revision authorities even though messages are
immutable inside a Source Turn revision.

An Archival Evidence Citation therefore sets `source_id` to the exact
`TurnMessage.message_id` and `source_revision` to the containing
`TurnRecord.source_revision`. The kernel resolves that pair only inside the
bound Source Turn and exact relationship; apparent global uniqueness of a
message ID is never sufficient scope proof. The extractor does not supply a
role.

Schema `"2"` requires a non-empty quote and explicit `start` and `end` offsets
for every citation. Offsets use Unicode code points, matching Python `str`, and
must select the quote exactly. The kernel does not infer omitted offsets by
searching for the first copy of a repeated quote. This is intentionally stricter
than the existing legacy relationship citation convenience behavior.

After verification, the durable Artifact Evidence Reference contains a
deterministic `evidence_id`, source ID, Source Turn revision, kernel-resolved
role, message SHA-256 and exact offsets. Its identity binds the relationship,
Source Turn, source ID, revision, hash and offsets. It does not duplicate the
quote; the Source Transcript remains the content authority.

## Consequences

a8 reuses the existing message-addressing model without changing the Turn
message format. Repeated text remains unambiguous, Unicode behavior is testable,
and a copied message ID cannot move evidence across a Turn or relationship.
Portable modern references require their exact Source Turn dependency closure;
legacy artifacts without it remain `legacy_unavailable`.
