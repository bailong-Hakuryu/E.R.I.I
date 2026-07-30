# Activate contextual voice from sourced interaction signals

## Decision

Runtime Contextual Voice Pattern selection consumes source-typed Interaction Context Signals rather than a reply model's self-declared state.

- `host_observed` describes only externally observable facts such as activity, environment, and communication modality. Public Engine inputs accept only this authority class.
- `core_derived` relationship safety is produced by the deterministic, versioned `RelationshipSafetySignalProjector`. It reads the current Persona Instance's Relationship Snapshot and emits only `low`, `moderate`, or `high`.
- `evaluator_inferred` emotion is optional and produced only through a versioned `InteractionContextEvaluatorV1`. Its request contains the current User message, current relationship state, at most 16 accepted Events from that relationship, host-observed signals, and only the emotion vocabulary present in the approved Manifest. Returned evidence references must belong to that bounded request.

The kernel stamps every derived signal with `relationship_id`, `source_turn_id`, and `producer_version`, plus a non-serialized runtime attestation held only by the producing Engine process. The deterministic matcher rejects a scope mismatch, a manually constructed or deserialized derived label without that attestation, and legacy unscoped derived labels. A Voice Pattern Activation is itself bound to the same relationship and Turn and records its pattern and supporting-signal references.

Repeated matching for the same complete input may reuse a bounded temporary in-process evaluator result. The cache is cleared when the Engine closes, evicted when a Turn reaches a terminal state, and is not portable state.

## Consequences

The reply model cannot unlock a register by asserting an emotion after choosing the wording it wants to justify, and a host cannot forge authority by copying internal source labels or replay a derived signal from another relationship or Turn. Activations can inform reply generation and Continuity Evaluation, but signals, evaluator decisions, runtime attestations, and activations do not persist as personality, relationship history, or long-term memory. Hosts that need emotion-conditioned patterns must configure an independent evaluator; without one, those conditions simply do not activate.
