# Public API Stability for the v0.4 Source Line

This document classifies the source tree by adoption intent. It is not a claim
that a `0.x` API has reached Semantic Versioning stability, and it does not
create a package-distribution promise. Reproducible deployments must pin a
reviewed full commit SHA.

The labels answer one question: **where should a new host begin?**

## Golden Path

Golden Path interfaces are the documented route for new integrations and
receive the strongest source-line regression, migration, and documentation
attention.

- `erii demo --output-dir PATH` as the no-network, self-verifying first-adoption
  proof; its SQLite database and MemoryPack use real supported formats, while
  `demo-report.json` remains an inspectable demo artifact rather than a frozen
  host API;
- `ERIIEngine` explicit construction, context management, `close()`, and
  `shutdown()`;
- `initialize_relationship()`;
- canonical Turn Recording:
  `begin_turn()` / `complete_turn()` / `abandon_turn()` and the atomic
  `record_turn()` convenience form;
- reliable memory derivation:
  `archive_turn()` with explicit `process_pending()` or `drain()`;
- canonical relationship derivation: `process_relationship_turn()`;
- `recall_structured()` followed by `render_recall()`;
- relationship-scoped `export_memory()` and exact-identity `import_memory()`;
- `DataLifecycleCoordinator.inspect() → plan() → execute()` for supported
  operational changes;
- built-in FileStorage and SQLiteStorage within the documented compatibility
  matrix.

Golden Path means recommended and protected by public behavior tests. It does
not mean that model quality, external services, or every historical data shape
is supported.

## Advanced

Advanced interfaces are public and supported for hosts that understand their
authority, lifecycle, and failure obligations. They are not required for the
first useful integration.

- Persona Compilation proposal, revision, approval, rejection, and revocation;
- `evaluate_reply_continuity()` and sourced contextual-voice activation;
- `adjudicate_turn_candidates()` for explicit inspection/correction tools;
- trusted-host `record_relationship_event()` and direct Promise/Open Loop
  operations;
- Persona Reflection correction/reinterpretation and approval-gated Persona
  Growth;
- Relationship Premise modes beyond the default `fresh`;
- detailed archival, relationship-run, Turn, consolidation, and lifecycle
  inspection methods;
- explicit `RecallBudget`, authority-tier inspection, public-audience recall,
  and opt-in Recall Reinforcement;
- the reference REST protocol when embedded behind a host-owned security and
  lifecycle boundary.

Advanced does not mean unsafe by definition. It means the caller must provide
more policy, stable identities, model capabilities, or operational control.

## Experimental

Experimental surfaces may be evaluated, replaced, or removed without becoming
durable character authority. They must stay behind explicit capability seams.

- Labs and host-integration prototypes, including KouriChat bridges;
- optional Model Provider adapters and Shadow comparisons;
- proposed DeepSeek-specific convenience adapters;
- proposed Provider-neutral Character Deliberation and Deliberation Ensemble
  experiments;
- experimental vector backends and third-party Storage/Agent-framework
  adapters unless their own support policy says otherwise.

The v0.4 core does not persist raw thinking, full prompts, credentials, model
error bodies, or discarded drafts as character memory. Experimental adapters
must be removable without making Turn, Recall, MemoryPack, export, or erasure
unusable.

## Internal

Internal surfaces have no compatibility promise. Do not import or call them
from host applications.

- names beginning with `_`;
- implementation modules or symbols not exported from the documented top-level
  `erii` package interface;
- private FileStorage/SQLite schema helpers, lock files, journals, staging
  layouts, task internals, and cache structures;
- test fixtures, benchmark helpers, contract-free scripts, and demo-only
  deterministic extractors;
- `erii.server.app` implementation details other than the documented CLI and
  REST protocol.

If a host needs an Internal symbol, open a synthetic feature request describing
the missing behavior. Promoting a small, stable Interface is preferable to
making storage internals public.

## Deprecated compatibility paths

`remember()` and transient
`adjudicate_relationship_candidates()` remain readable compatibility paths in
the current source line, emit `DeprecationWarning`, and are planned for removal
in v0.5. New integrations should not treat them as Experimental alternatives;
they have explicit replacements:

- `remember()` → Turn Recording + `archive_turn()`;
- transient adjudication → persisted Turn +
  `adjudicate_turn_candidates()` / `process_relationship_turn()`.

Deprecating a Python entry point does not delete historical records or remove
the corresponding legacy readers.

## Change rules

- A public field, authority rule, SQLite schema, MemoryPack wire, Backup, Plan,
  or OpenAPI change must update its own version and compatibility evidence.
- Documentation-only edits cannot silently promote planned v0.5 semantics into
  the v0.4 runtime.
- Golden Path changes require tests through public interfaces and an upgrade or
  migration statement when durable data is affected.
- Advanced-to-Golden promotion requires a shorter adoption path and real host
  evidence, not only a larger export list.
- Experimental-to-public promotion requires Provider-neutral meaning, at least
  two real implementations when variation is claimed, and complete lifecycle
  semantics.

For the actual first flow, use [Getting Started](getting-started.md) and
[Host Integration](host-integration.md). Format identities and upgrade routes
remain authoritative in [Compatibility Policy](compatibility.md).
