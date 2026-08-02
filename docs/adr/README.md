# Architecture Decision Record Index

ADRs are historical decision records. “Accepted” means the design was accepted
at the time; it does not by itself prove the corresponding capability was
implemented in the release currently being read. As of 2026-08-03, the latest
immutable release is `0.4.0a8`; the implemented, not-yet-published b1 candidate
status is recorded in [`../b1-implementation-contract.md`](../b1-implementation-contract.md),
and the operational lifecycle surface is in
[`../data-lifecycle.md`](../data-lifecycle.md).

## Status map

- ADRs 0001–0096 and 0107–0116 describe the implemented v0.3/v0.4 alpha
  foundations unless an individual ADR explicitly marks a narrower status.
- ADRs 0097–0105 describe the accepted v0.5 consequence/inner-review direction.
  They are not b1 APIs. ADR 0106 explicitly freezes a8 and moves that work to
  v0.5.
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
