---
status: accepted
---

# Freeze persona and relationship context at Turn Opening

`begin_turn()` freezes a `TurnContextBaseline` containing the Character
Blueprint revision and source hash, active Persona Manifest identity and
content fingerprint, the approved Persona Growth prefix, the bound Relationship
Premise, direct-event and adjudication journal high-water marks, exact prefix
fingerprints, and the policy versions used by current-turn derivations. Every
generation or evaluation attempt for that open Turn reads the same baseline;
ordinary persona approvals and relationship events committed after Turn
Opening first become visible to the next Turn.

An open Turn loaded from a format that predates `TurnContextBaseline` remains
readable but cannot acquire one retroactively. Reading current state during
upgrade would invent what was visible at its original opening. Such a Turn may
be abandoned or completed only as an explicit unreviewed delivery; successful
a8 review requires a Turn whose baseline was frozen at its actual opening.

## Consequences

Retries cannot silently change contextual voice or continuity judgment because
an asynchronous relationship update, Manifest approval or Persona Growth
approval finished between attempts. The baseline stores constant-size
boundaries, identities, fingerprints and versions rather than copying history,
while MemoryPack must preserve enough ordered authority to reconstruct and
verify the same prefixes. A host that needs pending prior processing reflected
immediately must complete it explicitly before `begin_turn()`; the kernel does
not hide a wait inside Turn Opening.

If no approved Manifest is active at Turn Opening, that Turn cannot later claim
a successful pre-delivery continuity review using a newly approved Manifest. It
may complete only as an explicit unreviewed delivery or be abandoned before a
new Turn is opened.

An explicit revocation is a safety invalidation rather than an ordinary context
update and therefore pierces the frozen baseline. Before committing a
successful receipt, `complete_turn()` rechecks that the bound Manifest and every
approved Persona Growth authority remain approved. Revocation rejects the
stale Result instead of silently switching authority inside the Turn. If the
reply was already displayed during the race, the host still records the real
Source Transcript as a failed unreviewed delivery with a sanitized revocation
reason; it cannot retain the former successful receipt or use the reply to
authorize persona or relationship change.

The authority recheck and terminal Turn CAS must share one storage
linearization boundary. FileStorage holds the cross-process Turn Context root
lock until the Turn revision is installed; SQLiteStorage performs the recheck
and update in one write transaction. If revocation commits first, reviewed
completion fails and leaves the Turn open. If reviewed completion commits
first, revocation may still apply to future Turns but cannot rewrite the
already-sealed historical receipt. Retrying that exact completed payload
remains idempotent after later revocation.
