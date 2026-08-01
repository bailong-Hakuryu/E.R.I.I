---
status: accepted
---

# Reject quarantined Agent relationship candidates until v0.5 reprocessing

a8 preserves every visible exceptional Agent utterance but intentionally does
not ship the exception-resolution workflow planned for v0.5. Leaving a
relationship candidate pending would create an unimplemented review lifecycle;
failing the complete run would misclassify a deterministic authority boundary
as a technical outage and discard independent User-side work.

This boundary is selected only by the persisted delivery disposition, never by
sentiment, politeness, compliance or likely User approval. A harsh reply that
was regenerated or rolled back until its exact final text received `aligned`
or `supported_new_choice` and was delivered as `shown` is an ordinary Source
Turn. It may reject the User, cause hurt, maintain a boundary or end the
relationship and still enter normal relationship adjudication and memory. An
`overridden` result is an explicit exception, not another spelling of “passed.”

During normal a8 relationship adjudication, every candidate citing an
`overridden` or `shown_unreviewed` Agent message receives a durable
`DecisionOutcome.REJECTED` receipt with stable reason code
`continuity_exception_agent_evidence_quarantined`. It creates no Relationship
Event, state delta, Promise, Open Loop, Persona Reflection or Growth input.
Independent candidates whose evidence remains eligible, including valid
User-only candidates, continue normally. Candidate dependencies use the
existing rejected-dependency rule. A batch with no accepted candidate completes
as `completed + no_accepted_events`, not `partial_failed`, `failed` or a new
pending state.

The rejection is an immutable account of the a8 decision: automatic authority
was insufficient at that time. It is not a claim that the utterance produced no
real comfort, harm, expectation or conflict, and it is not a permanent ban on
recognizing those consequences. The Source Turn, exact evidence, frozen
extraction decision, batch fingerprint and Decision Receipt remain portable.

`0.5.0a1` bridges this boundary only through an explicit new
`historical_reprocessing` identity issued to a caller with the appropriate host
capability. That run cites the original Turn, frozen candidate and a8 rejection,
then appends a `ContinuityExceptionResolution`. Its continuity-authority track
uses only the original frozen causal baseline. Its relationship-consequence
track may additionally cite later relationship-local evidence such as the
User's observable reaction. A relationship consequence never grants continuity
authority, and later continuity approval never automatically accepts the old
relationship candidate.

For an ordinary, pre-delivery-reviewed harmful choice, v0.5 does not need to
launder or pardon the reply. It starts from accepted history and projects the
resulting Relationship Consequence, separate User and Persona stances,
Narrative Tension and a non-coercive Reflection Opportunity. For an exceptional
utterance, the relationship-consequence track can establish the same lived
impact while the continuity-authority track remains unresolved or rejects
future persona authority. In either path, the character may apologize, explain,
maintain the boundary, remain conflicted, withdraw or end the relationship.
The kernel preserves memory and consequences but never selects reconciliation
as the morally preferred result.

Neither migration nor ordinary retry starts this v0.5 processing. The new run,
Resolution and any resulting event have new identities and preserve the old
Run, receipt, reason code, disposition and transcript unchanged.

## Consequences

a8 remains terminal, deterministic and free of placeholder review APIs while
retaining everything v0.5 needs to recognize real consequences without
laundering an exceptional utterance into character truth. FileStorage,
SQLiteStorage and MemoryPack must round-trip both the candidate-level rejection
and, in v0.5, the append-only bridge to any later Resolution.
