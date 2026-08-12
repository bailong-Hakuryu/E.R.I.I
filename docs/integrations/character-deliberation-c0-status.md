# Character Deliberation C0 status

> Snapshot date: 2026-08-12
> Product status: Experimental, removable, offline C0 + G2 prototype
> Persistence: none
> Network access: none

This is the current C0/G2 implementation status page. The canonical product
semantics remain in the
[development plan](../architecture/character-deliberation-development-plan.md),
while Claude-specific transport requirements remain in the
[Claude adapter guide](character-deliberation-claude.md).

## Implemented and locally testable

- Provider-neutral Frame + private Interior Scene + exact visible Reply schema.
- Recursively immutable tuple collections and revalidated `model_copy()`.
- Duplicate part, evidence, appraisal, candidate, impulse, tension and anchor
  identifiers fail closed.
- Provider wire decoding requires explicit identity/version fields even where
  trusted Python constructors retain ergonomic defaults.
- Strict canonical JSON rejects duplicate keys, non-finite values, unsupported
  Python types, invalid Unicode, excessive depth, collection size and document
  size.
- Public codec errors omit attacker-controlled input from message, `repr`,
  cause and context.
- Trusted Envelope V2 and Result Binding use host-held HMAC and require an open
  source Turn, matching authority epoch/scope and the exact reply candidate.
- Offline Claude-shaped SSE accumulation requires a complete `message_stop`.
- The Claude capability-profile format distinguishes `verified`, `unsupported`
  and `untested`; the offline fixture never presents its untested features as
  real model support.
- The response parser requires one strict-tool result, rejects ambiguous or
  truncated/refused results, discards reasoning blocks and returns a validated
  `CompactDecisionV1`.
- Canary matching is Unicode-normalized and scans the entire validated result.
- Evidence validation covers basis refs, counter refs, affect refs, factual
  echoes and supported claims without a basis.
- Lexical prompt-injection matching is diagnostics only; it is not allowed to
  classify character language as invalid.
- A host-owned offline bridge resolves a real `TurnRecord`, requires its frozen
  baseline and exact User message, binds actor/router/idempotency/run-fence
  identities, executes Fake Claude SSE, re-resolves authority after the Actor
  returns, and verifies the exact decision/reply Result Binding.
- The executable example displays only the final reply; Interior Scene text is
  not logged, exported or persisted.

## G2 Private Compact orchestration

The removable `erii.deliberation.orchestration` module now provides an explicit
offline adapter over existing host-owned `ERIIEngine` operations:

- `off | compact` is an explicit host choice; importing the module enables
  nothing and starts no worker.
- Compact success is rechecked by existing Continuity Review and returned as a
  prepared exact reply. The Turn remains open until the host confirms the exact
  bytes shown and calls `finalize_shown()`.
- Provider failures, abstention, staged escalation requests, unsupported
  multi-part envelopes and rejected continuity use a separately generated
  Direct fallback marked `not_deliberated`.
- Compact and Direct failures create separate sanitized attempt receipts.
  Actor, Provider and Direct callback exception bodies do not cross the module.
- Finalization re-resolves Turn status, record version, source revision and
  baseline fingerprint before calling the existing `complete_turn()` path.
- The prepared delivery object contains the exact visible reply and binding
  metadata, but no Frame or Interior Scene. No deliberation text is persisted.

The existing Continuity API accepts one string. G2 therefore accepts exactly
one text part for Compact delivery. Multi-part candidates fail closed to Direct
fallback; they are not joined with `$`, newlines or another ambiguous delimiter.
This adapter is an Experimental seam, not a stable public `ERIIEngine` method.

## CD-1 offline Shadow mechanics

The removable `erii.labs.deliberation.shadow_eval` package now exercises D0-D4
with synthetic deterministic fixtures. It verifies exact frozen-input
fingerprints, route-specific Compact/Plan/Realization shapes, Core Result
Binding where a deliberation artifact exists, and a host-signed Shadow binding
over scenario, configuration, sample, route, plan and exact visible reply.

- D0-D4 use the same fake model identity; each D4 run explicitly names the
  D1-D3 target whose call and token budget it matches.
- Blinded judge artifacts omit configuration, Provider, Frame, Interior Scene,
  bindings and metrics, while assigning stable opaque IDs to paired candidates.
