# E.R.I.I. TypeScript Client

Typed **server-side** client for the E.R.I.I. reference REST service.

> [!IMPORTANT]
> The reference server currently uses one project-owner `ERII_API_KEY`. Keep it
> in a trusted backend process. Do not import this client with that key into a
> browser bundle, React/Vue application, mobile app, desktop renderer, or other
> user-controlled environment. A frontend should call your own authenticated
> backend, which can then call E.R.I.I.

The package follows the live Python server contract:

- authentication header: `X-API-Key`
- API prefix: `/api/v1`
- canonical Turn Lifecycle: open, evaluate, complete/abandon, inspect, archive
- MemoryPack routes: `/api/v1/memory/export` and `/api/v1/memory/import`
- structured E.R.I.I. errors exposed as `ERIIError`

## Status

This client tracks the E.R.I.I. alpha API. Pin the package version and run the
contract check when upgrading. It is not a browser authentication SDK and does
not add per-user authorization to the single-owner reference server.

The repository currently treats this directory as a source-distributed SDK; it
does not claim that `@erii/client` has been published to npm. Publishing is a
separate maintainer action described in [PUBLISHING.md](PUBLISHING.md).

## Requirements

- Node.js 18 or newer
- a running E.R.I.I. reference server
- the same owner key configured as `ERII_API_KEY` on that server (at least 32
  UTF-8 bytes)

## Build and install from this checkout

```bash
cd clients/typescript
npm ci
npm run lint
npm run build
npm test
npm pack
# In the trusted backend project:
npm install /path/to/erii-client-0.5.0-alpha.3.tgz
```

After a maintainer has independently published and verified a registry artifact,
consumers can pin that exact published prerelease instead of assuming the latest
tag represents this checkout.

## Create a client

```typescript
import { ERIIClient } from '@erii/client';

const apiKey = process.env.ERII_API_KEY;
if (!apiKey) {
  throw new Error('ERII_API_KEY is required');
}

const erii = new ERIIClient({
  apiKey,
  baseURL: process.env.ERII_URL ?? 'http://127.0.0.1:8000',
});
```

The client sends the key as `X-API-Key`. Use TLS whenever the service is not
restricted to loopback.

## Recommended Turn Lifecycle

The host first persists the visible user message. It then generates/evaluates a
reply and persists exactly what was shown. Derived archival is a separate,
explicit step.

```typescript
const turn = await erii.beginTurn({
  agent_id: 'assistant',
  user_id: 'user-123',
  user_message: 'Do you remember our first walk in the snow?',
  interaction_context: [],
});

// Generate the reply in the host application. This example records the host's
// explicit unreviewed-delivery decision; no hidden worker is started here.
const receipt = await erii.completeTurn(turn.turn_id, {
  agent_id: 'assistant',
  user_id: 'user-123',
  agent_message: 'I remember the quiet street and the snow on your sleeve.',
  delivery_disposition: 'shown_unreviewed',
  delivery_exception: {
    exception_record_version: 'delivery-exception-record/v1',
    disposition: 'shown_unreviewed',
    actor_kind: 'host_policy',
    actor_id: 'my-host/v1',
    reason_code: 'availability_fallback',
    decided_at: new Date().toISOString(),
    reply_attempt_number: 1,
  },
  processing_channels: ['memory_archival'],
});

const archival = await erii.archiveTurn({
  agent_id: 'assistant',
  user_id: 'user-123',
  source_turn_id: receipt.source_turn_id,
  idempotency_key: `archive:${receipt.source_turn_id}`,
});

console.log(archival.status);
```

### Reviewed reply path

If the server has a continuity evaluator configured, evaluate the exact draft
before completing the Turn. The returned result is self-bound to the Turn and
reply and must be passed back unchanged.

```typescript
const result = await erii.evaluateTurnContinuity(turn.turn_id, {
  agent_id: 'assistant',
  user_id: 'user-123',
  proposed_reply: 'I remember the quiet street and the snow on your sleeve.',
  persona_context_refs: personaEvidenceRefs,
  relationship_context_refs: relationshipEvidenceRefs,
});

await erii.completeTurn(turn.turn_id, {
  agent_id: 'assistant',
  user_id: 'user-123',
  agent_message: 'I remember the quiet street and the snow on your sleeve.',
  continuity_result: result,
  delivery_disposition: 'shown',
  processing_channels: ['memory_archival'],
});
```

