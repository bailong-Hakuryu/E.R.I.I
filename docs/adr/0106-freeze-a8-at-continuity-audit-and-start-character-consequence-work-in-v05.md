---
status: accepted
---

# Freeze a8 at continuity audit and start character-consequence work in v0.5

The a8 planning review established several necessary semantics beyond receipt
persistence: continuity-exception resolution, relationship consequences,
separate User and Persona stances, Narrative Tension outcomes, non-coercive
reflection triggering and character-specific sensitivity profiles. Implementing
all of them inside `0.4.0a8` would contradict the published v0.4 Alpha freeze
and simultaneously change Turn, archival, relationship, reflection, persona
compilation, recall and MemoryPack behavior.

`0.4.0a8` remains the continuity-audit and release-closeout Alpha. It will implement
the exact final-reply Receipt, strict Review Record and delivery dispositions,
typed and relationship-resolvable evidence, frozen Turn context, minimal voice
activation traces, legacy and MemoryPack validation, atomic persistence,
idempotency and release verification. It also enforces the smallest safe
continuity-exception boundary: visible exceptional Agent text remains historical
fact but cannot silently gain ordinary persona, memory or relationship
authority, while User-side evidence from the same Turn is not discarded.
Evidence-aware archival candidates and persisted minimal message references
provide that per-message authority boundary under ADR-0107. Relationship
candidates that cite exceptional Agent evidence receive an immutable
candidate-level rejection under ADR-0111; a8 does not leave a pending review
state or classify this deliberate boundary as a technical failure.
That rejection is disposition-driven rather than sentiment-driven. An exact
final reply that passes the ordinary pre-delivery path remains normal even when
it refuses, angers, distances or hurts the User.

The a8 evaluator and aggregation contract is affectively neutral and tests both
supported refusal or conflict and unsupported affection or commitment. It does
not add exception-resolution APIs, stance projections, tension lifecycle,
reflection sensitivity or growth coupling. No placeholder public methods,
reserved but inactive schema variants or claims of implemented capability are
published merely to anticipate v0.5.

`0.5.0a1` begins the complete vertical character-consequence and inner-review
system described by ADR-0096 through ADR-0105. It will include append-only dual-track
exception resolution, trusted-host domain capability declarations that are not
authentication or authorization, relationship consequence
continuity, separate party stance projections, evidence-based tension outcomes,
`stance_unformed`, mixed global and character-specific reflection triggers, and
multi-directional sensitivity coverage. These features receive full File,
SQLite, MemoryPack, migration, deletion and relationship-isolation semantics
rather than remaining documentation-only flags. v0.5 consumes the original a8
Turn, frozen candidate and rejection receipt through a new explicit
`historical_reprocessing` identity; it appends a Resolution and never changes
the a8 decision in place.
For already reviewed harmful choices, v0.5 operates directly on accepted
history; for exceptional choices it uses the two independent resolution tracks.
Both paths can preserve consequences, memory, stance and later repair behavior
without forcing apology, reconciliation or User-preferred character change.

Later v0.5 Alphas retain the previously planned Portability deep module, narrow
Storage Interfaces, Belief Lineage, Memory Relation ledger, source/support/
lifecycle separation and Continuity Map. The roadmap order makes these changes
explicit instead of hiding a second feature family inside a8.

## Consequences

a8 stays small enough to verify as the final v0.4 Alpha, while accepted character
agency decisions have a concrete implementation release rather than an
indefinite backlog. Until v0.5a1, quarantined authority has no general release
workflow and trusted hosts must treat that limitation honestly. Stable v0.4
data remains the migration source for all v0.5 work.
