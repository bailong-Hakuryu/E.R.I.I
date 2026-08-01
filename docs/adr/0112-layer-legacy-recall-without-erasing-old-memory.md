---
status: accepted
---

# Layer Legacy recall without erasing old memory

Removing every `legacy_unavailable` Timeline or Memory artifact from default
recall would make an upgrade feel like sudden amnesia for existing users.
Treating every such artifact as ordinary modern authority would preserve short-
term fluency but allow old unsupported Agent output to reinforce itself and
shape continuity, persona or relationship decisions without message-role
evidence.

a8 therefore assigns a projection-level Recall Authority Tier independently of
Memory Type and semantic relevance. `ordinary` requires complete modern
message-level evidence and an eligible delivery path. `legacy_context` applies
to pre-a8 or schema `"1"` artifacts whose message role or review status cannot
be recovered and for which no modern exceptional source is provable. They
remain available in a separately labelled Agent-private compatibility section
so a character can continue recalling old topics and shared experiences, but
they cannot become Continuity Basis, Persona Reflection, Persona Growth,
relationship-stage authority or core-persona change. They are excluded from
Recall Reinforcement, so repetition cannot increase uncertain authority.

`quarantined_history` applies when an evidence-unavailable artifact can be
resolved to a modern `overridden` or `shown_unreviewed` Turn and cannot prove
that it depended only on eligible User evidence. It is excluded from default
generation recall. The artifact and Source Transcript remain available to
inspection, front-end labelling, export and deletion; quarantine is not data
loss or a claim that the remembered interaction never occurred.

Modern complete evidence takes priority in authority-sensitive use and is
rendered separately from Legacy Context. A conflict is never resolved by
silently rewriting or deleting the older artifact, and content similarity or a
plausible summary cannot promote Legacy data. Front ends may label the tiers as
“旧版记忆／来源未完整验证” and “异常交付历史／默认不参与生成” without
calling either one false.

## Consequences

Existing relationships retain conversational continuity after upgrade while
known exceptional output cannot become a self-reinforcing prompt influence.
Recall results and tests must expose the authority tier, keep Legacy data out of
reinforcement and authority-sensitive pipelines, and preserve inspection,
portability and deletion for every tier. ADR 0114 defines the default bounded
selection and rendering policy for `legacy_context`.
