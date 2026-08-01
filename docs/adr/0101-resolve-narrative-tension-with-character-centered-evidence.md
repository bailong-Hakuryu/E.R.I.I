---
status: accepted
---

# Resolve narrative tension with character-centered evidence

Narrative Tension concerns a relationship shared by two parties, but it must
not make the User the author of the character's inner state. A User can report
hurt, offer forgiveness, reject an apology or request reconciliation. Those are
authoritative only as evidence of the User's stance. They cannot establish that
the character regrets a choice, retracts a boundary, wants renewed intimacy or
has completed Persona Growth.

Character stance comes from the character's own causal sources: Character
Blueprint, Formative Experience, accepted relationship history, formal Persona
Reflection and later Agent choices that pass the ordinary continuity path. A
User statement such as "you have forgiven me" is not evidence of forgiveness
by the character. A `relationship_reviewer` validates an evidence-bound event
candidate; the reviewer cannot write the character's emotion, choose a preferred
ending or directly assign Relationship State values.

Current tension status is a deterministic projection over append-only,
explicitly linked Relationship Events rather than a mutable `resolved` flag:

- `unaddressed`: the consequence exists without a later direct response;
- `addressed_unresolved`: an apology, explanation, refusal, boundary or repair
  attempt occurred, but the relational outcome is not established;
- `mutually_reconciled`: evidence from both parties supports renewed shared
  understanding or accepted repair;
- `boundary_stabilized`: an evidence-backed boundary remains in force and the
  current relationship projection no longer treats reconciliation as pending;
- `relationship_ended`: a valid relationship-ending choice closes future
  interaction expectations without erasing the prior harm;
- `superseded`: an explicit later agreement or arrangement replaces the open
  matter while retaining its source history.

An apology by the character proves a character action but not User acceptance.
User forgiveness proves the User's stance but not the character's wish to
resume the relationship. Mutual reconciliation requires evidence from both.
A character may maintain or end a relationship through an aligned choice even
when the User disagrees; character agency does not require User approval.

Closing or transforming a tension never deletes the initiating event, restores
pre-conflict numeric state, manufactures Persona Reflection, or declares the
harm unreal. Silence, elapsed time, one unrelated warm exchange and a reviewer's
preference cannot close it.

## Consequences

E.R.I.I. can remember User impact without becoming User-controlled. The User
retains authority over their own experience; the character retains authority
over its own evidence-backed stance. Reconciliation is genuinely relational,
while boundaries, estrangement and endings remain valid character outcomes.
ADR-0102 separates the two party stances from the resulting joint projection.
