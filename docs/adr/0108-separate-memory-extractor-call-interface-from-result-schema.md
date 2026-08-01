---
status: accepted
---

# Separate the memory extractor call interface from the result schema

`MemoryExtractorV1` versions the Python call interface exposed to a host: an
extractor supplies a descriptor and implements `extract(request)`. Adding
message-level evidence changes the structure and semantics of the returned
Archival Extraction Decision, but it does not change how the extractor is
called. Creating a nearly identical `MemoryExtractorV2` would conflate those
two version axes and force host adapters to migrate without gaining a new call
boundary.

The authoritative result contract is therefore
`ExtractorDescriptor.extraction_schema_version`. Schema `"1"` identifies the
legacy result without Archival Evidence Citations. It remains readable and its
default remains `"1"` so an unchanged adapter cannot accidentally claim modern
support, but it cannot be used for a new archival submission after the a8
contract takes effect. Schema `"2"` identifies the evidence-aware result and is
the minimum explicitly declared schema for every new a8 archival submission.
The kernel rejects an ineligible schema before assigning a new Archival Identity
or queue task.

There is no parallel `supports_evidence` flag: a boolean could disagree with the
schema and create two authorities for the same capability. A future
`MemoryExtractorV2` is justified only when the invocation method, lifecycle or
error model changes. Documentation must call `MemoryExtractorV1` the call
interface version and schema `"2"` the output contract version; neither may be
silently relabeled as the other.

## Consequences

Existing imports and adapter call shapes remain stable while modern evidence
support is explicit and fail-closed. New extractors, fixtures and submissions
must opt in to schema `"2"`; historical schema `"1"` artifacts keep their
truthful Legacy provenance. Already accepted schema `"1"` tasks cross the a8
upgrade boundary according to ADR 0109; the descriptor and request identity are
never silently rewritten.
