# Reference Server Deployment Guide

**Status:** alpha deployment guidance, not a production-readiness claim

This document describes the reference FastAPI server that is present in the
repository today. E.R.I.I. is primarily a Python kernel. A product host remains
responsible for user identity, authorization, TLS, quotas, rate limits,
observability, and its LLM/extractor capabilities.

## What the reference server provides

- one process-local `ERIIEngine`;
- FileStorage selected through `--storage-dir`;
- a single owner-level API key for business endpoints;
- loopback-only unauthenticated development when explicitly enabled;
- an 8 MiB request-body limit;
- public health, OpenAPI, and Swagger endpoints;
- stable safe REST error envelopes;
- cooperative engine close on CLI shutdown.

It does not provide:

- end-user identity or per-user authorization;
- a multi-tenant security boundary;
- TLS termination;
- rate limiting, quotas, or cost controls;
- CORS policy for browser applications;
- a CLI switch for SQLite;
- a configured memory/relationship/continuity model provider;
- automatic reliable-archival processing;
- a supported multi-worker or multi-instance deployment topology.

Both bundled storage implementations store plaintext by default. The owner key
authorizes every relationship visible to the reference-server process. Do not
expose it to a browser or mobile application.

## Requirements

- Python 3.11 through 3.14
- a writable persistent directory
- FastAPI and Uvicorn through the `server` extra

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install "erii[server]==<VERSION>"
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "erii[server]==<VERSION>"
```

Pin an exact package version or source commit. An alpha upgrade can include data
format and API changes.

## Owner API key

Generate the key at deployment time and place it in a host secret store. The
value must contain at least 32 UTF-8 bytes.

Linux shell example:

```bash
export ERII_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

PowerShell example:

```powershell
$env:ERII_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The CLI reads `ERII_API_KEY`. It does not currently read `ERII_STORAGE_DIR`,
`ERII_LOG_LEVEL`, `ERII_MAX_REQUEST_BODY_BYTES`, or `ERII_ALLOW_LOOPBACK`.
Storage and network choices are CLI arguments.

Business requests send exactly one header:

```http
X-API-Key: <ERII_API_KEY>
```

`Authorization: Bearer ...` is not accepted by the reference server.

## Local verified launch

```bash
erii serve \
  --host 127.0.0.1 \
  --port 8000 \
  --storage-dir ./data/rest-memory
```

Health is public and does not initialize storage by itself:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

Example business request:

```bash
curl \
  -H "X-API-Key: $ERII_API_KEY" \
  "http://127.0.0.1:8000/api/v1/turns?agent_id=AGENT&user_id=USER"
```

Swagger UI is available at `/docs`, and the schema is at `/openapi.json`.

For short-lived local development only:

```bash
erii serve \
  --host 127.0.0.1 \
  --port 8000 \
  --storage-dir ./data/rest-memory \
  --allow-unauthenticated-loopback
```

Do not place this unauthenticated mode behind a reverse proxy. A proxy can make
remote traffic appear to originate from loopback.

## Non-loopback deployment

The CLI rejects a non-loopback bind unless `--allow-unsafe-network` is present.
This flag acknowledges that the built-in server is plain HTTP with one owner
key; it does not add TLS or user authorization.

```bash
erii serve \
  --host 0.0.0.0 \
  --port 8000 \
  --storage-dir /var/lib/erii \
  --allow-unsafe-network
