# E.R.I.I. 0.4.0rc1 Accepted Source-Closure Checkpoint

Source identity: `0.4.0rc1.dev0`
Status: accepted source-closure checkpoint
Accepted commit: `58ea8e69df28bec8e755e0a0d2a175679c18a694`
Distribution status: no standalone RC package or GitHub Release was published

This document records the completed source-closure work on top of the accepted
`0.4.0b1` source baseline at commit
`f6dca322379c4ea88320c69d752cab471d035e95`. The checkpoint was accepted at the
commit above and is retained as historical evidence for the final `0.4.0`
source milestone.

## Purpose

rc1 made the v0.4 kernel easier to install, understand, integrate, and verify
without changing its durable character-domain meaning. The checkpoint focuses
on source closure:

- one-command proof of restart persistence, relationship isolation,
  provenance, and MemoryPack export;
- a short first-adoption path and one canonical real-chat integration flow;
- explicit `Golden Path | Advanced | Experimental | Internal` API levels;
- source installation, local build, clean-install, CLI, reference-host, and
  contract verification;
- documentation links, contribution templates, compatibility wording, and
  support-boundary audits;
- correctness, recovery, compatibility, and performance defects found while
  closing the source line.

The name `rc1` preserved the existing source-version sequence. No Release
Candidate wheel, sdist, GitHub Release, or package-registry artifact was
published. Formal distribution remains planned for `1.0`.

## Golden Continuity Demo

The implemented public command is:

```bash
erii demo --output-dir ./erii-demo
```

It uses an original synthetic character, deterministic host-side extractors,
and real SQLite. It closes and reopens the Engine before checking User A's
first-snow recall, verifies that User B has no event/memory/state inheritance
and cannot receive User A's canonical-relationship Persona graph, resolves
recalled artifacts to their Source Turn and archival identity, and exports
User A as `user-a.erii`.

The destination must not already exist. The command does not remove or replace
an earlier run.

## Compatibility boundary

rc1 kept the accepted b1 data identities:

| Axis | accepted rc1 value |
| --- | --- |
| Python source identity | `0.4.0rc1.dev0` |
| Python | `3.11`–`3.14` |
| SQLite schema | `9` |
| FileStorage format | `1` |
| MemoryPack | `0.4.0a8` |
| Lifecycle Backup | `1` |
| Lifecycle Plan | writer `3`, readers `1`–`3` |

Only the source identity changes. A documentation or Demo change cannot
silently advance an authority rule, storage schema, MemoryPack wire, Backup,
or Lifecycle Plan.

Platform statements remain evidence-scoped. The accepted rc1 workflow ran the
full declared Python matrix on Linux and named storage/build/Demo smoke paths
on Windows. This document does not claim validation for an operating-system
and interpreter combination that the committed workflow did not run.

## Explicit non-goals

This snapshot **does not implement v0.5 Relationship Consequence**, Narrative
Tension, automatic memory of harm, repair, refusal of repair, relationship
ending, or Character Review.

It **does not persist DeepSeek**, raw thinking, full prompts, discarded drafts,
provider credentials, provider error bodies, or a Character Deliberation
record. DeepSeek and multi-model work remain optional Labs/Adapter experiments
with no durable authority in rc1.

rc1 also does not add authentication, per-user object authorization,
encryption, tenant isolation, TLS, rate limiting, or a product SLA. Those
remain host/product responsibilities and later roadmap work.

## Acceptance evidence

The accepted commit passed the committed Python 3.11–3.14 Linux CI matrix,
targeted Windows paths, Ruff, `compileall`, public tests, document contracts,
relative-link checks, frozen contract checks, wheel/sdist build, clean
installation, Golden Demo, and reference-host smoke. The reviewed local
acceptance run also exercised the fixed FileStorage/SQLite longitudinal suite.

This evidence accepts a source checkpoint, not a published package or
production-security boundary. The final stable source identity and its added
export/import Demo round trip are described in
[0.4.0 notes](release-notes-0.4.0.md).
