# 0.4.0b1 executable implementation contract

This document turns the `0.4.0b1` section of `ROADMAP.md` into an ordered
engineering contract. It does not declare Beta complete. The package remains a
development build until every exit gate in this document and the roadmap passes.

## Release and format identities

The version axes are independent:

| Axis | Development value at Beta start | Rule |
| --- | --- | --- |
| Package Version | `0.4.0b1.dev0` | becomes `0.4.0b1` only on the release commit |
| Python support | `3.11` through current stable `3.14` | minimum and latest stable are Required CI |
| SQLite Schema Version | `9` | changes only when the physical schema changes |
| MemoryPack Format Version | `0.4.0a8` | changes only when its wire format or reader contract changes |
| Lifecycle Backup Format | `1` | changes only when the verified backup bundle contract changes |
| Extractor, evaluator and policy versions | their existing feature-local values | never follow the package version implicitly |

No global search-and-replace may advance these values together. The immutable
`v0.4.0a8` tag, release artifacts and documentation remain unchanged.

## Current implementation status

The current development tree has completed B1.0 and the B1.1 fail-closed
storage slice. The read-only part of B1.2 is also implemented: an independent
compatibility catalog and `LifecycleInspector` identify FileStorage, SQLite and
MemoryPack sources without opening a Storage driver or writing a manifest,
future SQLite schemas and undeclared MemoryPack formats fail explicitly, and
MemoryPack envelope validation precedes nested model construction. The first
B1.3 slice is also complete: `DataLifecycleCoordinator` now creates and
inspects versioned backup bundles for FileStorage, SQLite and MemoryPack,
serializes immutable plans, rejects stale sources, restores byte-preserving
payloads to missing targets and makes both operations idempotent across a
process restart.

B1.2's final auto-migration cutover is deliberately conditional on B1.3. Until
an explicit upgrade transformation exists, opening an older supported SQLite
database still uses the historical in-place migration behavior. FileStorage v1
is currently a readable target manifest identity; backup and restore preserve
the detected source format byte-for-byte and do not publish that manifest.
Upgrade, overwrite restore, semantic migration validation, atomic MemoryPack
import, B1.4 deletion/rebuild and B1.5-B1.6 gates remain unimplemented.

## Scope and non-goals

Beta completes the v0.4 data lifecycle:

- fail-closed local storage reads and atomic legacy JSON writes;
- read-only format inspection and explicit future-version rejection;
- verifiable backup, dry-run, migration, validation and restoration;
- scoped deletion and deterministic rebuilding of derived projections;
- fixed longitudinal evaluations and measured performance baselines;
- freeze of the public Python Interface, REST `/api/v1`, SQLite Schema and
  MemoryPack Format.

Beta does not add relationship-consequence, continuity-exception resolution,
User/Persona stance, narrative-tension or character inner-review semantics.
Those remain `0.5.0a1` work. Authentication, authorization, encryption and
multi-tenant isolation remain v0.6 work.

## Non-negotiable invariants

1. **Inspection is zero-write.** It must not create directories, switch SQLite
   journal mode, create tables, recover transactions or run migrations.
2. **Corruption is not absence.** Missing data, malformed data, an I/O failure
   and an unsupported future format are distinct outcomes. None may silently
   become an empty collection.
3. **Every mutation is planned.** A plan is immutable and binds the storage
   kind, source location, source format identities and a content fingerprint.
4. **Execution rechecks the source.** If data differs from the planned
   fingerprint, execution fails before backup or mutation.
5. **Backup precedes publication.** State-changing execution writes and
   verifies a backup before staging the target result.
6. **Publication is atomic.** SQLite uses a transaction or verified database
   replacement; FileStorage uses a staging tree and atomic file/directory
   replacement. Readers never observe a half-migrated graph.
7. **Failure preserves recoverability.** A failed execution leaves the source
   unchanged or automatically restores the verified backup. It never reports
   success merely because some records were written.
8. **Authority remains scoped.** Migration, restore, deletion and rebuild never
   move relationship authority across the original `Agent × User` and
   `relationship_id` identity.
9. **Reports contain no conversation text.** Reports may contain versions,
   counts, stable record identities, digests, actions, warnings and external
   cleanup obligations, but not Source Transcript, persona source or memory
   content.
10. **Derived records do not outrank history.** Rebuild folds remaining
    authoritative history into Current Belief, Relationship State, State Reason,
    Episode, Relationship Chapter and Recall Projection. It never reconstructs
    deleted evidence from a later summary.

## Data Lifecycle deep Module

### Chosen Interface

The external seam is a three-entry `DataLifecycleCoordinator` Interface:

```python
assessment = lifecycle.inspect(target)
plan = lifecycle.plan(
    BackupRequest(source=assessment, destination=backup_target)
)
report = lifecycle.execute(plan)
```

```python
class DataLifecycleCoordinator:
    def inspect(self, target: LifecycleTarget) -> LifecycleAssessment: ...
    def plan(self, request: LifecycleRequest) -> LifecyclePlan: ...
    def execute(self, plan: LifecyclePlan) -> LifecycleReport: ...
```

