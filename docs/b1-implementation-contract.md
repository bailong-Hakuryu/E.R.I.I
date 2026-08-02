# v0.4.0b1 Implementation Contract

Status (2026-08-03): implemented and awaiting the final `0.4.0b1` acceptance
gate. This document describes candidate behavior, not a proposal, but b1 is not
an immutable release until its tag, artifacts and release checks exist. The
latest immutable release remains `0.4.0a8`. Historical alpha contracts and ADRs
remain immutable records of the decisions that led here.

## Release boundary

`0.4.0b1` is the v0.4 feature-complete Beta candidate. After this point v0.4 accepts
compatibility, migration, performance, documentation and defect fixes; it does
not add another character-domain model. The next planned domain work—lasting
consequences of a character's choices and character-centred inner review—remains
in v0.5.

The version axes are deliberately independent:

| Axis | b1 value | Meaning |
| --- | --- | --- |
| Package | `0.4.0b1` candidate | intended Python distribution and Git tag; not immutable until published |
| Python | `3.11`–`3.14` | supported interpreter range |
| SQLite schema | `9` | physical SQLite layout |
| FileStorage format | `1` | `.erii-store.json` identity; legacy remains readable for lifecycle upgrade |
| MemoryPack format | `0.4.0a8` | portable relationship wire format |
| Lifecycle Backup | `1` | verified backup bundle format |
| Lifecycle Plan | writer `3`, readers `1`–`3` | immutable operation plan contract |

A package release does not silently advance the other axes. In particular,
publishing b1 does not rename an a8 MemoryPack or rewrite schema 9 as “b1”.

## Public module boundary

All lifecycle mutations use one deep-module interface:

```python
assessment = lifecycle.inspect(target)  # read-only
plan = lifecycle.plan(request)          # zero-write dry-run
report = lifecycle.execute(plan)        # mutate, then terminally verify
```

`DataLifecycleCoordinator` owns orchestration. `LifecycleRequest` is a closed
typed union of `BackupRequest`, `RestoreRequest`, `UpgradeRequest`,
`MemoryPackImportRequest`, `EraseRequest` and `RebuildRequest`. Plans are strict,
canonical JSON credentials: they bind the observed source, destination parent,
strategy, optional backup target and selector. Reports contain identities,
counts, digests and disposition groups, never conversation, persona, event or
memory bodies.

Plan v3 is the current writer because erase/rebuild and import need a durable
selector. The reader preserves exact v1 backup/restore and v2
backup/restore/upgrade rules and digests. Older contracts cannot claim newer
operations or carry v3 selectors.

## Delivered operation matrix

| Operation | Source | Destination/result | Backup-first | Publication rule |
| --- | --- | --- | --- | --- |
| inspect | FileStorage, SQLite, MemoryPack, Backup | no-content assessment | no | no write |
| backup | FileStorage, SQLite, MemoryPack | Lifecycle Backup v1 | n/a | missing target, no-replace |
| restore | Lifecycle Backup v1 | original live format | n/a | missing target, no-replace |
| upgrade | FileStorage `legacy` | FileStorage v1 sibling | yes | source-preserving, missing target |
| upgrade | SQLite schema 6 | SQLite schema 9 sibling | yes | source-preserving, missing target |
| upgrade | every declared older readable MemoryPack | MemoryPack `0.4.0a8` sibling | yes | source-preserving, missing target |
| import | current or declared readable MemoryPack | fresh FileStorage v1 or SQLite v9 | no existing target to preserve | isolated staging, then no-replace |
| erase | current FileStorage v1 or SQLite v9 | protected replacement of the same live target | yes | verified staged transform and recoverable cutover |
| rebuild | current FileStorage v1 or SQLite v9 | rebuilt projections for one relationship | yes | verified staged transform and recoverable cutover |

Upgrade is intentionally side-by-side. b1 does not expose arbitrary in-place
schema migration, overwrite restore, merge import into an online storage, or a
generic downgrade. Rollback means restoring the verified pre-change backup to a
missing location and performing an explicit host cutover.

## Upgrade and import semantics

- FileStorage `legacy → 1` retains all logical source files and adds the
  canonical format manifest.
- SQLite `6 → 9` is the only verified SQLite lifecycle upgrade route. It runs an
  explicit historical transformation and validates the resulting semantic
  database identity. Merely opening any old SQLite database no longer counts as
  a supported upgrade workflow: old schemas fail closed, and schemas other than
  6 must not be represented as upgradeable merely because inspection can
  identify them.
- Every older MemoryPack version listed by `MEMORY_PACK_FORMAT.readable_versions`
  has an explicit route to `0.4.0a8`. The transformation validates the complete
  semantic graph and does not invent missing modern evidence, review authority
  or relationship facts.
- Fresh MemoryPack import performs production import validation inside an
  isolated missing FileStorage or SQLite target, verifies the semantic result,
  and publishes only after success. Exact retries are idempotent. This is not an
  atomic merge into a live destination.
- Optional Agent/User remapping remains subject to MemoryPack's own authority
  rules. A pack bound by Source Turns, relationship history or other identity
  evidence cannot be remapped merely because IDs were supplied.

Historical fixtures are synthetic and contain no user or copyrighted character
data. They retain producer version/commit metadata and checksums. Tests cover
Unicode, time zones, stable identities, source closure and failure recovery.

## Erasure and deterministic rebuild

`EraseRequest` supports exactly four selectors:

1. complete relationship (`agent_id + user_id + relationship_id`);
2. one Source Turn in that relationship;
3. one Relationship Event in that relationship;
4. complete user (`user_id + user_identity_id`) across matching local
   relationships.