```

Before using such a bind:

1. terminate TLS at a trusted reverse proxy;
2. keep port 8000 inaccessible from untrusted networks;
3. enforce product user authentication and object-level authorization before
   forwarding requests;
4. inject the owner key at the trusted server boundary;
5. configure rate limits and body limits at the proxy as a second boundary.

See [Rate limiting](rate-limiting.md) for proxy examples. Treat them as starting
points and measure limits against the host workload.

## Minimal reverse-proxy shape

```nginx
server {
    listen 443 ssl;
    server_name HOST;

    ssl_certificate     /path/to/certificate;
    ssl_certificate_key /path/to/private-key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Authentication and authorization logic is intentionally omitted from this
fragment because it belongs to the product host. A proxy that only forwards the
owner key does not create tenant isolation.

## Process supervision

Use one reference-server process per storage directory until a deployment has
validated its own concurrency model. Do not multiply Uvicorn workers merely for
throughput: each worker owns an engine and provider instances, while FileStorage
and the operational queues require deliberate cross-process testing.

Example systemd unit shape:

```ini
[Unit]
Description=E.R.I.I. reference server
After=network.target

[Service]
Type=simple
User=erii
Group=erii
EnvironmentFile=/etc/erii/server.env
ExecStart=/opt/erii/.venv/bin/erii serve --host 127.0.0.1 --port 8000 --storage-dir /var/lib/erii
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

`/etc/erii/server.env` should be readable only by the service account and root.
It contains the runtime `ERII_API_KEY` assignment; do not commit that file.

## Container shape

```dockerfile
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 erii
WORKDIR /app
RUN python -m pip install --no-cache-dir "erii[server]==<VERSION>"
RUN mkdir -p /data/erii && chown -R erii:erii /data/erii

USER erii
EXPOSE 8000
CMD ["erii", "serve", "--host", "0.0.0.0", "--port", "8000", "--storage-dir", "/data/erii", "--allow-unsafe-network"]
```

At runtime:

- mount `/data/erii` on persistent storage;
- inject `ERII_API_KEY` from the orchestrator secret store;
- keep the container behind TLS and the product authorization layer;
- use one replica for this reference topology unless a shared-storage design has
  been implemented and tested by the host.

## Storage truth

The CLI uses FileStorage. The resulting directory contains relationship data and
an operational task database. Protect the entire directory, not only files with
a `.db` suffix.

SQLite is available to a programmatic Python host:

```python
from erii import ERIIEngine, SQLiteStorage

storage = SQLiteStorage(db_path="/var/lib/erii/erii.db")
engine = ERIIEngine(storage_driver=storage)
```

This does not make SQLite a multi-tenant authorization boundary, and the
reference CLI has no SQLite option. A custom ASGI host that selects SQLite must
also configure server access and engine shutdown explicitly.

## Processing lifecycle

The reference CLI creates and closes the engine explicitly, but it does not
configure a memory extractor, relationship-event extractor, persona interpreter,
or continuity evaluator.

Consequences include:

- reliable archival routes report `archival_capability_unavailable` unless a
  custom host injects a memory extractor;
- continuity evaluation reports `continuity_capability_unavailable` unless a
  custom host injects an evaluator;
- constructing the server does not start hidden reliable-archival processing;
- deferred reliable archival in a custom host needs explicit
  `process_pending()` or equivalent host scheduling.

`ERIIEngine.start()` starts only the legacy `remember()` worker. It is not a
replacement for explicit reliable-archival lifecycle control.

## Health and monitoring

`GET /api/v1/health` returns:

```json
{
  "status": "healthy",
  "version": "VERSION",
  "engine_initialized": true,
  "archiver_running": false
}
```

This is a process health signal, not proof that an external model provider,
archival capability, disk capacity, or product authorization service is ready.
Add host-specific readiness checks outside the reference server.

The project does not expose built-in Prometheus metrics. Instrument the host or
proxy for request counts, latency, status/error code, storage capacity, queue
age, archival outcomes, and provider cost. Do not label the full request body,
transcript, API key, or MemoryPack as telemetry.

## Backup and upgrade

File-level copying while writes are active is not a verified backup protocol.
Quiesce the host and use the data-lifecycle inspection/backup/restore workflow
documented in [Usage](../USAGE.md). Verify a restore into a separate destination
before relying on it.

Before an upgrade:

1. pin and record the current package version and source commit;
2. inspect the storage identity without mutating it;
3. create and verify a backup;
4. read the migration and compatibility documents;
5. test the upgrade against a copy;
6. run the host's Turn, recall, MemoryPack, and provider contract tests;
7. keep a rollback artifact and procedure.

## Performance and scaling

No fixed throughput, latency, memory-count, or worker-count claim is made for the
reference server. Benchmark the exact combination of:

- FileStorage or a programmatic SQLite host;
- relationship and Turn count;
- memory/event volume and recall budget;
- extractor/evaluator latency;
- request concurrency;
- backup and archival scheduling;
- operating system and storage medium.

Use results from that workload to decide limits and topology. The standalone
performance helpers and examples are not a production capacity guarantee.

## Deployment checklist

- [ ] Exact package version or source commit pinned
- [ ] Python version is supported
- [ ] Persistent storage directory mounted with least-privilege permissions
- [ ] Storage is encrypted or protected by the deployment environment as needed
- [ ] Owner key generated at deployment and stored outside the repository
- [ ] Browser/mobile clients never receive the owner key
- [ ] TLS terminates before untrusted traffic reaches the server
- [ ] Product authentication and object-level authorization are enforced
- [ ] Proxy rate/body/time limits are measured and configured
- [ ] One-process topology is used or concurrency was independently verified
- [ ] Provider capabilities and explicit processing lifecycle are configured
- [ ] Health and dependency readiness are monitored separately
- [ ] Backup restore was tested on a separate destination
- [ ] Upgrade and rollback were rehearsed
- [ ] Logs and metrics exclude transcript and credential material

## Related documents

- [English usage guide](../USAGE.md)
- [Security policy](../../SECURITY.md)
- [Rate limiting](rate-limiting.md)
- [Turn lifecycle](../api/turn-lifecycle.md)
- [Turn and REST errors](../api/turn-error-handling.md)
