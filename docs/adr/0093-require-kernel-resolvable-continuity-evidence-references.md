---
status: accepted
---

# Require kernel-resolvable continuity evidence references

A successful Continuity Review Receipt may cite only a typed, stable
`ContinuityEvidenceRef` whose supported kind and identifier the kernel can
resolve to an authoritative record and verify against the reviewed Character,
approved Manifest or exact `Agent × User` relationship. Host-provided labels,
descriptions, identifier prefixes and self-reported scope are non-authoritative;
an unknown, deleted, dangling or cross-scope reference fails the review before
Turn completion rather than being stripped, downgraded or silently accepted.

## Consequences

The public contract is narrower than accepting arbitrary strings, but a hostile
or contaminated host cannot authorize another relationship's memories merely
by placing their IDs in the evaluator request. Supported evidence kinds form a
versioned allowlist, and each kind must define a kernel resolver and ownership
rule before it can appear in a portable receipt.

## Initial a8 allowlist

Persona authority may reference the Character Blueprint or an exact source
span, and a claim, Formative Experience, Meaning Capsule, Contextual Voice
Pattern or approved Persona Growth belonging to the Manifest and Persona
Instance bound to the review. Relationship authority may reference the bound
Relationship Premise or Premise Experience, a Source Turn, an accepted
Relationship Event, a formal Persona Reflection Record, or a MemoryNode with
complete provenance in the exact reviewed relationship.

Relationship State metrics, Current Beliefs, State Reasons, Episodes,
Relationship Chapters, Recall Projections, Recall Signals and legacy memories
without complete provenance are not direct a8 evidence authorities. An
evaluator may read a derived projection as bounded context, but a Finding must
cite the resolvable authoritative records behind it rather than use the
projection to prove itself.
