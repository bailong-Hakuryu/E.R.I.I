# E.R.I.I. 0.4.0rc1 Source-Closure Development Notes

Source identity: `0.4.0rc1.dev0`
Status: source-closure development snapshot
Distribution status: no standalone RC package or GitHub Release planned

This document tracks work in progress on top of the accepted `0.4.0b1` source
baseline at commit
`f6dca322379c4ea88320c69d752cab471d035e95`. It is not an immutable release
note and must be updated until the rc1 source checkpoint is accepted.

## Purpose

rc1 makes the v0.4 kernel easier to install, understand, integrate, and verify
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

The name `rc1` preserves the existing source-version sequence. It does not mean
that a Release Candidate wheel, sdist, GitHub Release, or package-registry
artifact will be published. Formal distribution remains planned for `1.0`.

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

rc1 keeps the accepted b1 data identities:

| Axis | rc1 development value |
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

Platform statements remain evidence-scoped. Current workflows run the full
declared Python matrix on Linux and named storage/build/Demo smoke paths on
Windows. This document does not claim validation for an operating-system and
interpreter combination that the committed workflow does not run.

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

## Verification before acceptance

The rc1 checkpoint is not accepted merely because this note exists. Acceptance
requires the committed source to pass its actual Python/OS matrix, Ruff,
`compileall`, public tests, document contracts, relative-link checks, frozen
contract checks, local wheel/sdist build, clean installation, Golden Demo,
reference-host smoke, and the relevant longitudinal regression suite.

Exact commands and observed results belong in the final source-checkpoint
handoff. Until then, this file describes a development target rather than a
published capability claim.
