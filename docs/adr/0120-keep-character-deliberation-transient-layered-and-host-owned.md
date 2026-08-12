---
status: accepted
---

# Keep Character Deliberation transient, layered, and host-owned until evidence earns durability

E.R.I.I. exists to preserve character continuity without reducing a character to
"pleasant means correct" or allowing a reply generator to rewrite Persona,
Relationship, Memory, or Turn authority.  A richer pre-reply process is useful
only if it preserves that boundary.  The accepted design language in
[`CONTEXT.md`](../../CONTEXT.md) therefore does not make Character Deliberation
an implemented capability: it defines the contract that an Experimental/Labs
implementation must prove before any durable or public surface is promoted.

## Decision

A Character Deliberation Result has three deliberately different layers:

1. a machine-verifiable **Deliberation Semantic Frame** containing sourced
   situation appraisals, competing psychological candidates, uncertainties,
   behavioral intent, disclosure choice and communication strategy;
2. a warm, character-native **Character Interior Scene** that may be literary,
   fragmented, sensory, self-deceiving or uncertain, but may not introduce
   factual premises outside the verified Frame; and
3. a proposed **Visible Reply Envelope**, containing the exact ordered message
   parts the host would show if the candidate passes the independent continuity
   and delivery gates.

The Frame is not a hidden psychological truth, the Interior Scene is not
Provider chain-of-thought, and neither has direct write authority.  Unknown,
conflict, abstention, incomplete self-understanding and later reinterpretation
are normal outcomes.  The final reply may conceal, contradict or only partly
express the Interior Scene when that divergence has sourced psychological
causality; honesty, gentleness and user satisfaction are not universal
continuity criteria.

The host or a separately removable Labs package owns Provider selection,
prompt construction, retries, budgets, timeouts and orchestration.  The kernel
owns the frozen Turn baseline, relationship-scoped evidence views, strict
validation, exact binding and the existing Continuity Review and Turn
completion gates.  Provider output cannot self-declare Relationship, Turn,
Persona, evidence permissions, admission or delivery.  Character Deliberation
success never proves continuity, and Continuity Review remains independent.

The default topology is **Compact-first, Staged-when-earned**.  A Compact Actor
normally returns the Frame, Interior Scene and reply candidate in one strict
result.  A versioned Adaptive Router may choose or escalate to a two-call
Staged path—Plan then Reply Realization—only for structural complexity such as
conflicting supported bases, knowledge or relationship-scope ambiguity, or an
important Persona/relationship transition.  Anger, refusal, sharp language,
user discomfort or lack of warmth are not escalation reasons.  A host may also
select `off`, `compact_every_turn`, `adaptive`, or `staged_every_turn`.

The first implementation keeps complete deliberation artifacts process-local.
After an exact reply is continuity-supported and actually shown, an admission
gate may retain only a minimal relationship-scoped **Session Pending
Deliberation Residue**.  The full Interior Scene is discarded.  Repetition and
recall do not renew or strengthen Residue.  A later independent **Private
Reflection Adjudication**, not the reply Actor, may accept a limited private
stance, leave it unformed, close it, or reject it as unsupported.  Core-Persona
or large changes still become proposals requiring the existing explicit
approval path.

Residue may become restart-durable only after controlled evaluation proves a
benefit and FileStorage, SQLite, MemoryPack, backup/restore, rebuild, erasure,
concurrency and old-reader behavior have matching contracts.  Durable Residue
remains provisional and relationship-scoped.  It does not become true merely
by surviving a restart.

User-visible **Thought Projection** and **Deliberation Explanation** are later,
separate renderings.  They are not raw Interior Scene dumps.  User and Operator
audiences use different schemas.  What the user actually sees belongs to an
Exposure Ledger, separate from the Source Transcript but joined in Experience
History; this requires explicit export, erasure, awareness and correction
semantics before shipping.

## Provider and Claude consequence

This decision refines, but does not replace,
[ADR-0117](0117-keep-character-deliberation-provider-neutral.md).  DeepSeek,
Claude, another remote model, or a local model may implement the Character
Actor or Reviewer role.  No Provider receives identity or history-write
authority.  A Claude Adapter is an optional host-injected integration and must
implement the same strict request/result contracts as every other Adapter.
Provider-specific reasoning or "thinking" features remain runtime material:
they are neither mapped to Character Interior Scene nor logged, exported or
used as durable character history.

A public Provider seam is frozen only after at least two real implementations
demonstrate genuine variation.  One Deliberation Ensemble has exactly one
Character Actor and zero or more Reviewers.  Reviewers return bounded findings;
they do not vote a character into existence and cannot revise or persist state.
Only the Actor may author a new immutable Revision after review.

## Exactness and lifecycle consequences

The proposed reply is bound to the final `1..N` ordered message parts, their
exact UTF-8 bytes and the frozen request/evidence commitments.  Host syntax such
as KouriChat's `$` separator is parsed before this binding.  Translation,
formatting, splitting, emoji insertion or any other byte-changing transform
after review invalidates the binding and requires a new review.

Attempt, Revision, delivery and Residue lifecycles remain separate.  A network
retry is an Attempt, not a character Revision.  Only the active immutable
Revision can advance.  Same-relationship generation is serial by default;
late Provider results are fenced by Turn state, record version, baseline
fingerprint and run epoch.  A legal reply already shown is not retracted if
optional Residue admission later fails; the result is explicitly
`delivered_without_residue`.

## Evaluation consequence

Promotion depends on blinded comparison against direct generation and an
equal-compute control, not anecdotes about one model's thinking.  The initial
matrix compares direct, Compact, Staged, Adaptive, equal-compute unstructured,
Compact plus Session Residue, and Adaptive plus Session Residue.  It measures
psychological causality, Persona and relationship scope, knowledge boundary,
character voice, agency, justified conflict/refusal retention, unsupported
appeasement, latency, cost and failure rates.  Cross-relationship leakage, raw
thinking leakage, stale-binding acceptance and late-result writes are zero-
tolerance gates.

This follows [ADR-0118](0118-prioritize-consequences-and-separate-experiments.md):
Labs evidence must precede new kernel migration obligations.  The detailed
implementation and promotion plan is recorded in
[`character-deliberation-development-plan.md`](../architecture/character-deliberation-development-plan.md).

## Non-goals

The accepted direction does not:

- claim access to a character's objectively true mind;
- turn Provider raw reasoning into a product feature;
- make every reply longer, warmer, more compliant, more apologetic or safer;
- let a User, Actor, Reviewer or majority vote define the character's intent;
- replace Continuity Review or allow deliberation to self-approve delivery;
- persist complete prompts, drafts, Interior Scenes or Provider error bodies;
- add REST, TypeScript, SQLite, FileStorage or MemoryPack fields in the first
  Labs milestone;
- require Claude, DeepSeek, a remote service, multiple models or background
  threads;
- promise that the accepted vocabulary is present in the current source
  milestone.

## Reversibility

The first Labs implementation must be removable without a storage migration.
Disabling it restores direct generation plus the existing Continuity Review,
marks the path `not_deliberated`, discards Session Residue and leaves existing
Turn, Recall, Persona, Relationship, MemoryPack and lifecycle behavior intact.
Later durable stages require their own ADR and a verified downgrade/export/
erasure story before this reversibility boundary may change.
