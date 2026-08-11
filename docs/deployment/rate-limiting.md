# Rate Limiting the E.R.I.I. Reference Server

**Status:** deployment starting point, not a production-readiness or capacity
claim.

The reference FastAPI server has no built-in rate limiter. A trusted product
host or reverse proxy must enforce request rates, concurrency, quotas, and user
authorization before traffic reaches E.R.I.I. Limits must be measured against
the actual storage, Turn volume, extractors/evaluators, and provider latency.

## Security boundary first

The reference server uses one owner-level `ERII_API_KEY`; it is not an end-user
identity. Consequently:

- never expose the owner key to a browser or mobile client;
- do not treat arbitrary inbound `X-API-Key` values as rate-limit identities;
  an attacker can rotate fake values before authentication;
- apply end-user quotas at the authenticated product-host boundary;
- use source-IP limiting only as a coarse perimeter control;
- keep the reference server reachable only from the trusted host/proxy.

Recommended topology:

```text
User client
  -> product authentication + object authorization + user quota
  -> trusted gateway/reverse proxy + perimeter rate/concurrency limit
  -> single-owner E.R.I.I. reference server
```

## Nginx starting point

The following fragment applies a coarse per-source-IP request rate to business
routes and deliberately leaves the public health route separate. `[RATE]` and
`[BURST]` are deployment values to choose from measurements, not project
defaults.

```nginx
limit_req_zone $binary_remote_addr zone=erii_perimeter:10m rate=[RATE];

upstream erii_reference {
    server 127.0.0.1:8000;
    keepalive 16;
}

server {
    listen 443 ssl;
    server_name HOST;

    ssl_certificate     /path/to/certificate;
    ssl_certificate_key /path/to/private-key;

    location = /api/v1/health {
        proxy_pass http://erii_reference;
    }

    location /api/v1/ {
        limit_req zone=erii_perimeter burst=[BURST] nodelay;
        limit_req_status 429;

        proxy_pass http://erii_reference;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

This fragment does not implement product authentication or inject the owner
key. Those operations depend on the trusted host architecture and must happen
before forwarding. If clients can connect around the proxy, the limit is not a
boundary.

When returning a proxy-generated 429, a host may use the same public shape as
the reference server:

```json
{
  "detail": {
    "code": "rate_limit_exceeded",
    "retryable": true,
    "safe_summary": "Too many requests. Try again later."
  }
}
```

The proxy should add an appropriate `Retry-After` value when it can calculate
one. Do not include user messages, relationship data, or the owner key in the
response.

## Other gateways

Caddy, Traefik, cloud API gateways, service meshes, and application gateways can
enforce the same boundary. Their configuration syntax and available algorithms
change independently of E.R.I.I.; use the documentation for the exact deployed
version and verify behavior in staging. Required properties are:

1. a stable authenticated identity for product-user quotas;
2. a coarse source/network limit before expensive parsing or provider work;
3. bounded concurrent upstream requests;
4. a bounded request body and upstream timeout;
5. a 429 response that contains no private input;
6. metrics that do not use transcript text, API keys, or relationship IDs as
   labels.

An in-process Python limiter is useful only when the host deliberately owns
that lifecycle and topology. It consumes application resources before
rejection, and process-local counters do not become distributed quotas merely
because multiple workers are started.

## Choosing limits

Do not copy a fixed requests-per-minute table. Measure at least:

- p50/p95/p99 latency for each business operation;
- storage write and lock contention;
- reliable-archival queue age and completion rate;
- extractor/evaluator/provider concurrency and cost;
- request-body size distribution;
- failure and retry amplification;
- one-process memory and file-descriptor usage.

Set separate host policies for inexpensive reads, Turn writes, archival, export,
and import. A provider quota is an upstream ceiling, not an appropriate product
limit by itself.

## Verification

Test a protected business route, because `/api/v1/health` is intentionally
outside the example limiter. Use only synthetic IDs and a temporary deployment
key.

```bash
for i in $(seq 1 [REQUEST_COUNT]); do
  curl --silent --output /dev/null --write-out "%{http_code}\n" \
    -H "X-API-Key: <OWNER_KEY>" \
    "https://HOST/api/v1/turns?agent_id=AGENT&user_id=USER"
done | sort | uniq -c
```

Verify all of the following instead of checking only that some 429 responses
appear:

- requests within the measured limit reach the upstream successfully;
- excess requests receive 429 and an expected `Retry-After` policy;
- the health route follows its separately chosen policy;
- invalid owner keys do not create unlimited independent buckets;
- product users cannot consume each other's quota;
- proxy restarts and multiple replicas have the intended counter semantics;
- logs and metrics contain no owner key or transcript content.

## Monitoring and rollback

Monitor accepted/rejected request counts, upstream latency, 4xx/5xx rates,
concurrency, queue age, storage capacity, and provider cost. Alert on sustained
rejection or queue growth rather than a single burst. Keep a tested proxy
configuration rollback and validate it without bypassing authentication.

See also:

- [Reference-server deployment guide](production.md)
- [Security policy](../../SECURITY.md)
- [Turn and REST errors](../api/turn-error-handling.md)
