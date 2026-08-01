---
status: accepted
---

# Resolve continuity exceptions with append-only dual-track decisions

Continuity Exception Quarantine cannot always be permanent. A provider outage,
temporary evaluator failure or missing integration can leave an otherwise
well-grounded reply as `shown_unreviewed`. Permanent quarantine would make an
operational incident permanently weaken the relationship history. Mutating the
old Turn to `shown`, however, would falsely claim that review succeeded before
the User saw the reply.

E.R.I.I. uses an append-only `ContinuityExceptionResolution`. It references the
exact Source Turn and exceptional Agent message, records its own versioned
decision identity and effective time, and never replaces the Turn's original
Continuity Review Record, Delivery Disposition or Delivery Exception Record.
The original record continues to answer what was known and how the reply was
delivered at that time.

Resolution has two independent tracks:

1. The continuity-authority track decides whether the exact historical
   utterance may, from the resolution forward, participate in ordinary
   continuity recall and reasoning. Its review is bound to the original reply,
   the Turn Context Baseline and kernel-resolvable evidence. A positive result
   does not manufacture a delivery-time Receipt or change an original
   `shown_unreviewed` or `overridden` disposition.
2. The relationship-consequence track decides whether the utterance and the
   User's experienced reaction created an observable relationship event with
   bounded effects. It may recognize a promise, expectation, comfort, harm,
   misunderstanding or repair without declaring the utterance psychologically
   or stylistically consistent with the character.

One track cannot authorize the other. A real relationship consequence does not
prove continuity. A later continuity decision does not imply that a promise or
relationship-state change occurred, and does not erase consequences that the
User experienced before the decision. Consumers must select the relevant track
rather than treating Resolution as a single promotion flag.

All decisions are append-only. Later review can supersede an earlier resolution
for current projections through explicit references and deterministic ordering,
but it cannot delete or rewrite any prior decision. Historical rendering can
therefore distinguish delivery-time knowledge, resolution-time knowledge and
the currently effective authority state.

## Continuity-authority eligibility

Ordinary retrospective review is available only when the historical causal
boundary is complete enough to evaluate without invention:

- A modern `shown_unreviewed` Turn caused by an unavailable evaluator or a
  technical evaluation failure is eligible when its original Turn Context
  Baseline and exact delivered reply are intact.
- An `overridden` Turn whose original result was `review_required` is eligible
  for explicit review because the original verdict requested such a decision.
- An `overridden` `unsupported_drift` Turn is not eligible for ordinary
  resampling. It can change current authority only through a separately
  justified Historical Continuity Correction.
- A Turn delivered after Persona Authority Revocation is not eligible while
  that revocation remains valid. Only correction of a demonstrably erroneous
  authority decision can reopen the question.
- A one-shot `record_turn()` exchange, a legacy open Turn without its original
  baseline and a completed legacy Turn with unavailable review provenance are
  ineligible. Current state cannot be substituted for missing historical state.

An eligible review uses the exact historical Agent message and only the
authority and relationship prefix frozen at Turn Opening. Later Relationship
Events, approved Persona Growth, Blueprint revisions or newly imported context
cannot provide retroactive causality. Only a fully bound `aligned` or
`supported_new_choice` result grants continuity authority prospectively;
`review_required`, `unsupported_drift`, failure and insufficient evidence leave
the quarantine in force.

Historical Continuity Correction is a narrow error-correction path, not a
second ordinary review. It must identify a demonstrable evaluator, aggregation
policy or authority-decision defect and recompute against the original frozen
evidence. The corrected current projection and the superseded decision are both
retained. A later change that merely makes a similar utterance plausible cannot
serve as correction of the earlier judgment.

Actor authority follows the host-issued capability boundary in ADR-0098. An
evaluator result, actor label or chat utterance cannot authorize a Resolution by
itself.

## Consequences

Operational failures can be repaired without falsifying history, and harmful or
meaningful relationship effects can be recorded without allowing an OOC reply
to rewrite the Persona Instance. Storage, recall, relationship processing and
MemoryPack portability must preserve the original Turn and the complete ordered
resolution chain. Accepted relationship-track outcomes remain a separate policy
decision rather than being inferred from the existence of a Resolution.
