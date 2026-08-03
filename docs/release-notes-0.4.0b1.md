# E.R.I.I. 0.4.0b1 Development Milestone Notes

Milestone prepared: 2026-08-03
Distribution status: source milestone; no standalone package release planned
Acceptance status: accepted source baseline
Baseline commit: `f6dca322379c4ea88320c69d752cab471d035e95`

The last historical GitHub release remains `v0.4.0a8`. This document describes
the accepted b1 source milestone. The project does not plan to publish a
standalone package for every `0.x` milestone; reproduce b1 by pinning the full
baseline commit SHA above.

The accepted `0.4.0b1` source baseline completes the planned v0.4 feature set. It
turns data portability from
“export a pack and hope” into an explicit, verifiable lifecycle: inspect,
zero-write plan, backup-first execution and terminal verification. It also adds
repeatable long-trajectory evidence that relationship isolation and source
authority survive restarts and storage moves.

## Highlights

- The b1 source milestone supports Python 3.11–3.14. `0.4.0a8` remains the
  final Python 3.9 release.
- FileStorage, SQLite and MemoryPack share verified Backup v1 and missing-target
  restore.
- Side-by-side upgrades are available for FileStorage `legacy → 1`, SQLite
  schema `6 → 9`, and every older MemoryPack format declared readable →
  `0.4.0a8`.
- A MemoryPack can be validated and atomically published into a fresh
  FileStorage v1 or SQLite v9 target.
- Backup-first erasure covers relationship, Source Turn, Relationship Event and
  complete-user scopes. Relationship projections can be rebuilt independently
  from authoritative history.
- Earlier-history erasure follows frozen processing dependencies: surviving raw
  transcript remains, while dependent runs, events and long-term artifacts are
  revoked. Affected modern turns become explicit legacy-unavailable records
  instead of receiving a fabricated retrospective review.
- Staged erasure/rebuild publication now requires an actual production
  MemoryPack export/import round trip into a fresh same-adapter store.
- FileStorage uses an internal Windows extended-length I/O root, so hashed
  filenames and atomic temporary suffixes do not fail solely because they cross
  legacy `MAX_PATH`; the configured public root and on-disk layout are unchanged.
- Lifecycle reports contain IDs, counts and digests—not content bodies—and
  explicitly identify external deletion work that the kernel cannot verify.
- Three synthetic longitudinal scenarios run against both storage adapters;
  all six checked reports have zero failed hard metrics.

## Upgrade eligible v0.3.1/alpha data

The verified lifecycle routes are deliberately narrow: FileStorage
`legacy → 1`, SQLite schema `6 → 9`, and every older MemoryPack format declared
readable → `0.4.0a8`. SQLite schemas `0`–`5`, `7` and `8` can be identified by
inspection but are not verified b1 upgrade routes. Do not infer upgrade support
from readability alone.

1. Install b1 under Python 3.11–3.14 in a new environment. Do not replace an
   existing environment before testing its data.
2. Stop every writer, worker and Engine instance that can access the source.
3. Call `DataLifecycleCoordinator.inspect()` and keep the assessment/report.
   Continue only if the detected format/version matches a verified route above.
4. Create an `UpgradeRequest` with a missing side-by-side destination and a
   separate missing backup destination. `plan()` is the dry-run and must not
   write either target.
5. Persist the plan JSON, then call `execute()`. The verified original-format
   backup is published before the upgraded target.
6. Open and exercise the upgraded sibling. Verify relationship IDs, Source
   Turns, events, recall, export and an actual restore drill.
7. Change the host configuration to the new path only after validation. Retain
   or dispose of the old source and backup according to your policy.

Do not construct `SQLiteStorage` on an old database expecting implicit
auto-migration. Older schema access now fails closed. Schema 6 requires the
lifecycle route; other old SQLite schemas require a separately supported
migration procedure. b1 intentionally does not offer arbitrary in-place
upgrade, overwrite restore or generic downgrade.

Executable examples are in [`data-lifecycle.md`](data-lifecycle.md).

