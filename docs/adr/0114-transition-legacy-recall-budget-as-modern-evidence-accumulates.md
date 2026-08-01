---
status: accepted
---

# Transition Legacy recall budget as modern evidence accumulates

Always ranking `legacy_context` as ordinary memory would hide its weaker
authority and let it displace modern evidence. Allowing Legacy only when modern
selection leaves unused slots would have the opposite failure: once a
relationship accumulates `top_k` modern memories, all older shared experience
would abruptly disappear from the generated context.

The default Agent-private memory selection treats `top_k` as the total number
of `ordinary` plus `legacy_context` projections. It first applies the same
deterministic relevance eligibility and existing per-type bounds to both pools.
When fewer than `top_k` relevant ordinary projections exist, relevant Legacy
may fill the remaining slots. This permits a newly upgraded relationship with
only Legacy data to use the full memory allowance instead of appearing amnesic.

When ordinary candidates already fill `top_k` and `top_k >= 2`, the single most
relevant Legacy candidate may replace the lowest-ranked ordinary memory, so at
most one Legacy slot remains. With `top_k = 1`, an ordinary candidate always
wins and Legacy is used only when no relevant ordinary candidate exists. If no
Legacy candidate is relevant, every available slot remains ordinary.

An ordinary and Legacy projection with byte-identical UTF-8 content are an
exact duplicate for this policy: the ordinary projection wins and the Legacy
copy consumes no slot. The kernel does not use semantic similarity to delete or
merge historical records. Selected content is rendered in two explicit prompt
sections, `Verified Memories` followed by
`Legacy Context — provenance incomplete`; it is never mixed under one heading.
`quarantined_history` is outside default generation selection and consumes no
slot.

The hard recall-cost budget and required Persona or Relationship Context remain
higher-priority constraints. A Legacy reservation is best-effort only when its
cost fits after those constraints; it cannot cause budget overflow or evict
required authority context. Selection never applies Recall Reinforcement to a
Legacy projection.

## Consequences

Old relationships retain broad context immediately after upgrade and then
converge toward modern evidence without losing every formative memory. Tests
cover `top_k=1`, Legacy-only data, partially populated modern data, a full
modern pool, no relevant Legacy, exact duplicates and insufficient hard budget.