### Already-visible exchange

Use `recordTurn` only when both messages were already displayed before the host
could open a Turn. The API requires an explicit `shown_unreviewed` exception.

```typescript
const receipt = await erii.recordTurn({
  user_id: 'user-123',
  user_message: 'Hello',
  agent_message: 'Hello. I am here.',
  delivery_exception: {
    exception_record_version: 'delivery-exception-record/v1',
    disposition: 'shown_unreviewed',
    actor_kind: 'host_policy',
    actor_id: 'legacy-adapter/v1',
    reason_code: 'preexisting_visible_exchange',
    decided_at: new Date().toISOString(),
    reply_attempt_number: 1,
  },
});
```

## Turn methods

| Method | Server route | Result |
|---|---|---|
| `beginTurn(request)` | `POST /api/v1/turns/open` | durable `TurnResource` |
| `recordTurn(request)` | `POST /api/v1/turns` | `SourceTurnReceipt` |
| `completeTurn(turnId, request)` | `POST /api/v1/turns/{id}/complete` | `SourceTurnReceipt` |
| `evaluateTurnContinuity(turnId, request)` | `POST /api/v1/turns/{id}/continuity/evaluate` | bound evaluation result |
| `recordReplyAttempt(turnId, request)` | `POST /api/v1/turns/{id}/reply-attempts` | sanitized attempt record |
| `listReplyAttempts(turnId, scope)` | `GET /api/v1/turns/{id}/reply-attempts` | attempt records |
| `getTurn(turnId, scope)` | `GET /api/v1/turns/{id}` | one scoped Turn |
| `listTurns(scope)` | `GET /api/v1/turns` | scoped Turns |
| `abandonTurn(turnId, request)` | `POST /api/v1/turns/{id}/abandon` | abandoned Turn |
| `archiveTurn(request)` | `POST /api/v1/archivals` | current archival receipt |
| `getArchival(id, scope)` | `GET /api/v1/archivals/{id}` | current archival receipt |

`archiveTurn` may receive either HTTP 200 or 202. The returned receipt's
`status` is authoritative; poll with `getArchival` when it is `pending`,
`processing`, or `retry_wait`.

## Recall and core memory

```typescript
const context = await erii.recall({
  agent_id: 'assistant',
  user_id: 'user-123',
  query: 'snowy walk',
  top_k: 8,
});

await erii.setCoreMemory({
  agent_id: 'assistant',
  user_id: 'user-123',
  content: 'Host-managed core persona source text.',
});

const core = await erii.getCoreMemory('assistant', 'user-123');
```

`remember()` remains available only as a deprecated compatibility endpoint.
New integrations should use the explicit Turn Lifecycle.

## MemoryPack portability

```typescript
const pack = await erii.exportMemory({
  agent_id: 'assistant',
  user_id: 'user-123',
});

await erii.importMemory({
  pack_data: pack,
  agent_id: 'assistant',
  user_id: 'user-456',
  overwrite: false,
});
```

The old `export(agentId, userId)` and `import(pack, ...)` method names remain as
deprecated aliases. Both use the canonical memory routes and `pack` response
field.

## Error handling

Endpoints that return E.R.I.I.'s structured error body are converted to
`ERIIError`. FastAPI validation arrays and network/transport failures remain
Axios errors, because the server has not yet standardized every error shape.

```typescript
import { ERIIError } from '@erii/client';

try {
  await erii.getTurn('missing-turn', {
    agent_id: 'assistant',
    user_id: 'user-123',
  });
} catch (error) {
  if (error instanceof ERIIError) {
    console.error(error.code, error.statusCode, error.retryable, error.message);
  } else {
    throw error;
  }
}
```

## Development verification

From `clients/typescript`:

```bash
npm ci
npm run lint
npm run build
npm test
```

From the repository root, after installing the Python server extras and
`httpx`, verify the SDK against the live FastAPI schema and authentication
middleware:

```bash
python clients/typescript/scripts/verify_server_contract.py
```

CI executes all five checks.

## Links

- [E.R.I.I. repository](https://github.com/bailong-Hakuryu/E.R.I.I)
- [Host integration guide](../../docs/host-integration.md)
- [General usage guide](../../docs/USAGE.md)
- [Issue tracker](https://github.com/bailong-Hakuryu/E.R.I.I/issues)

## License

Apache-2.0