## Compatibility identities

| Axis | Value |
| --- | --- |
| Package | `0.4.0b1` accepted source identity |
| Python | 3.11–3.14 |
| SQLite schema | 9 |
| FileStorage format | 1 |
| MemoryPack wire | `0.4.0a8` |
| Lifecycle Backup | 1 |
| Lifecycle Plan | writer 3, readers 1–3 |

MemoryPack remains `0.4.0a8` because its wire contract did not change merely to
match the source package-version identifier. Backup/restore preserves the
detected format; upgrade and fresh import are separate explicit operations.

## Longitudinal evidence

The repository baseline covers:

- 128 turns in one ordinary-life relationship;
- two similar but isolated relationships with 72 turns each;
- 120 turns containing incorrect claims, corrections, conflict, reflection
  reinterpretation and one growth proposal;
- FileStorage and SQLite, restarts/retries, File↔SQLite portability, duplicate
  import, positive/negative structured recall, erase and rebuild.

The committed 46,143-byte report is
[`benchmarks/baselines/v0.4.0b1-longitudinal.json`](../benchmarks/baselines/v0.4.0b1-longitudinal.json).
On the maintainer's recorded machine, full-scale erase/rebuild observations were
about 0.60–1.87 seconds with approximately 2.05–4.42 MiB peak traced Python
memory. These are regression observations for synthetic fixtures, not latency,
capacity or memory SLAs.

## Security and deletion caveats

- Data, packs and lifecycle backups are plaintext. SHA-256 detects corruption
  and plan drift; it does not authenticate the exporter.
- The lifecycle coordinator assumes a trusted, quiescent local host. Its lock
  is not authorization, tenant isolation or protection from an adversarial
  same-host process with directory write access.
- Erasure intentionally creates a pre-change backup. A successful result does
  not mean that backup, vector indexes, exported packs, copied databases, logs,
  cloud retention or remote provider copies were removed.
- Removing an early turn/event can intentionally revoke later derived memories
  that depended on its frozen journal prefix. Unselected conversation text is
  retained, but rebuilding those derived memories requires explicit historical
  reprocessing; b1 does not silently call a model during deletion.
- The reference REST service has one owner-level API key, not per-user
  authorization. It has no built-in TLS, rate limiting or multi-tenant boundary.

Read [`../SECURITY.md`](../SECURITY.md) before using real data.

## Resource limits

File/tree hashing and copying uses chunks of at most 1 MiB. Lifecycle
MemoryPacks are capped at 256 MiB, transformations that require materialization
at 512 MiB, and backup manifests at 16 MiB. These rejection limits are not
substitutes for product quotas or adversarial upload isolation.

## Deprecated interfaces

`remember()` and transient `adjudicate_relationship_candidates()` now emit
`DeprecationWarning` and are planned for removal in v0.5. Use canonical Turn
Recording plus `archive_turn()`, and persisted-Turn
`adjudicate_turn_candidates()` / `process_relationship_turn()` respectively.
Historical records remain readable.

## What comes next

The accepted source baseline moves directly to `0.4.0rc1` development; no b1
tag, GitHub Release, uploaded wheel/sdist, or package-registry publication is
required.

v0.4 remains feature-frozen during RC. RC work is limited to compatibility,
defects, documentation, local build verification, and source closure,
including:

- a Golden Continuity Demo for relationship isolation, restart recall,
  provenance, and MemoryPack portability;
- a `Golden Path | Advanced | Experimental | Internal` public-Interface audit;
- clean-install and example execution checks, link and support-policy audits,
  and a shorter first-adoption path.

Formal Git tags, GitHub Release assets, package-registry publication, and
release readback are deferred to `1.0`.

The Golden Demo does not simulate v0.5 behavior. The character-centred model for
consequences, harm, repair without forced reconciliation, and inner review
remains v0.5 work; b1 does not silently implement it inside data lifecycle
code. ADR-0118 narrows the first v0.5 Alpha to one delivered-choice →
relationship-consequence → unresolved-tension → later-recall vertical slice.
