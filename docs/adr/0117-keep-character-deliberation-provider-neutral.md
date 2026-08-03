---
status: accepted
---

# Keep character deliberation and model collaboration provider-neutral

E.R.I.I. may recommend or ship a separately installable DeepSeek Adapter because
the maintainer has found it useful for experiments and, as of 2026-08-03, its
public pricing is accessible to budget-sensitive users. DeepSeek is
nevertheless a Model Provider, not part of character identity, relationship
authority, or the kernel's durable data meaning. The core must remain usable
without DeepSeek, and hosts should not have to redesign an otherwise working
deployment merely to use that Provider.

Character Deliberation therefore exposes a Provider-neutral Interface. A
DeepSeek Adapter may use provider-specific thinking inside one call, but raw
reasoning, prompts, credentials, and provider error bodies do not cross the
Seam or become character history. Any later durable deliberation record is
created and validated by the kernel against the frozen Turn, exact delivered
reply, current relationship, and continuity result.

Future multi-model collaboration is a separate orchestration concern. A
Deliberation Ensemble has one Character Actor and zero or more Deliberation
Reviewers, and its participants may use DeepSeek, other remote Providers, local
models, or any mixture selected by the host. DeepSeek receives no privileged
role in that design, reviewers do not vote to define the character, and no
Provider may write persona, relationship, memory, or Turn state directly.

## Consequences

DeepSeek may be documented as an optional, budget-conscious reference choice,
with dated links to official pricing and privacy terms rather than a permanent
cost or quality promise. Provider packages are explicitly installed and
injected; the kernel does not install them, auto-discover untrusted code, or
require hot unloading. A public Provider Interface should be frozen only after
at least two real implementations demonstrate that the Seam represents actual
variation rather than a DeepSeek-shaped abstraction.