`LifecycleRequest` is a closed, typed union. The implemented vocabulary is
currently `backup | restore`; `upgrade`, `delete` and `rebuild` will extend the
same union without adding another orchestration method. `execute()` includes
final verification; a successful `LifecycleReport` is therefore a verified
terminal result, not a progress flag.

The Interface includes these ordering and error rules:

- `plan()` accepts only a complete, supported assessment and freezes its source
  fingerprint, format versions, operation scope and backup destination;
- state-changing requests without a safe backup destination fail during
  planning;
- `execute()` accepts only a plan produced by the same major coordinator
  contract and re-inspects every source before acting;
- unsupported future formats raise `UnsupportedFormatError`; malformed or
  unreadable data raises `StorageIntegrityError`; changed sources raise
  `StaleLifecyclePlanError`; failed verification raises
  `LifecycleVerificationError` with recovery status;
- retries return or resume the same operation identity and cannot widen a
  deletion scope or select a different migration result.

`LifecycleTargetKind.BACKUP` is a first-class inspectable target. A v1 bundle
contains a strict no-content manifest plus a complete payload. The manifest
binds the plan digest, source format identity, source content fingerprint,
canonical relative file list, byte counts and per-file SHA-256 digests. It does
not contain the source's absolute path or any conversation/persona text. These
unkeyed digests detect damage and plan drift; they do not authenticate who
created a bundle. Signatures, MACs, encryption and authorization remain v0.6.

### Why this shape

Three alternatives were considered:

1. one generic `run(command)` method is smaller syntactically, but hides the
   crucial inspect/plan/execute ordering and makes dry-run hard to prove;
2. a public pluggable pipeline with separate backup, migration, validation,
   publication and recovery ports is flexible, but exposes implementation
   ordering and creates a shallow Interface before third-party demand exists;
3. a stateful migration session makes the common path convenient, but makes
   durable plans, process restarts and audit serialization harder.

The chosen hybrid keeps the ordering visible while hiding backup mechanics,
staging, transactions, graph validation and recovery. This gives callers high
leverage and keeps migration knowledge local to one Module.

### Adapters and seam placement

The implementation owns internal `FileLifecycleAdapter`,
`SQLiteLifecycleAdapter` and `MemoryPackLifecycleAdapter` seams. They are
local-substitutable dependencies and are currently exercised by generated
temporary stores and targeted compatibility cases. Frozen v0.3.1 and relevant
v0.4-alpha historical fixtures remain a B1.3 follow-up. The adapters are not
added to `BaseStorage`, and lifecycle methods are not added to the already broad
`ERIIEngine` Interface.

The first-party CLI may present conveniences such as `erii data inspect` and
`erii data migrate --dry-run`, but those commands delegate to the same three
Interface entries. A CLI command is not a second lifecycle implementation.

## Delivery order

### B1.0 — Development and compatibility baseline

- use Package Version `0.4.0b1.dev0` until release closeout;
- require Python 3.11 and verify Python 3.11/3.14 on Linux and Windows;
- keep SQLite v9 and MemoryPack `0.4.0a8` identities unchanged until their
  formats actually change;
- make prerelease automation version-neutral;
- emit real `DeprecationWarning` from `remember()` and the transient Source Turn
  adjudication entry, naming replacements and planned v0.5 removal;
- record every post-a8 change under `CHANGELOG.md` `[Unreleased]`.

### B1.1 — Fail-closed storage integrity

- route legacy FileStorage JSON writes through flush, fsync and atomic replace;
- distinguish missing files from malformed JSON, invalid records and I/O
  failures;
- never overwrite a malformed `nodes.json`, `core_memory.json` or
  `timeline.json` with an empty/default result;
- make SQLite record decoding fail explicitly instead of returning a partial
  collection after skipping a damaged row;
- add fault-injection tests for interrupted writes, malformed JSON, invalid row
  payloads and recovery behavior.

### B1.2 — Version catalog and read-only inspection

- name Package, SQLite, FileStorage and MemoryPack versions in a dedicated
  compatibility catalog;
- inspect SQLite without instantiating `SQLiteStorage` or setting write PRAGMAs;
- inspect FileStorage without creating any path; legacy stores are recognized by
  a complete scan, and a manifest is written only by a successful migration;
- make MemoryPack readers require valid metadata and reject unknown fields or
  unsupported future versions before model construction;
- reject SQLite Schema versions newer than the reader rather than opening them
  as if they were current;
- once explicit migration is available, opening an older store reports
  `MigrationRequiredError` instead of silently mutating user data.

### B1.3 — Backup, migration, validation and restore

- **Implemented first slice:** strict lifecycle-backup v1 inspection; deterministic
  `BackupRequest` and `RestoreRequest` plans; plan JSON round-trip and digest
  validation; stable logical-data capture for FileStorage while excluding its
  known runtime locks (`_turn_context_snapshot.lock` and `<64hex>.lock` files
  below `_turn_locks/`, `_relationship_history_locks/`, and
  `_relationship_processing_locks/`), plus quiescent SQLite and MemoryPack;
  verified same-parent staging and atomic publication; restore only
  to a missing target; exact-plan retries returning `already_complete`; and
  non-sensitive persistent destination lock files used for cross-process
  exclusion. Plans bind the destination parent's resolved path and filesystem
  identity; publication is atomic no-replace. A failed post-publication check
  preserves the visible target for manual inspection instead of risking the
  deletion of writes made by another host.
