---
status: accepted
---

# Quarantine the authority of continuity-exception utterances

A reply that reached the User is part of the relationship's real history even
when it was explicitly overridden or could not complete continuity review.
Removing that reply would falsify the Source Transcript and could erase the
User's real reaction. Treating it as ordinary authoritative memory, however,
would allow one unsupported or out-of-character reply to justify later replies
and progressively rewrite the Persona Instance.

E.R.I.I. therefore preserves fact while quarantining interpretive authority.
Every visible `shown`, `overridden` and `shown_unreviewed` reply remains in the
complete Source Transcript and in portable data. A withheld, discarded or
replaced draft was never visible and remains outside that history. Quarantine
does not alter or redact the text and does not turn a continuity verdict into a
fact verdict.

Source processing continues for an exceptional Turn rather than rejecting the
whole Turn. Evidence remains message-role aware. Material derived solely from
the User message follows the ordinary evidence rules, including the rule that
a User assertion is not automatically an objective fact. Material that depends
in whole or in part on an `overridden` or `shown_unreviewed` Agent message must
retain its exceptional delivery provenance.

An exceptional Agent message initially has only observed-utterance authority:
it proves that the Agent said those words in that relationship at that time. It
does not by itself prove stable character identity, knowledge, belief, attitude
or intention. Memory archival may retain or project the utterance only with an
explicit continuity-exception meaning such as "historical utterance made during
an overridden or unreviewed delivery." It must not enter ordinary persona or
knowledge recall stripped of that qualification.

Relationship extraction may identify the observable interaction, including
that the Agent made a statement and that the User reacted to it. A candidate
that relies on the exceptional Agent message cannot be accepted automatically,
apply a Relationship State delta, advance a relationship stage, or create an
authoritative promise, Open Loop, Persona Reflection, Continuity Basis or
Persona Growth input. In a8 it receives a candidate-level `rejected` Decision
Receipt with reason code
`continuity_exception_agent_evidence_quarantined`. This is a normal policy
outcome, not a processing failure or an unresolved pending state. Independent
User-only candidates continue through ordinary adjudication; if no candidate
is accepted, the run completes as `no_accepted_events`.

The frozen candidate, exact evidence, Source Turn and rejection receipt remain
portable. In v0.5 an authorized host may start a new `historical_reprocessing`
identity and append the dual-track Resolution defined by ADR-0097. It may
recognize a real relationship consequence without granting character authority,
or separately grant prospective continuity authority when the strict historical
eligibility rules permit. Neither track modifies the a8 receipt, and neither is
an automatic migration or retry.

A later explicit decision may recognize relationship consequences that really
occurred or separately grant continuity authority for future processing. Such
recognition does not retroactively change the original continuity verdict,
convert the reply to `shown`, or prove that a review preceded delivery.
Relationship consequence and character-continuity authority remain separate
decisions under ADR-0097.

Ordinary `shown` replies continue through the existing archival and relationship
policies. Quarantine is driven by the persisted delivery disposition and
message provenance, not by content heuristics, model confidence or a later
attempt to infer whether a sentence merely "sounds" out of character.

## Consequences

The full conversation remains trustworthy as a record of what both parties
experienced, while long-term memory cannot turn an exceptional reply into
self-reinforcing personality evidence. Processors must preserve per-message
roles and delivery provenance through candidates, artifacts, recall projections
and relationship decisions. MemoryPack round trips must preserve the quarantine
meaning; import cannot silently strip it or promote exceptional artifacts to
ordinary authority. v0.5 treats the a8 rejection as immutable input to a new
append-only decision, not as a placeholder to overwrite.