Selectors are strict: ambiguous, missing or cross-boundary identities fail
during planning. The operation first publishes and verifies a Lifecycle Backup,
transforms an isolated staging copy, rebuilds affected projections from the
remaining authoritative history with the production projector/consolidator and
temporal validator, verifies the staged store, and only then performs the
protected replacement. For every surviving affected relationship, verification
also requires a production MemoryPack export followed by import into a fresh
store of the same adapter. Physical readability alone is not publication proof.

Source Turn and Relationship Event erasure follows the frozen-journal authority
graph rather than deleting one row in isolation. A processing run is revoked
when its direct-event or adjudication prefix included removed authority; its
decisions, events, reflections, growth records, archival artifacts and
transitively dependent later runs are removed as well. Source transcripts not
selected for erasure remain. If a surviving modern Turn's
`TurnContextBaseline` included the removed prefix, it is converted to an
explicit legacy `turn-record/v1` record without continuity-assessment authority.
This preserves what was said while refusing to invent a historical re-review.
Re-deriving later long-term memory requires an explicit future historical
reprocessing operation; erasure does not resample a model.

`RebuildRequest` currently accepts a relationship selector only. It does not
delete authoritative events; it recomputes Current Belief, relationship state,
state reasons, Episodes and Chapters from the authoritative relationship
history. The report includes content-free rebuild digests and counts.

An erasure report distinguishes `deleted`, `rebuilt`, `delegated` and
`unverified_external` inventory. The lifecycle backup itself still contains the
pre-erasure data, and configured vector stores, uploaded MemoryPacks, copied
databases, logs, remote model providers and other external copies are not
silently deleted. The host must apply its retention policy to those locations.
Consequently, success means “the selected data was removed from this verified
live store and its local projections were rebuilt,” not “every copy everywhere
has been cryptographically erased.”

## I/O, resource and failure contract

- `inspect()` and `plan()` do not create targets, manifests, SQLite sidecars or
  storage objects.
- File/tree hashing and byte-preserving copying use bounded chunks (at most
  1 MiB per stream chunk). SQLite identity is computed by streaming canonical
  rows rather than loading the database file as one byte string.
- A MemoryPack handled by the lifecycle module is limited to 256 MiB. A
  transformation that must materialize semantic JSON is limited to 512 MiB.
  A backup manifest is limited to 16 MiB. These are rejection boundaries, not
  recommendations for ordinary deployments.
- Source paths must be quiescent. Cross-process locks coordinate cooperating
  E.R.I.I. hosts; they do not defend a directory writable by an adversarial
  process.
- Links, reparse points, hard links, non-regular files, unstable SQLite
  WAL/journal state and incomplete temporary files fail closed.
- Destination names are no-replace. A stale source or changed parent identity
  aborts execution. An exact plan retry returns `already_complete` only when all
  expected artifacts match.
- A verified pre-change backup is retained if later transformation or
  publication fails. If a newly visible target cannot pass final verification,
  the coordinator preserves it for manual inspection rather than risk deleting
  writes made after publication.
- POSIX directory synchronization failures fail closed. Python cannot provide
  an equivalent portable directory-handle flush on Windows; content flushing
  and no-replace still apply, but equal power-loss durability is not claimed.

## Longitudinal and performance evidence

The repository contains three original synthetic trajectories—128 ordinary
single-relationship turns, two interleaved 72-turn relationships, and a
120-turn correction/conflict/growth trajectory—run against both FileStorage and
SQLite. The six full scenario/adapter reports cover restarts, retries,
File↔SQLite portability, duplicate import, positive/negative structured recall,
relationship isolation, provenance, corrections and deterministic lifecycle
operations. The checked baseline reports zero failed hard metrics.

The frozen JSON baseline is
[`benchmarks/baselines/v0.4.0b1-longitudinal.json`](../benchmarks/baselines/v0.4.0b1-longitudinal.json)
(46,143 bytes). On the maintainer's recorded run, full-scale erase/rebuild
observations were approximately 0.60–1.87 seconds with about 2.05–4.42 MiB peak
traced Python memory. These numbers describe one machine and dataset; they are
regression observations, not an SLA or a general capacity promise. Run
`python benchmarks/run_longitudinal.py --adapter both --scenario all` on the
deployment hardware before choosing budgets.

## Security and non-goals

Lifecycle hashes detect corruption and plan drift; they are not signatures,
MACs or provenance authentication. Storage, packs and backups are plaintext.
The module assumes a trusted, quiescent host and trusted local parent
directories. It does not provide user authentication, object authorization,
encryption, tenant isolation, adversarial same-host filesystem safety, TLS,
rate limiting or deletion of unregistered external copies. Product deployment
must add those controls outside the kernel; the planned complete boundary is
tracked for v0.6.

## Release evidence and change control

The b1 acceptance gate includes complete tests on the minimum and maximum supported Python
versions, Ruff, `compileall`, wheel/sdist builds, clean artifact installation,
contract snapshots, historical fixtures, lifecycle fault injection and the full
longitudinal suite. Exact counts can change when a regression test is added, so
the release claim is “all committed gates pass,” not a hand-maintained test
count.

Public Python exports, REST OpenAPI, compatibility catalog and storage/wire
snapshots are frozen for the v0.4 Beta line. A change that alters an authority
rule, public field, schema or wire identity must update its own version and
migration path; it cannot be hidden inside a documentation or patch-only edit.

Operational examples and recovery guidance are in
[`data-lifecycle.md`](data-lifecycle.md). Security boundaries are in
[`../SECURITY.md`](../SECURITY.md), and format-level support is in
[`compatibility.md`](compatibility.md).
