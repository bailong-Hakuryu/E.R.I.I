# E.R.I.I. 0.4.0 Stable Source Milestone Notes

Source identity: `0.4.0`
Status: stable source milestone
Distribution status: no standalone package or GitHub Release
Milestone date: 2026-08-04

`0.4.0` accepts the v0.4 character-continuity and long-term-memory kernel after
the feature-complete b1 baseline and the rc1 source-closure checkpoint. The rc1
evidence commit is
`58ea8e69df28bec8e755e0a0d2a175679c18a694`; this final source identity changes
no durable character-domain meaning on top of it.

“Stable” here describes the v0.4 **source line**. Under
[ADR-0119](adr/0119-defer-formal-package-distribution-until-v1.md), it does not
create a Git tag, GitHub Release, PyPI publication, uploaded wheel/sdist, or
commercial SLA. Reproducible deployments must pin the reviewed full commit SHA
that contains this version. Formal package distribution remains planned for
`1.0`.

## What v0.4 establishes

- immutable Character Blueprint source plus approved, source-anchored Persona
  interpretation;
- independent `Agent × User` relationship, persona, memory, event, state, and
  intimacy histories while the same `agent_id` may retain one shared character
  identity;
- exact visible Source Turns, explicit delivery state, reliable archival,
  relationship processing, Continuity Review, and provenance-aware Recall;
- ordinary relationship evolution, Persona Reflection, approval-gated Persona
  Growth, Promises, Open Loops, Episodes, and Relationship Chapters;
- FileStorage, SQLiteStorage, MemoryPack, strict compatibility inspection,
  verified backup/restore, narrow upgrades, fresh import, erasure, rebuild, and
  deterministic long-horizon regression;
- a documented Golden Path, real-chat host flow, support boundary, and
  source-only verification workflow.

## Golden Continuity Demo

Run:

```bash
erii demo --output-dir ./erii-demo
```

The no-network Demo uses an original synthetic character, deterministic
host-side extractors, and real SQLite. It verifies:

1. User A's first-snow Turn, memory, event, relationship state, and selected
   Persona graph survive an Engine restart.
2. User B shares the character identity but receives none of User A's event,
   memory, intimacy, state reason, or canonical-relationship Persona graph.
3. Recalled artifacts retain their exact Source Turn and archival provenance.
4. User A exports to `user-a.erii`; the Pack is atomically imported into a
   fresh SQLite database, reopened, and checked for the same relationship,
   Persona, Turn, memory, event, state, and compact archival commitments. User
   B is absent from the imported store. Because MemoryPack carries a
   content-free Archival Tombstone rather than the full operational receipt,
   imported Recall keeps ordinary generation authority but honestly reports
   `partial_source`; the Demo verifies that intentional retention boundary.

`demo-report.json` is an inspectable Demo artifact, not a frozen host API.

## Final compatibility repairs

The final source closes two upgrade-only defects found by the fresh-import
proof:

- approved Persona Compilation Proposal `decision_reason` values now survive
  new MemoryPack imports;
- a target written by the older importer may already have lost that reason.
  A retry recognizes only the one-way `existing None → incoming non-empty`
  historical loss and preserves the stored `None`; it does not rewrite audit
  history. The reverse direction and two different non-empty reasons still
  conflict;
- SQLite imports remove their private staging relationship-lock directory.
  A retry of an older completed Plan may clean an orphan only when it has the
  exact runtime-lock shape; links, unexpected names, and other data fail
  closed.

These repairs do not change any persistent format version.

## Independent compatibility axes

| Axis | v0.4.0 value |
| --- | --- |
| Python source identity | `0.4.0` |
| Python | `3.11`–`3.14` within the committed workflow evidence |
| SQLite schema | `9` |
| FileStorage format | `1` |
| MemoryPack | `0.4.0a8` |
| Lifecycle Backup | `1` |
| Lifecycle Plan | writer `3`, readers `1`–`3` |

Moving from b1 or rc1 source to `0.4.0` does not require a persistent-data
migration. MemoryPack `0.4.0a8` remains the current portable-data format; the
Python package version does not replace that independent compatibility axis.
The final `v0.4.0` Python API, OpenAPI, data-format, and SQLite
snapshots are retained beside the b1 and rc1 snapshots and must match rc1
except for their source-release identity fields.

Historical data still follows the explicit rules in
[Compatibility Policy](compatibility.md) and
[Data Lifecycle](data-lifecycle.md). “Readable,” “backup,” “restore,”
“upgrade,” and “fresh import” remain different operations.

## Explicit non-goals

`0.4.0` does not implement v0.5 Relationship Consequence, Narrative Tension,
automatic memory of harm, repair, refusal of repair, relationship ending, or
Character Review.

It does not persist DeepSeek thinking, raw chain-of-thought, full prompts,
discarded drafts, credentials, or Provider error bodies. It also does not add
per-user authentication, object authorization, encryption, TLS, rate limiting,
tenant isolation, or a product SLA. Those remain Adapter/Labs, host-product,
and later-roadmap responsibilities.

## Verification boundary

The committed CI verifies Python 3.11–3.14 on Linux, targeted Windows storage
and lifecycle paths, source contracts, unit tests, compilation, wheel/sdist
builds, clean artifact installation, the Golden Demo, MemoryPack parsing, and a
reference-host smoke path. The exact-SHA source-milestone workflow additionally
requires the expected source version and has read-only repository permissions.

The fixed longitudinal scenarios run against FileStorage and SQLite and must
retain zero hard failures and zero cross-relationship leakage. Platform,
performance, Provider quality, and product-security claims do not extend
beyond the evidence explicitly named here.

See [Getting Started](getting-started.md), [Host Integration](host-integration.md),
[API Stability](api-stability.md), and the historical
[rc1 checkpoint notes](release-notes-0.4.0rc1.md).
