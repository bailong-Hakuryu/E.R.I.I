---
status: accepted
---

# Persist minimal non-replayable voice activation traces

A runtime `VoicePatternActivation` remains an attested, current-process
permission that expires with its Turn and is never portable. When an exact
activation is needed to explain the final continuity judgment,
`complete_turn()` may project it one way into a nested `VoiceActivationTrace`
inside the Continuity Review Receipt. The Trace has no runtime attestation and
no Interface may deserialize or otherwise convert it back into an activation.

Each condition match preserves its approved condition ID, source class, signal
type, exact categorical value from the bound Manifest's condition vocabulary,
producer version and kernel-resolvable evidence references. It does not retain
free-text emotion or situation analysis, Prompt content, evaluator reasoning or
unbounded host descriptions. Exact bounded values are retained instead of only
hashes so the data owner can understand the historical judgment and the kernel
can verify that the approved condition really matched.

Only an activation explicitly cited by a final Continuity Finding is projected
into a Trace. Every stored Trace must be referenced by at least one Finding and
every activation reference in a Finding must resolve to exactly one Trace.
Activated patterns that the final judgment did not use remain temporary and
are discarded; when several patterns genuinely support the reply, their
referenced Traces are stored in deterministic order.

A Trace may be cited only by a `voice_style` Finding using the contextual-voice
reason code. It can establish that a surface register was available in that
moment, but cannot support `identity_values`, `psychological_causality`,
`relationship_scope` or `knowledge_memory_scope`. Those axes may separately
cite the Trace's underlying authoritative records when their own evidence rules
permit it; the activation itself never launders a behavioral, relational or
knowledge conflict into an acceptable result.

Signal authority remains explicit in historical verification. A
`host_observed` match is checked against the exact structured observation saved
with the open Turn; it proves what the host recorded, not an independently
verified world fact. A `core_derived` match freezes the authoritative
relationship-history prefix, projection policy version and output so the
deterministic kernel rule can be replayed without seeing later events. An
`evaluator_inferred` match preserves its bounded output, evaluator descriptor,
input fingerprint and resolvable evidence, but is never re-sampled with a
current model; it records what that versioned evaluator inferred then rather
than declaring an objective or permanent emotion.

Core-derived matches use the Turn Context Baseline frozen by ADR-0095. They do
not read a newer relationship projection on evaluation retry, and an event
committed after Turn Opening can affect only a later Turn.

The Trace is audit metadata owned by its parent Turn. It is excluded from
memory recall, relationship projection, persona compilation and growth, and is
deleted or exported only with that Turn. Its canonical fingerprint establishes
internal consistency, not cryptographic authenticity.

Trace projection is observational and must not affect interaction behavior.
For the same frozen Turn baseline, reply and evaluator result, enabling or
omitting Trace serialization produces the same Findings, aggregate verdict,
delivery decision and future recall inputs. A Trace is never injected into a
generation or evaluation Prompt. It may be used only in offline diagnostics to
measure false or missed contextual-voice activations and improve a later,
separately versioned matcher or pattern approval.
