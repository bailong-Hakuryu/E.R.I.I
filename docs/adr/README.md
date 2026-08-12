# Architecture Decision Record Index

ADRs are historical decision records. “Accepted” means the design was accepted
at the time; it does not by itself prove the corresponding capability was
implemented in the release currently being read. As of 2026-08-03, the latest
historical release is `0.4.0a8`; the implemented b1 source-milestone status is
recorded in [`../b1-implementation-contract.md`](../b1-implementation-contract.md),
and should be reproduced with a reviewed commit SHA. The operational lifecycle
surface is in
[`../data-lifecycle.md`](../data-lifecycle.md).

## Status map

- ADRs 0001–0096 and 0107–0116 describe the implemented v0.3/v0.4 alpha
  foundations unless an individual ADR explicitly marks a narrower status.
- ADRs 0097–0105 describe the accepted v0.5 consequence/inner-review direction.
  They are not b1 APIs. ADR 0106 explicitly freezes a8 and moves that work to
  v0.5.
- ADR 0117 records the accepted v0.5+ Provider-neutral Character Deliberation
  and Deliberation Ensemble direction. It permits an optional recommended
  DeepSeek Adapter but does not describe a b1 Interface or installed capability.
- ADR 0118 narrows the first v0.5 delivery to one observable
  choice-to-consequence vertical slice and separates durable kernel evolution
  from removable Provider, host-integration and multi-model experiments. It
  refines ADR 0106's original a1 scope and supersedes ADR 0111 only on the
  scheduling of historical exception reprocessing; the a8 quarantine and
  append-only reprocessing constraints remain accepted.
- ADR 0119 defines `0.x` versions as source-development milestones and defers
  formal tags, GitHub Release assets and package-registry distribution until
  `1.0`. Local package build and clean-install verification remain engineering
  checks rather than release gates.
- ADR 0120 records the accepted Character Deliberation design contract: a
  sourced Semantic Frame, warm but non-authoritative Interior Scene, exact
  visible-reply binding, host-owned Compact-first/Staged-when-earned
  orchestration, and Session Residue that may become durable only after
  independent reflection and lifecycle gates. The implementation remains
  Experimental and is not a currently shipped capability; see the detailed
  [`development plan`](../architecture/character-deliberation-development-plan.md).
- ADR 0003 predates the b1 lifecycle coordinator. Its broad MemoryPack
  “migration” language is superseded operationally by the explicit distinction
  between backup/restore, side-by-side upgrade and fresh import in
  [`../compatibility.md`](../compatibility.md).
- ADRs 0006, 0057 and 0068 describe REST/receipt domain boundaries, not product
  authentication. The current reference-server security boundary is defined in
  [`../../SECURITY.md`](../../SECURITY.md): one owner key, no user-level
  authorization, TLS, rate limiting or tenant isolation.

Do not rewrite an old ADR merely because implementation advanced. Add a new ADR
when a decision is superseded, and link both records so the reasoning history
remains inspectable.
