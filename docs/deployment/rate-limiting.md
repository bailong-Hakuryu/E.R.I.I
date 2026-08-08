# Rate Limiting for E.R.I.I. Reference Server

E.R.I.I.'s reference FastAPI server does not include built-in rate limiting. Per [SECURITY.md](../../SECURITY.md), rate limiting should be implemented at the "trusted proxy/host layer."

This guide provides production-ready examples.

---

## Why Rate Limiting?

Without rate limiting, the E.R.I.I. API is vulnerable to:
- **DoS attacks**: Overwhelming the service with requests
- **LLM cost explosion**: Excessive recall/generation operations
- **Resource exhaustion**: Database and memory overload

---

## Recommended Approach

Place a reverse proxy (nginx, Caddy, or Traefik) in front of the E.R.I.I. server:

```
Client → Reverse Proxy (rate limit) → E.R.I.I. FastAPI Server
```

---

## Nginx Configuration

### Basic Rate Limiting

```nginx
# Define rate limit zone (10MB memory, ~160K IP addresses)
limit_req_zone $binary_remote_addr zone=erii_api:10m rate=100r/m;

# Rate limit for burst traffic
limit_req_zone $binary_remote_addr zone=erii_burst:10m rate=20r/s;

server {
    listen 443 ssl http2;
    server_name erii-api.example.com;

    # SSL configuration
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # General API endpoints - 100 requests/minute
    location /api/v1/ {
        limit_req zone=erii_api burst=20 nodelay;
        limit_req_status 429;

        # Custom error response for rate limit
        error_page 429 = @rate_limit_error;

        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Health check - no rate limit
    location /api/v1/health {
        proxy_pass http://localhost:8000;
    }

    # Custom rate limit error response
    location @rate_limit_error {
        default_type application/json;
        return 429 '{"detail":{"code":"rate_limit_exceeded","retryable":true,"safe_summary":"Too many requests. Please try again later."}}\n';
    }
}
```

### Per-API-Key Rate Limiting

If using API keys, rate limit per key instead of per IP:

```nginx
# Extract API key from header
map $http_x_api_key $api_key_for_limit {
    default $binary_remote_addr;
    "~^(.+)$" $1;
}

limit_req_zone $api_key_for_limit zone=erii_per_key:10m rate=1000r/m;

server {
    location /api/v1/ {
        limit_req zone=erii_per_key burst=50 nodelay;
        proxy_pass http://localhost:8000;
    }
}
```

---

## Caddy Configuration

Caddy with `caddy-rate-limit` plugin:

```caddy
{
    order rate_limit before basicauth
}

erii-api.example.com {
    # Rate limit by remote IP
    rate_limit {
        zone dynamic_erii {
            key {remote_host}
            events 100
            window 1m
        }
    }

    # Route to E.R.I.I. server
    reverse_proxy localhost:8000 {
        # Forward real IP
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
    }
}
```

**Install rate limit plugin:**
```bash
xcaddy build --with github.com/mholt/caddy-ratelimit
```

---

## Traefik Configuration

Using Traefik's rate limit middleware:

```yaml
# docker-compose.yml
services:
  traefik:
    image: traefik:v2.10
    command:
      - "--providers.docker=true"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
    ports:
      - "443:443"
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock"
      - "./traefik:/etc/traefik"

  erii-api:
    image: erii:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.erii.rule=Host(`erii-api.example.com`)"
      - "traefik.http.routers.erii.entrypoints=websecure"
      
      # Rate limit middleware
      - "traefik.http.middlewares.erii-ratelimit.ratelimit.average=100"
      - "traefik.http.middlewares.erii-ratelimit.ratelimit.period=1m"
      - "traefik.http.middlewares.erii-ratelimit.ratelimit.burst=20"
      
      - "traefik.http.routers.erii.middlewares=erii-ratelimit"
```

---

## Cloud Provider Solutions

### AWS API Gateway

If deploying behind AWS API Gateway:

