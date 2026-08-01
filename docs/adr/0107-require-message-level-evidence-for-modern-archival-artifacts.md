---
status: accepted
---

# Require message-level evidence for modern archival artifacts

Relationship Event candidates already cite exact Source Message spans and the
kernel resolves each citation to a User or Agent role. Current TimelineCandidate
and MemoryCandidate output contains only proposed content and semantic fields.
Source-Turn provenance alone therefore cannot establish whether a derived
artifact depends on the User message, the Agent message or both. That ambiguity
would make a8's per-message continuity-exception quarantine unenforceable.

Every modern Timeline and Memory candidate must include at least one Archival
Evidence Citation. Its `source_id` is a `TurnMessage.message_id`; its
`source_revision` is the containing `TurnRecord.source_revision`, not a second
message revision. It supplies an exact quote and mandatory Unicode code-point
`start` and `end` for transient verification. Schema `"2"` never searches for
the first matching quote when offsets are absent. The kernel resolves the
message inside the exact Source Turn and relationship, verifies the span and
text, computes the message hash and obtains the role from stored data.
Extractors cannot self-declare role, relationship scope or authority.

After validation, the Prepared Archival Batch and committed artifact retain an
Artifact Evidence Reference containing a deterministic evidence identity,
message identity, Source Turn revision, kernel-resolved role, message hash and
exact span. The evidence identity binds the relationship, Source Turn, message,
revision, hash and offsets. References do not retain a second copy of the quoted
text; the canonical Source Transcript remains the content authority.
FileStorage, SQLiteStorage and MemoryPack preserve these references and reject
dangling, cross-Turn, cross-relationship, conflicting or unknown future
versions before any import write.

For an ordinary reviewed Turn, candidates may cite either visible party subject
to the normal fact and memory rules. For `overridden` or `shown_unreviewed`, a
candidate depending only on User evidence may still be archived. Any candidate
that cites the quarantined Agent message invalidates the complete Archival
Extraction Decision before a Prepared Archival Batch is formed. The kernel does
not silently drop that artifact and commit the remainder, because doing so would
change a strict extractor decision and weaken all-or-nothing batch semantics.
The extractor may retry with an authority-compliant decision or return explicit
`no_memory` when no authorized artifact exists.

This is provenance strengthening, not a new Memory Type. Historical artifacts
that predate message-level references remain readable with
`legacy_unavailable` artifact provenance and cannot be promoted by inspecting
their summary content. The existing MemoryExtractorV1 call interface remains
stable; Extractor Descriptor `extraction_schema_version` separately advertises
the returned content contract. Schema `"1"` is the legacy no-citation identity,
while schema `"2"` is the evidence-aware identity required for new a8 archival
submissions. An older descriptor cannot be relabeled as the modern capability.

## Consequences

a8 can preserve User-derived memory from an exceptional Turn without allowing
an unreviewed Agent utterance to become ordinary character knowledge. Modern
extractor implementations and fixtures must explicitly adopt schema `"2"`.
Pending schema `"1"` work follows the durable-phase migration boundary in ADR
0109; neither this evidence contract nor an extractor upgrade rewrites an
existing archival identity. Default recall of evidence-unavailable artifacts
follows the authority tiers in ADR 0112.
