# 0.4.0a8 executable implementation contract

This document turns the accepted a8 domain decisions into a testable wire and
public-interface contract. `CONTEXT.md` remains the project glossary; the ADRs
remain the authority for why these boundaries exist.

## Confirmed public test seams

1. `ERIIEngine` Turn lifecycle and source-processing operations.
2. Public Turn and MemoryPack serialization, export validation and import.
3. `recall_structured()` plus the compatibility `recall()` renderer.
4. REST translation of the same Engine and portable-wire behavior.

FileStorage and SQLiteStorage are exercised through the same public behavior.
Tests do not assert private method calls, physical table layouts or internal
collaborator ordering.

## Strict modern wire rules

- Every new portable object declares its own version. Tagged unions also
  declare a discriminator.
- A modern object with a missing or unknown version, unknown field, unknown
  enum or illegal branch combination fails before persistence or import.
- Modern parsers do not coerce strings to integers or booleans. In particular,
  `bool` is not a valid integer offset or revision.
- Legacy defaults are applied only by an explicit legacy parser or migrator;
  they are not part of a strict modern parser.
- Canonical JSON uses UTF-8 without BOM, sorted object keys,
  `ensure_ascii=False`, compact separators and `allow_nan=False`. No Unicode
  normalization is applied.
- A canonical fingerprint is lowercase SHA-256 over a domain-separated object
  containing the wire type, version and complete identity payload. The
  fingerprint field itself is excluded.
- Arrays with domain order preserve it. Arrays representing sets are sorted by
  their stable identity and reject duplicate members.

## Modern Turn wire

`TurnRecord.turn_format_version` is independent from the lifecycle
`record_version`. New a8 Turns use `turn-record/v2`; records without that field
are parsed only through the Legacy v1 path. Every modern completed Turn owns
exactly one `ContinuityReviewRecord`.

### ContinuityReviewRecord v1

Common fields are:

```text
review_record_version = "continuity-review-record/v1"
kind = reviewed | not_evaluated | failed | legacy_unavailable
```

The strict branches are:

- `reviewed`: exactly one `receipt`.
- `not_evaluated`: exactly one `reason_code`.
- `failed`: `failure_classification` plus explicit nullable
  `evaluator_descriptor` and `reply_attempt_number`.
- `legacy_unavailable`: an explicit nullable `legacy_summary` and no modern
  review claim.

The compatibility `continuity_assessment` property is derived. It returns the
modern summary for the first three branches and `None` for
`legacy_unavailable`; callers read historical summary data explicitly from
`legacy_summary`.

### DeliveryExceptionRecord v1

The wire fields are:

```text
exception_record_version = "delivery-exception-record/v1"
disposition = overridden | shown_unreviewed
actor_kind = host_policy | human_operator | data_owner
actor_id
reason_code = availability_fallback | configured_delivery_policy |
              out_of_band_judgment | preexisting_visible_exchange |
              legacy_turn_completion
decided_at
reply_attempt_number = null | positive integer
```

`overridden` permits only the first three reason codes;
`shown_unreviewed` permits all five. `configured_delivery_policy` requires
`host_policy`; `out_of_band_judgment` requires `human_operator` or
`data_owner`. `shown` rejects this record.

The disposition and review combinations are closed:

- `shown` requires a reviewed `aligned | supported_new_choice` receipt.
- `overridden` requires a reviewed `review_required | unsupported_drift`
  receipt for the same delivered bytes and one Delivery Exception.
- `shown_unreviewed` requires `not_evaluated | failed` and one Delivery
  Exception.

`record_turn()` can only create `shown_unreviewed` with
`preexisting_visible_exchange`; it cannot accept a successful evaluation
result. Legacy completed Turns remain `legacy_unavailable`. A Legacy open Turn
can be completed only as `shown_unreviewed` with
`legacy_open_without_context_baseline` and `legacy_turn_completion`.

### TurnContextBaseline v1

Every new a8 open Turn freezes:

- Relationship, Turn and Persona IDs;
- Character Blueprint ID, revision and source hash;
- the explicit nullable active Manifest ID and content fingerprint;
- the ordered approved Persona Growth prefix with immutable content
  fingerprints;
- Relationship Premise identity and content fingerprint;
- direct-event and adjudication prefix counts and fingerprints; and
- the versions of the baseline, relationship projection, interaction-context
  and voice-matcher policies used at opening.

