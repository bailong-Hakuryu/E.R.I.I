---
status: accepted
---

# Separate User and Persona stances from relationship outcomes

A single relationship status cannot faithfully represent what each party wants
or believes. If User forgiveness directly sets the relationship to repaired,
the User silently authors the character's response. If a private character
reflection immediately marks the relationship repaired, the system pretends
that an unexpressed intention has already affected the User. E.R.I.I. therefore
separates party evidence before deriving a joint outcome.

`UserStanceProjection` is derived only from the User's visible statements and
accepted actions concerning a specific Relationship Event or Narrative Tension.
It can represent expressed hurt, forgiveness, refusal, desire to continue,
desire to end or absence of an expressed stance. It is not a claim to know the
User's hidden psychology and carries no authority over the character.

`PersonaStanceProjection` is derived only from formal Persona Reflection,
continuity-reviewed Agent choices and the accepted events created from those
choices. It can represent a desire to repair, a maintained boundary, a desire
to leave, unresolved conflict or an explicit unformed stance. User instructions and User
claims about what the character supposedly feels cannot populate it. When
character-side evidence is absent, the correct state is unknown or unformed.

Private reflection and visible action remain distinct. A Persona Reflection may
establish that the character privately wants to repair, but it does not prove
that an apology or repair attempt reached the User. Only a later delivered,
reviewed Agent action can create that shared relationship evidence.

`RelationshipOutcomeProjection` is computed from both party projections and
the linked accepted Relationship Events. Mutual reconciliation requires
evidence from both parties. A boundary or relationship ending may remain valid
without mutual agreement when the acting party has the relevant agency, but it
does not overwrite the other party's recorded hurt or disagreement. No reviewer
or caller writes the joint outcome directly.

These are rebuildable projections over append-only sources rather than mutable
truth rows. Updating one party's stance adds new evidence and recomputes the
current view; it never deletes an earlier stance, rewrites Persona Reflection or
changes what the other party expressed.

## Consequences

Prompts and front ends can explain whether a tension is blocked by User stance,
Persona stance or lack of mutual evidence without exposing private reflection
as public speech. The relationship remains genuinely shared while the
character's interiority cannot be commandeered by User preference.