- Parse, schema, binding, semantic and human-judgment states remain separate.
- Pilot-derived reliability, latency, cost, inter-rater and behavioral
  thresholds remain unset. Fake fixtures and empty samples cannot pass the
  promotion gates.
- The harness has no delivery, persistence, network, SDK or `ERIIEngine` path.

This proves evaluation mechanics only. It does not prove that deliberation
improves replies, that 20 seed scenarios are statistically sufficient, or that
any configuration qualifies for promotion.

## Explicitly not implemented

- No production Anthropic API client, credentials, HTTP calls or provider SLA.
- No stable public `ERIIEngine` method, REST, TypeScript SDK, MemoryPack, Backup
  or erase/rebuild change. The G2 adapter only composes existing host methods.
- No persisted Interior Scene, Pending Residue, user-visible Thought Projection
  or Private Reflection.
- No product Staged/Adaptive orchestration, reviewer ensemble, Session Residue
  or multi-provider failover. D2/D3 currently exist only as deterministic
  offline Shadow fixtures.
- Trusted Envelope V2 and the host bridge remain process-local Labs objects.
  They do not create a new storage format or authorize final delivery.

## C0 exit condition

C0 is complete only when the final verification commands are recorded here
with literal outputs, all Deliberation tests pass with no XFAIL, repository
secret hygiene passes without broad allowlists, and the offline end-to-end host
bridge continues to prove:

`real open Turn authority -> trusted request -> fake Claude SSE -> strict domain
decision -> evidence/canary validation -> exact Result Binding -> stale/late
result rejection`.

Passing this gate means **C0 offline contract complete**. It does not mean a
production capability, a real Claude integration, or a released v0.5 feature.

## Verification snapshot

Environment: Windows, Python 3.12.13, uncommitted working tree. The two explicit
`--ignore` arguments exclude local, Git-ignored real-API probes that are absent
from a clean checkout.

| Scope | Command | Literal result | Exit |
| --- | --- | --- | --- |
| C0 + G2 contracts and CD-1 Shadow | `python -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-g2-deliberation tests/deliberation tests/labs/deliberation/shadow_eval` | `241 passed in 3.14s` | `0` |
| CD-1 Shadow + reserved Claude seam + secret hygiene | `python -m pytest -q tests/labs/deliberation/shadow_eval tests/deliberation/test_claude_adapter.py tests/test_repository_secret_hygiene.py -rxX --tb=short` | `41 passed in 0.99s` | `0` |
| Repository regression | `python -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-g2-full tests --ignore=tests/test_full_erii_real_api_integration.py --ignore=tests/test_real_api_integration_simple.py -rxX --tb=short` | `954 passed, 5 skipped, 96 warnings, 488 subtests passed in 93.11s` | `0` |
| Optimized authority/binding/G2 regression | `python -O -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-g2-opt tests/deliberation/test_authority_envelope_validation.py tests/deliberation/test_result_binding_regression.py tests/deliberation/test_compact_orchestration.py tests/labs/deliberation/shadow_eval/test_integration.py` | `41 passed, 1 warning in 0.55s` | `0` |
| DeepSeek experiment offline contracts | `python -m pytest -q experiments/deepseek-continuity-review/tests` | `45 passed in 0.30s` | `0` |
| Credential-shaped literal scan | `python scripts/check_secrets.py` | `Secret scan passed: no credential-shaped literals in commit candidates.` | `0` |
| Secret-hygiene regression | `python -m pytest -q tests/test_repository_secret_hygiene.py` | `4 passed in 0.65s` | `0` |
| Static checks | `python -m ruff check erii tests examples benchmarks scripts experiments/deepseek-continuity-review` | `All checks passed!` | `0` |
| Source compilation | `python -m compileall -q erii tests examples benchmarks scripts experiments/deepseek-continuity-review` | no stdout | `0` |
| Documentation links | `python scripts/check_docs.py` | `Checked 168 Markdown files and 304 local links: OK` | `0` |
| Frozen contracts | `python scripts/freeze_contracts.py --check` | `contract snapshots are current (4 files)` | `0` |
| Offline demo | `python examples/deliberation_demo.py` | `C0 offline host bridge: verified`; `discarded reasoning blocks: 1`; `reply[reply-1]: I understand.` | `0` |

The 96 repository warnings are existing deprecation notices, including legacy
`remember()`/relationship adjudication calls and the Starlette TestClient
transition. They are recorded here rather than reclassified as C0 failures.