```yaml
# serverless.yml
functions:
  erii:
    handler: erii_lambda.handler
    events:
      - http:
          path: /api/v1/{proxy+}
          method: ANY
          throttling:
            maxRequestsPerSecond: 100
            maxConcurrentRequests: 50
```

### Google Cloud Armor

For GKE deployments:

```yaml
apiVersion: cloud.google.com/v1
kind: BackendConfig
metadata:
  name: erii-backend-config
spec:
  securityPolicy:
    name: "erii-rate-limit-policy"
```

Create rate limit policy:
```bash
gcloud compute security-policies create erii-rate-limit-policy \
    --description "Rate limit for E.R.I.I. API"

gcloud compute security-policies rules create 1000 \
    --security-policy erii-rate-limit-policy \
    --expression "true" \
    --action "rate-based-ban" \
    --rate-limit-threshold-count 1000 \
    --rate-limit-threshold-interval-sec 60
```

---

## Application-Level Rate Limiting (Alternative)

If you cannot use a reverse proxy, add rate limiting directly to FastAPI using `slowapi`:

```bash
pip install slowapi
```

```python
# erii/server/app.py
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.post("/api/v1/recall")
@limiter.limit("100/minute")  # 100 requests per minute per IP
def api_recall(req: RecallRequest):
    ...
```

**⚠️ Warning:** Application-level rate limiting consumes server resources for rejected requests. Reverse proxy is preferred.

---

## Monitoring

### Nginx Logs

Monitor rate limit hits:
```bash
tail -f /var/log/nginx/access.log | grep "429"
```

### Prometheus Metrics

If using `nginx-prometheus-exporter`:
```yaml
- job_name: 'nginx'
  static_configs:
    - targets: ['localhost:9113']
```

Query rate limit hits:
```promql
rate(nginx_http_requests_total{status="429"}[5m])
```

### Alert Example

```yaml
# Prometheus alert
groups:
  - name: erii_rate_limit
    rules:
      - alert: HighRateLimitHits
        expr: rate(nginx_http_requests_total{status="429"}[5m]) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High rate limit hits on E.R.I.I. API"
          description: "{{ $value }} requests/sec being rate limited"
```

---

## Testing Rate Limits

### Simple Test

```bash
# Send 150 requests (should hit 100/min limit)
for i in {1..150}; do
    curl -H "X-API-Key: your-key" \
         https://erii-api.example.com/api/v1/health
done
```

### Load Testing with `hey`

```bash
hey -n 1000 -c 10 -H "X-API-Key: your-key" \
    https://erii-api.example.com/api/v1/recall
```

Expected: ~900 requests get HTTP 429.

---

## Recommended Limits

Based on E.R.I.I. use cases:

| Endpoint | Recommended Limit | Reasoning |
|----------|------------------|-----------|
| `/api/v1/health` | No limit | Health checks |
| `/api/v1/recall` | 100/minute | LLM-backed, expensive |
| `/api/v1/remember` | 200/minute | Simple write operation |
| `/api/v1/turns/*` | 500/minute | Core workflow |
| `/api/v1/archivals/*` | 50/minute | Heavy database operation |

**Adjust based on your hardware and LLM provider limits.**

---

## LLM Provider Rate Limits

Don't forget your LLM provider also has limits:

| Provider | Default Limit | Notes |
|----------|---------------|-------|
| OpenAI | 3,500 RPM (GPT-4) | Per organization |
| Anthropic | 50 RPM (Claude) | Per API key |
| Azure OpenAI | Custom | Set in Azure Portal |

**Your E.R.I.I. rate limit should be ≤ LLM provider limit.**

---

## See Also

- [SECURITY.md](../../SECURITY.md) - E.R.I.I. security model
- [nginx rate limiting docs](https://www.nginx.com/blog/rate-limiting-nginx/)
- [Caddy rate limit plugin](https://github.com/mholt/caddy-ratelimit)
- [slowapi docs](https://github.com/laurentS/slowapi)
