# 0.4.0a8 executable implementation contract

This document turns the accepted a8 domain decisions into a testable wire and
public-interface contract. `CONTEXT.md` remains the project glossary; the ADRs
remain the authority for why these boundaries exist.

Implementation status: complete and published as the `v0.4.0a8` GitHub
prerelease on 2026-08-02. Its tag and published artifacts are immutable.

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

Full archival receipts certify the committed artifact's canonical immutable
commit payload, Source revision and extractor descriptor. For MemoryNode this
intentionally excludes mutable recall/lifecycle fields such as reinforcement,
access counters, state, unresolved/latest markers, supersession and last access.
When a modern fingerprinted receipt
is compacted, the tombstone drops operational details but copies each artifact
kind, stable ID and immutable-commit-payload SHA-256 into content-free
`artifact_commitments`. Recall recomputes the current payload fingerprint and
requires the exact relationship, Source Turn, Source revision, completed
outcome, kind and ID commitment. A same-ID rewrite or a merely well-formed UUID
therefore cannot inherit Ordinary authority.

The projected archival provenance remains partial because the full receipt is
no longer present, while the exact payload commitment can still support
generation authority. These are deliberately separate claims. A Legacy
tombstone without `artifact_commitments` remains readable for idempotency and
audit continuity but cannot certify a current artifact payload or promote it to
Ordinary.

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

MemoryNodes receive one upstream keyword/vector RRF and dynamic-effective-weight
order. Authority is classified before `max_per_type` is applied, and the
authority selector preserves that upstream order instead of running a second
lexical relevance sort. A high-ranked Legacy item therefore cannot consume an
Ordinary type quota before the pools are separated. Exact UTF-8 content
duplicates keep the Ordinary projection.

For `recall_structured()`, `top_k` is the total dynamic Memory projection count
across MemoryNode, structured/Legacy Timeline and Legacy Core candidates.
Legacy fills unused slots; with a full Ordinary pool and `top_k >= 2`, at most
the highest-ranked relevant Legacy projection may replace the lowest Ordinary
projection. With `top_k = 1`, Ordinary wins when present. The compatibility
`recall()` signature preserves its historical Core Memory behavior by adding
that Core as a `legacy_context` candidate after dynamic `top_k` selection. It
does not consume a dynamic slot, but it remains subject to the same hard cost
budget and gains no modern provenance authority.

Required Persona and Relationship Context and the hard projection-cost budget
have priority. A Legacy reservation is best effort and falls back to a fitting
Ordinary candidate rather than overflowing or leaving an avoidable empty slot.
Only final, budgeted Ordinary MemoryNodes may be reinforced.

Apart from that explicit Core compatibility candidate, the legacy `recall()`
signature and string result delegate to the same authority classifier,
selector, budget assembly and Renderer as structured recall.

## Relationship-candidate evidence quarantine

Relationship candidate authority is derived only from the persisted delivery
disposition, never from sentiment, politeness or whether the User is likely to
approve. A candidate that cites an Agent message from an `overridden` or
`shown_unreviewed` Turn receives a durable `rejected` receipt with reason
`continuity_exception_agent_evidence_quarantined`. Its exact verified evidence
is retained, but it creates no Relationship Event, state delta, Promise, Open
Loop, Persona Reflection or Persona Growth input.

Independent User-only candidates continue through ordinary adjudication. A
candidate depending on a quarantined candidate follows the existing
`candidate_dependency_not_accepted` rule. If every candidate is quarantined,
the processing run ends as `completed + no_accepted_events`, not as a technical
failure or a pending review placeholder.

Normal retries and a8 `historical_reprocessing` preserve the same quarantine.
Only v0.5's separately authorized, append-only exception-resolution workflow
may revisit consequences; it will not rewrite the a8 rejection or original
Turn. A harsh or hurtful reply that passed review and was delivered as `shown`
remains ordinary history and is not quarantined merely because it caused harm.

### Direct adjudication and canonical Turn identity

`adjudicate_turn_candidates()` always derives its evidence source from one
persisted completed Turn and writes receipts using
`relationship-turn-adjudication-v1`. The compatibility
`adjudicate_relationship_candidates()` call does not create or replace a Turn
Record. When its `turn_id` already identifies a persisted completed Turn in the
same relationship, the supplied revision, message IDs, roles, contents and
occurrence times must match that record exactly; the resulting receipt is
upgraded to the same persisted-Turn contract and receives the Turn's delivery
quarantine.

When no persisted Turn exists, the call remains a truly transient Legacy path.
Once a relationship adjudication has used that transient Turn ID, a later
`begin_turn()` or `record_turn()` cannot register the same ID as a canonical
Turn. This prevents a historical transient receipt from acquiring modern Turn
authority after the fact.

## Portable and REST closure

MemoryPack import validates every schema `"2"` evidence dependency before the
first target write. Each modern artifact must have its exact Source Turn closure
and a matching tombstone commitment for kind, stable ID and recomputed canonical
payload SHA-256.

`relationship-processing-v1` runs have frozen candidates and journal baselines,
so their ordinary accepted/corroborated/rejected/ignored results can be replayed
through the production adjudicator. Direct adjudication does not persist a
frozen candidate. For `relationship-turn-adjudication-v1`, import therefore
promises only complete revalidation of the exact completed Source Turn,
Evidence identity and the invariant that exceptional Agent evidence remains a
non-pivotal rejection with no Event. A receipt whose contract field is
downgraded is still checked when its matching Turn remains in the Pack. This is
not a claim that ordinary accepted direct Events can be fully reconstructed or
replayed without their original candidate.

Old truly transient records remain Legacy-readable and are not assigned a
canonical Turn during import. FileStorage and SQLiteStorage round trips preserve
the applicable Source Turn, evidence references, quarantined rejection, frozen
processing decision and terminal run outcome. The REST export/import surface
delegates to the same portable-wire validation; it is not a weaker alternate
path. These unkeyed checks establish internal consistency, not origin
authenticity: an unsigned Pack that is rewritten as a whole, removes the Turn,
or coherently downgrades related data still requires a host signature or MAC to
be treated as trusted input.

## Delivery order

The completed implementation is covered by these vertical public slices:

1. Turn baseline, review union, exact reply receipt and exception disposition:
   `test_turn_lifecycle_public.py`, `test_continuity_review_receipt.py`.
2. Typed continuity evidence and non-replayable voice traces:
   `test_continuity_evidence.py`, `test_voice_activation_trace.py`.
3. Schema `"2"` archival evidence and exceptional-Agent quarantine:
   `test_archival_evidence_public.py`.
4. Recall authority classification, selection, rendering and reinforcement:
   `test_recall_authority_public.py`.
5. Relationship-candidate quarantine, MemoryPack closure and REST round trips:
   `test_relationship_evidence_quarantine_public.py`,
   `test_a8_rest_memorypack_roundtrip_public.py`.

Each slice starts with a failing public-seam test, reaches a focused green
state, and then reruns the complete suite before the next slice. The completed
suite is the release gate; these file names document observable seams rather
than private implementation structure.