- **Deliberately not claimed by that slice:** format upgrade, overwrite restore,
  arbitrary rollback, semantic graph migration validation, and atomic import
  into an existing online Storage. Inspection, capture and verification also
  materialize the full payload in process memory; bounded-memory streaming is a
  later B1 delivery, not a current large-store guarantee.
- Directory synchronization failures fail closed on POSIX. Windows file bytes
  are flushed, but CPython has no portable directory-handle flush, so power-loss
  persistence of the published directory entry remains a host/filesystem
  responsibility rather than a portable B1.3 guarantee.
- Cross-process locking coordinates cooperating hosts. B1.3 paths must live in
  trusted local directories; the lock is not authorization or an adversarial
  same-host filesystem boundary.
- freeze real fixtures for v0.3.1 and every relevant v0.4 alpha storage format;
- preserve full legacy Timeline history; export limits used for display or
  recall must never truncate backups;
- create and verify backup manifests before target publication;
- execute migrations on a staging copy or in one atomic transaction;
- validate identity, counts, causal references, Unicode ranges, temporal order,
  authority tiers and artifact commitments before publication;
- make MemoryPack import globally atomic rather than a sequence of visible
  partial writes;
- prove repeated migration, restart and restore are idempotent.
- replace whole-payload memory materialization with chunked, bounded-memory
  capture and verification while retaining stable-source detection, canonical
  manifest commitments and atomic publication.

### B1.4 — Deletion and deterministic rebuild

- support relationship, Source Turn, Relationship Event and complete user-data
  deletion plans;
- enumerate authoritative records, derived projections, queue payloads,
  receipts, backups and external copies affected by the scope;
- rebuild from remaining append-only authority with the production projector and
  consolidator Modules, not duplicate algorithms in lifecycle code;
- return a no-content deletion report separating deleted, rebuilt, delegated and
  unverified external copies;
- require exact relationship scope at planning and execution time.

### B1.5 — Longitudinal and performance gates

- implement the three original synthetic trajectories specified in the roadmap;
- checkpoint restarts, export/import, duplicate import, positive recall,
  should-not-answer, cross-relationship leakage, correction and causal growth;
- measure recall, relationship projection, consolidation, export, import,
  deletion and rebuild at increasing data sizes;
- retain small deterministic replays in pull-request CI and run large trajectories
  in scheduled CI;
- optimize only after a baseline exists, without changing authoritative history,
  relationship scope or deterministic output.

### B1.6 — Freeze and release closeout

- publish a machine-checked stable root Interface list and classify advanced or
  internal module paths separately;
- snapshot REST OpenAPI and supported Storage/MemoryPack contracts;
- run clean wheel and sdist installation on every required Python/platform lane;
- close all known P0/P1 data-loss, cross-relationship, duplicate-write and
  unrecoverable-migration defects;
- change Package Version from `0.4.0b1.dev0` to `0.4.0b1` only after every exit
  condition passes.

## Implementation slice record

The current development tree has completed the first B1.1–B1.3 safety loop.
Its public observable tests prove:

1. missing legacy JSON still yields the documented empty/default state, while
   malformed FileStorage JSON and malformed SQLite rows fail closed without
   replacing the original bytes;
2. `LifecycleInspector` performs read-only inspection of FileStorage, SQLite,
   MemoryPack and Lifecycle Backup v1, and rejects unknown future formats before
   constructing nested domain models;
3. all three live formats can be captured into a strict Lifecycle Backup v1 and
   restored byte-for-byte to a missing target through the same
   `inspect → plan → execute` contract;
4. plans are immutable and serializable, bind source fingerprints and target
   parent identity, and use a persistent cross-process lock plus atomic
   no-replace publication;
5. FileStorage excludes only the exact documented runtime-lock paths; unknown
   `.lock` files remain payload, while stale `.tmp`, symlink/reparse, hard-link
   and other non-regular inputs fail closed;
6. a final verification failure after publication preserves the visible target
   and reports `published_target_preserved_manual_cleanup_required` instead of
   deleting the only published copy.

The 2026-08-02 verification snapshot is 474 `unittest` tests with 3 skips and
471 passing `pytest` tests with 3 skips; Ruff, `compileall`, wheel/sdist builds,
clean-package import smoke tests and the runnable backup/restore example also
pass. CI additionally exercises the process-safety lifecycle path on the
supported Windows lanes.

This record does not claim that B1 is complete. The next slice must add
historical format fixtures and a real `UpgradeRequest`, followed by migration
dry-run, protected overwrite recovery and semantic validation. It must not reuse
byte-preserving restore and call that operation a migration. The current
implementation also materializes the complete payload in memory, coordinates
cooperating trusted hosts rather than adversarial same-host writers, and treats
known unsupported Windows directory-sync errors as best-effort.
