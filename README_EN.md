# E.R.I.I.

> Experiential Recall & Impression Integration — causal character continuity and relationship-scoped long-term memory.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Historical release](https://img.shields.io/badge/historical-v0.4.0a8-orange.svg)](https://github.com/bailong-Hakuryu/E.R.I.I/releases/tag/v0.4.0a8)
[![Source](https://img.shields.io/badge/source-v0.5.0a3-blue.svg)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/Python-3.11--3.14-green.svg)](pyproject.toml)

[简体中文](README.md)

## Why E.R.I.I.

Ordinary RAG asks which text resembles the current query. E.R.I.I. must also
decide:

- which `Agent × User` relationship owns an experience;
- why the character may remember, believe, or reinterpret it;
- which immutable events support the current relationship projection;
- whether a delivered statement may become memory, relationship, or persona
  authority;
- whether that causal chain survives restart, migration, export, erasure, and
  rebuild.

The project's core definition is:

> E.R.I.I. is a character-continuity and long-term-memory kernel. It lets a
> character continue living from an established persona and formative
> experiences in each independent relationship. The character may grow through
> real experiences, but every important change must preserve psychological and
> historical causality.

That means:

- the original Character Blueprint remains authoritative source material;
- “we watched our first snow together” belongs only to the relationship where
  it happened;
- ordinary relationship state may move gradually, while core-personality
  changes and large jumps require reviewable proposals;
- kindness is not automatically correct, and anger, refusal, or harm is not
  automatically OOC;
- models propose candidates while the kernel validates identity, scope,
  provenance, and state changes.

E.R.I.I. is an embeddable Python kernel, not a chat model, universal Agent
framework, or turnkey multi-tenant chat product.

## Install from source

The active development checkout identifies as `0.5.0a3` (alpha) and requires
Python 3.11–3.14. The `0.4.x` line is the stable maintenance line; integrations
that prioritize lower change risk should pin a reviewed full `0.4.x` commit SHA:

```bash
git clone https://github.com/bailong-Hakuryu/E.R.I.I.git
cd E.R.I.I
python -m venv .venv
```

Linux or macOS:

```bash
source .venv/bin/activate
python -m pip install .
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

Verify the source identity:

```bash
python -c "import erii; print(erii.__version__)"
```

`0.x` identifiers are source-development milestones. They do not require a Git
tag, GitHub Release, uploaded wheel/sdist, or package-registry publication for
every checkpoint. Pin a reviewed **full commit SHA** for reproducible use.
Formal package distribution is planned for `1.0`; `v0.4.0a8` remains an
immutable historical release.

Optional extras:

```bash
python -m pip install ".[server]"  # FastAPI / Uvicorn reference service
python -m pip install ".[openai]"  # Optional SDK for custom host integrations
python -m pip install ".[vector]"  # Optional vector retrieval
python -m pip install ".[dev]"     # Tests, builds, and static checks
```

## Run the Golden Continuity Demo

The base install is enough; no model API key or network is required:

```bash
erii demo --output-dir ./erii-demo
```

The self-verifying demo uses an original synthetic character and real SQLite to
prove:

1. User A and the character experience their first snow together.
2. User A still recalls it with provenance after the Engine is closed and
   reopened.
3. User B neither knows the event, inherits User A's intimacy, nor receives
   the approved Persona projection bound only to User A.
4. User A's relationship exports as `user-a.erii`, atomically imports into a
   fresh SQLite database, and survives another restart with the same
   relationship, Persona, memories, Source Turns, and content-fingerprint
   commitments while carrying no User B data. MemoryPack carries content-free
   archival Tombstones rather than pretending to preserve full operational
   receipts, so imported Recall honestly reports that portion as
   `partial_source` while retaining ordinary generation authority.

Expected output contains four `[PASS]` lines. The directory also contains the
original database, independently imported database, rendered recall, and
`demo-report.json`. The destination must not already exist, so the command
never overwrites an old run.

See [Getting Started](docs/getting-started.md) for the full proof.

## Integrate a real chat host

New hosts have one recommended durable path:

```text
Turn Recording
  → archive_turn() / process_relationship_turn()
  → recall_structured()
  → export_memory()
```

Use `record_turn()` when both visible messages already exist. When the host
still controls generation and delivery, use the stronger
`begin_turn() → complete_turn()` lifecycle. The host then explicitly runs
memory archival and/or relationship processing, recalls that relationship on a
later turn, and retains a MemoryPack export path.

E.R.I.I. does not generate the visible reply, automatically start hidden
processing threads, or select a model. `remember()` and transient
`adjudicate_relationship_candidates()` are deprecated compatibility paths, not
starting points for new integrations.

Read [Host Integration](docs/host-integration.md) and
[API Stability](docs/api-stability.md).

## The 0.5 line: Relationship Consequence

`0.5.0a1` introduced the minimum vertical slice for
**Relationship Consequence** and **Narrative Tension**. It records durable
effects from supported, delivered relationship events; projects their current
tension state (`unaddressed`, `addressed_unresolved`, `mutually_reconciled`,
`boundary_stabilized`, `relationship_ended`, or `superseded`); keeps that
projection Agent-private; and includes it in storage and lifecycle operations.
This is alpha source capability, not a stable release or production-readiness
claim. See the
[Migration Guide](docs/migration-0.5.0.md) and [Changelog](CHANGELOG.md).

## Reference

- [Getting Started: one-command isolation and restart proof](docs/getting-started.md)
- [0.4.0 stable source milestone notes](docs/release-notes-0.4.0.md)
- [Host Integration: the canonical real-chat path](docs/host-integration.md)
- [API Stability: Golden / Advanced / Experimental / Internal](docs/api-stability.md)
- [English User Guide](docs/USAGE.md)
- [中文完整使用手册](docs/USAGE_zh-CN.md)
- [Data Lifecycle](docs/data-lifecycle.md)
- [Compatibility Policy](docs/compatibility.md)
- [Domain Model](docs/domain-model.md)
- [Development Strategy](docs/development-strategy.en.md)
- [发展战略（中文）](docs/development-strategy.md)
- [Security Policy](SECURITY.md)
- [Support Policy](SUPPORT.md)
- [Roadmap](ROADMAP.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

## Current source boundary

`0.4.0b1` is an accepted source baseline at commit
`f6dca322379c4ea88320c69d752cab471d035e95`.
The `0.4.0rc1` source-closure evidence is fixed at commit
`58ea8e69df28bec8e755e0a0d2a175679c18a694`. That work remains the stable
`0.4.x` maintenance line. `0.5.0a2` was uploaded as an alpha package; the active
`0.5.0a3` checkout is the subsequent source-stabilization milestone and does not
yet have a corresponding tag or package-registry artifact.

The version axes remain independent:

| Axis | Current value |
| --- | --- |
| Python source identity | `0.5.0a3` |
| Python | `3.11`–`3.14` |
| SQLite | schema `10` |
| FileStorage | format `2` |
| MemoryPack | `0.5.0a3` (reader through `0.5.0a3`) |
| Lifecycle Backup | `1` |
| Lifecycle Plan | writer `3`, readers `1`–`3` |

`v0.5.0a1` introduced the durable Relationship Consequence and Narrative Tension
fields and advanced SQLite to schema 10 and FileStorage to format 2. The current
writer labels MemoryPack `0.5.0a3`; its reader still accepts declared-readable
older packs. A strict `0.4.0a8` reader rejects packs containing the 0.5 extension
fields, so compatibility is new-reader-to-old-data readability rather than a
bidirectional wire promise. Post-harm repair decisions and durable Character
Deliberation remain unimplemented.

## Existing kernel capabilities

- the same character may reuse one `agent_id` (shared character identity)
  across users; every `Agent × User` still has its own `relationship_id`,
  `persona_id`, and relationship-scoped memories, events, state, and intimacy;
- preserved Character Blueprint source and approved structured Persona
  Manifest;
- exact visible Source Transcript, two-phase Turn lifecycle, and append-only
  source ledger;
- message-level archival evidence and Ordinary / Legacy / Quarantined recall
  authority;
- append-only Relationship Events, five-axis state projection, Promises, and
  Open Loops;
- Relationship Consequences and deterministic Narrative Tension projection;
- Persona Reflection, approval-gated Growth Proposals, Episodes, and Chapters;
- five-axis Continuity Review, Delivery Exception, Context Baseline, and Voice
  Trace;
- FileStorage, SQLiteStorage, structured Recall, and MemoryPack;
- backup, restore, narrow upgrades, fresh import, erasure, rebuild, and
  long-horizon synthetic regression scenarios.

Treat [API Stability](docs/api-stability.md),
[Compatibility Policy](docs/compatibility.md), and the machine-readable
contract snapshots as the precise capability boundary.

## Models and experiments

The kernel is Provider-neutral. DeepSeek, other remote models, and local models
may only enter through removable adapters or experiments. E.R.I.I. does not
require one Provider, and users should not redesign a working host merely to
adopt one. Raw thinking, complete prompts, credentials, and Provider error
bodies do not become “character inner life.”

Future model collaboration is not tied to DeepSeek. A Deliberation Ensemble
would still have one Character Actor; Reviewers cannot vote to define the
character or directly write persona, relationship, or memory state.

### Experimental module

**DeepSeek Continuity Review**
([experiments/deepseek-continuity-review](experiments/deepseek-continuity-review/))
is a removable Labs experiment that explores whether thinking mode can
implement `ContinuityEvaluatorV1`. The available real-API record is one small,
hand-written exploratory run. It does not establish production accuracy, an
SLA, reproducible cost/latency gains, or a production recommendation; a
single-run scenario pass count is not an accuracy metric. Core Turn, Recall,
Continuity, MemoryPack, and lifecycle paths must continue to work when the
module is absent, disabled, or deleted.

Remote model calls send selected prompts, evidence, conversations, or memories
to that Provider. Hosts must obtain appropriate authorization, minimize egress,
and review the Provider's region, retention, deletion, and training policies.
API keys must come only from environment variables or a host secret manager;
never place them in source, documentation, fixtures, command-line arguments,
logs, or durable character data. Historical experiment output is not evidence
of kernel quality.

## Security, data, and maintenance

E.R.I.I. is maintained seriously by one person and provides no SLA. `0.4.x` is
the stable maintenance line; `0.5.0a3` is the active alpha source milestone.
FileStorage, SQLite, MemoryPack, and Lifecycle Backup are plaintext by default.
The reference REST service has one owner key, not per-user authorization or a
multi-tenant security boundary. A product host must add identity,
object-level authorization, TLS, encryption, key management, rate limits,
tenant isolation, and external-copy deletion orchestration.

Core memory, continuity semantics, and user-data portability remain open. The
project uses [Apache License 2.0](LICENSE). Third-party characters, profiles,
and source material are not part of the kernel; users remain responsible for
copyright, trademark, privacy, and platform compliance. Public issues and
fixtures must use original synthetic data—never real chats, private character
files, production databases, or credentials.