The baseline carries its own canonical fingerprint. It stores boundaries and
identities, not copied history. Successful review requires the exact baseline
and rechecks that its Manifest and growth authorities have not been revoked.
That recheck and the `open -> completed` Turn CAS are one storage operation:
FileStorage holds its cross-process context root lock through both steps, while
SQLiteStorage performs both under one `BEGIN IMMEDIATE` transaction. A
revocation and reviewed completion therefore have a defined order and cannot
interleave between validation and sealing. A later revocation does not erase a
Turn that already completed successfully, and an exact completion retry remains
idempotent.

## Continuity evidence and voice audit wire

`ContinuityEvidenceRef` v1 is a typed `{ref_version, kind, locator, ref_id}`
object. `ref_id` is the canonical fingerprint of version, kind and locator.
The initial kinds are those frozen by ADR-0093. The resolver, not the locator,
proves Character, Manifest or Relationship ownership against the parent review
binding. Findings cite `ref_id`; the parent binding carries the exact typed
Persona and Relationship reference arrays supplied to that review.

Runtime `VoicePatternActivation` objects are never serialized into a receipt.
Only activations actually cited by a final `voice_style` Finding with reason
`supported_contextual_voice` may be projected one way into
`VoiceActivationTrace` v1. A Trace stores bounded approved condition values,
source class, producer identity and typed evidence references, has no runtime
attestation, and exposes no conversion back to an Activation. Trace presence
must not affect Findings, verdict, disposition or future recall input.
Finding evidence references and Activation references are separate fields.
Only `voice_style + supported_contextual_voice` may use
`voice_activation_refs`, and it must also cite the matching typed Contextual
Voice Pattern. Result and Receipt wire use `voice_activation_traces`; the
runtime `voice_pattern_activations` collection never crosses that boundary.

## Archival evidence wire

Extractor schema `"2"` requires each Timeline or Memory candidate to carry
between one and sixteen `ArchivalEvidenceCitation` v1 values. A Citation is an
untrusted exact message-span claim:

```text
citation_version = "archival-evidence-citation/v1"
kind = "message_span"
source_id = TurnMessage.message_id
source_revision = TurnRecord.source_revision
quote
start
end
```

Offsets are non-boolean Unicode code-point integers satisfying
`0 <= start < end <= len(message.content)` and the exact slice must equal the
quote without trimming, newline conversion or Unicode normalization. The
extractor never supplies role, relationship or Turn scope.

After validation the kernel creates `ArtifactEvidenceReference` v1 containing
the relationship, Source Turn, message, Source revision, kernel-resolved role,
message SHA-256 and exact span. It does not retain quote text. `evidence_id` is
`ae1_` plus the SHA-256 of the canonical identity payload. Reference arrays are
set-valued, reject exact duplicates, preserve valid overlapping spans and are
stored in ascending `evidence_id` order. Reordering input cannot change an
artifact fingerprint or Prepared Batch digest.

Modern artifact identity includes the complete reference array. A modern
MemoryPack must carry the exact Source Turn dependency closure and import must
recompute every role, hash, range and evidence identity before its first write.
Schema `"1"` identities are never rewritten or promoted by inspecting content.

## Recall authority selection

`MemoryRecallProjection` adds a projected `authority_tier`:

```text
ordinary | legacy_context | quarantined_history
```

This is derived from current provenance and Turn delivery authority; it is not
persisted as a second authority field on MemoryNode or TimelineEntry. Public
generation excludes Legacy and Quarantined content. Default Agent-private
generation excludes Quarantined content and renders selected Ordinary and
Legacy content in separate sections.

`top_k` is the total Memory projection count across MemoryNode, structured and
Legacy Timeline, and Legacy Core projections. Ordinary and Legacy use the same
deterministic relevance ordering. Exact UTF-8 content duplicates keep the
Ordinary projection. Legacy fills unused slots; with a full Ordinary pool and
`top_k >= 2`, at most the highest-ranked Legacy projection may replace the
lowest Ordinary projection. With `top_k = 1`, Ordinary wins when present.

Required Persona and Relationship Context and the hard projection-cost budget
have priority. A Legacy reservation is best effort and falls back to a fitting
Ordinary candidate rather than overflowing or leaving an avoidable empty slot.
Only final, budgeted Ordinary MemoryNodes may be reinforced.

The legacy `recall()` signature and string result remain, but it delegates to
the same authority classifier, selector and Renderer as structured recall.

## Delivery order

Implementation proceeds as vertical public slices:

1. Turn baseline, review union, exact reply receipt and exception disposition.
2. Typed continuity evidence and non-replayable voice traces.
3. Schema `"2"` archival evidence and exceptional-Agent quarantine.
4. Recall authority classification, selection, rendering and reinforcement.
5. Relationship-candidate quarantine, MemoryPack closure and REST round trips.

Each slice starts with a failing public-seam test, reaches a focused green
state, and then reruns the complete suite before the next slice.
