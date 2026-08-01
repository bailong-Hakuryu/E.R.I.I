---
status: accepted
---

# Bind continuity review receipts to final delivered replies

`ContinuityEvaluationCoordinator` returns a self-bound, temporary
`ContinuityEvaluationResult`. Its immutable `ContinuityReviewBinding` identifies
the relationship, Turn, Persona Instance and approved Manifest; records SHA-256
digests and lengths for the current User message and proposed Agent reply; and
freezes the non-sensitive persona, relationship and voice-activation reference
IDs that the evaluator was allowed to cite. It does not retain the complete
Prompt, model reasoning, tool output or an extra copy of either message.
Hashes cover the exact UTF-8 bytes without text normalization; message lengths
and Finding spans count Unicode code points. This convention is part of the
portable receipt contract rather than an implementation accident.

The normal host Interface passes that Result to the existing `complete_turn()`
operation together with the exact Agent reply that became visible. The Turn
Ledger, rather than the host, builds `ContinuityReviewReceipt`, recomputes the
supported deterministic aggregation and style advisory, verifies the final
reply and open-Turn binding, and writes the receipt in the same open-to-completed
compare-and-swap transition as the Source Transcript, delivery disposition and
Source Processing Plan. A Result for draft A cannot be attached to reply B, a
different Turn or a different relationship. `overridden` records a host decision
to display the same evaluated text despite its gate verdict; it never means that
the host substituted different text.

The kernel records delivery truth but does not control the host interface. A
formal product defaults to withholding, retrying or regenerating when continuity
evaluation is unavailable or fails, while a host may still explicitly deliver a
reply to preserve interaction availability. Such a modern Turn records
`not_evaluated` or `failed` rather than fabricating a successful review. The
visible reply remains part of the Source Transcript, but the missing review
cannot automatically authorize Continuity Basis, Persona Reflection, Persona
Growth or relationship progression. This unreviewed delivery is distinct from
`overridden`, which requires a completed verdict for the same delivered text.

Only a complete, self-bound `ContinuityEvaluationResult` can produce an
available `ContinuityReviewReceipt`. The compatibility
`continuity_assessment` input may represent only `not_evaluated` or `failed` in
new Turn data; a bare `completed` assessment is rejected because it has no
Findings, exact message binding, evidence set or evaluator descriptor to audit.
Historical a7 summaries remain readable, but their presence does not promote a
legacy Turn into an a8-reviewed Turn or permit current rules to invent the
missing receipt.

The one-shot `record_turn()` convenience records an exchange that is already
visible and therefore cannot establish that review preceded delivery. It may
create only an explicit unreviewed delivery and cannot accept a successful
`ContinuityEvaluationResult` or manufacture a `ContinuityReviewReceipt`.
Auditable pre-delivery review uses the staged `begin_turn()` → evaluate → apply
the host gate → `complete_turn()` lifecycle. Both paths still write the same
Turn Ledger and preserve the actual Source Transcript.

Modern completed Turns use three delivery dispositions with strict legal
combinations. `shown` requires `aligned` or `supported_new_choice`;
`overridden` requires a completed `review_required` or `unsupported_drift`
verdict for the exact same evaluated text; and `shown_unreviewed` requires
`not_evaluated` or `failed`, including invalidation by a pre-delivery authority
revocation. Any cross-combination is rejected. A withheld, discarded or
replaced draft is not a delivered Source Transcript message and has no delivery
disposition; replacement text requires its own Result.

Every `overridden` or `shown_unreviewed` completion requires a versioned
`DeliveryExceptionRecord` containing the disposition, actor kind, opaque stable
actor ID, bounded reason code, decision time and an optional related sanitized
Reply Attempt number. Ordinary `shown` completion rejects that record. The
portable record contains no free-text justification, provider error body,
Prompt, model reasoning or credential; it proves that the host declared an
explicit exception, not that E.R.I.I. authenticated or authorized the actor.
Actor authentication and authorization remain a later host security boundary.
ADR-0115 freezes the closed actor and reason vocabulary, their legal
disposition combinations, and the separation between a review failure cause
and a delivery decision reason.

The receipt is embedded in `TurnRecord`, not stored in a second receipt ledger.
The existing Turn query Interface therefore returns it without adding another
top-level `ERIIEngine` method. Terminal idempotency compares the complete review
state, so matching visible text with different findings, evaluator identity or
policy version is a conflict rather than a successful retry. FileStorage and
SQLiteStorage continue to persist the whole Turn JSON atomically; SQLite needs
no physical schema migration because `source_turns.data` already owns that
payload. MemoryPack carries the same nested Turn representation.

An a8 MemoryPack containing a modern Receipt must also contain every referenced
authority needed to validate that Receipt, or the exact same authority must
already exist in the target storage. A missing, dangling, cross-scope,
conflicting or unknown-version dependency rejects the complete import before
its first write. Import never removes invalid references or downgrades a modern
Receipt to `legacy_unavailable`; a future redacted export requires a distinct
format and an explicit loss report.

Turn data has an explicit format version independent from `record_version`,
which remains only the lifecycle compare-and-swap revision. A completed legacy
Turn that predates the receipt field is represented as `legacy_unavailable` and
is never re-evaluated with current rules. A modern Turn must distinguish an
available receipt, an explicit not-evaluated or failed assessment, and legacy
unavailability; a missing receipt in a modern completed record is corruption,
not permission to downgrade it to legacy. Unknown future Turn, receipt,
aggregation-policy or MemoryPack versions fail before any import write.

The distinction is represented by one required, versioned
`ContinuityReviewRecord` discriminated union on every modern completed Turn:
`reviewed` contains exactly one full Receipt; `not_evaluated` contains a bounded
reason code; `failed` contains a sanitized failure classification and optional
evaluator/Reply Attempt reference; and `legacy_unavailable` may retain a
summary-only historical assessment but is never created by a modern completion
Interface. Open and abandoned Turns have no final Review Record. The previous
`continuity_assessment` property becomes a derived compatibility view rather
than a second writable authority.

That deprecated view is deliberately fail-closed for Legacy data. Modern
`reviewed`, `not_evaluated` and `failed` records may derive their corresponding
summary, but `legacy_unavailable` returns `None` even when it preserves an a7
summary that once said `completed/aligned`. The exact old summary remains
readable as `ContinuityReviewRecord.legacy_summary` and portable for historical
display. Returning the old completed status through the compatibility property
would let callers mistake summary-only history for an a8 Receipt; synthesizing
`not_evaluated` would falsely claim the old evaluator never ran.

A legacy `open` Turn has a real persisted User message but no original Turn
Context Baseline. Upgrade never fabricates that baseline from current persona
or relationship state. The Turn may be explicitly abandoned or completed only
as `not_evaluated` with reason `legacy_open_without_context_baseline` and
`shown_unreviewed`; it cannot receive a successful a8 Receipt. Existing legacy
completed Turns remain `legacy_unavailable` and retain any summary-only
assessment without current-rule re-evaluation.

## Consequences

The host keeps one small Interface: evaluate a draft, apply its product gate,
then complete the Turn with the returned Result and the exact displayed text.
Receipt construction, consistency checks and persistence semantics remain local
to one deep module and one ledger transition. Receipt references are stable,
non-sensitive identifiers rather than copied context text. ADR-0093 defines the
kernel-resolvable ownership contract required for those references; string
naming conventions alone remain neither authorization nor proof of ownership.
