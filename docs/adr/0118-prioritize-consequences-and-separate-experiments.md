---
status: accepted
---

# Prioritize observable consequences and separate model experiments

ADR-0106 correctly froze a8 and moved character-consequence semantics to v0.5,
but its proposed `0.5.0a1` bundled exception resolution, party stances,
Narrative Tension, inner review, sensitivity policies and several later
portability structures into one release. That scope is too broad for a
solo-maintained kernel and would make it difficult to prove which new semantic
actually improves a character's long-term continuity.

`0.5.0a1` therefore starts with one observable vertical slice: an exact,
delivered and continuity-supported character choice may append a
relationship-scoped consequence and Narrative Tension; later sourced events
project whether it remains unaddressed, is addressed without resolution,
reaches mutual reconciliation, stabilizes a boundary, ends the relationship,
or is superseded. The original Turn, continuity result and consequence remain
append-only. Harm does not imply OOC, and no outcome forces apology,
forgiveness or reconciliation.

Kernel evolution owns durable character, relationship, portability and
lifecycle semantics, whether or not an intermediate `0.x` source milestone is
distributed as a package. Provider experiments, DeepSeek thinking, host
integrations and multi-model orchestration remain in a separately removable
Labs & Integrations track until controlled behavioral evaluation proves a
stable benefit and a provider-neutral durable meaning. This narrows ADR-0106's
original a1 scope without reversing its a8 freeze or its accepted consequence
principles. It also supersedes only ADR-0111's scheduling of historical
exception reprocessing as the first `0.5.0a1` deliverable. ADR-0111's a8
quarantine, append-only history, frozen-candidate and explicit reprocessing
rules remain in force; their public resolution Interface moves to a later v0.5
Alpha.

## Consequences

Later v0.5 Alphas may add Character Review, party stances, retrospective
exception resolution and Character Deliberation only after the consequence
slice is portable, erasable, rebuildable and behaviorally validated. Model
experiments do not delay core evolution, acquire direct history-write
authority, or create migration obligations merely because one Provider
performs well.
