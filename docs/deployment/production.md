# E.R.I.I. Production Deployment Guide

**Version:** v0.5.0a2+  
**Last Updated:** 2026-08-10  
**Status:** Production-Ready Alpha

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Deployment Architecture](#deployment-architecture)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Security Hardening](#security-hardening)
7. [Rate Limiting](#rate-limiting)
8. [Monitoring & Logging](#monitoring--logging)
9. [Backup & Recovery](#backup--recovery)
10. [Scaling](#scaling)
11. [Troubleshooting](#troubleshooting)

---

## Overview

E.R.I.I. (Experiential Recall & Impression Integration) is a single-tenant AI agent memory system designed to be embedded in applications or deployed as a REST API service.

### Deployment Models

- **Embedded Library** - Import directly into Python applications
- **REST API Service** - FastAPI server for multi-language clients
- **Docker Container** - Containerized deployment
- **Cloud Platforms** - AWS, GCP, Azure compatible

### Production Readiness

- ✅ **Security:** Single-owner model, parameterized queries, credential redaction
- ✅ **Testing:** 671 tests passing (100% pass rate)
- ✅ **Performance:** Benchmarked with 100+ memories
- ✅ **Reliability:** Transaction safety, concurrent access tested

---

## Prerequisites

### System Requirements

**Minimum:**
- Python 3.10+
- 2 GB RAM
- 10 GB disk space
- Linux, macOS, or Windows

**Recommended:**
- Python 3.11+
- 4+ GB RAM
- 50+ GB disk space (for large memory stores)
- Linux (Ubuntu 22.04+ or similar)

### Dependencies

```bash
# Core dependencies (automatically installed)
pip install erii

# Optional: For ChromaDB vector store
pip install chromadb

# Optional: For development/testing
pip install pytest black ruff
```

---

## Deployment Architecture

### Single-Server Deployment

```
┌─────────────────────────────────────┐
│         Reverse Proxy (Nginx)       │
│     - Rate Limiting                 │
│     - SSL/TLS Termination           │
│     - Static Content (if any)       │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│      E.R.I.I. API Server            │
│      (FastAPI + Uvicorn)            │
│      Port: 8000 (internal)          │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│         Storage Layer               │
│  - SQLite (default)                 │
│  - FileStorage (alternative)        │
│  - ChromaDB (optional vectors)      │
└─────────────────────────────────────┘
```

### Multi-Instance Deployment

```
                  ┌─────────────┐
                  │ Load Balancer│
                  └──────┬───────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   ┌─────────┐      ┌─────────┐    ┌─────────┐
   │ Instance│      │ Instance│    │ Instance│
   │    1    │      │    2    │    │    3    │
   └────┬────┘      └────┬────┘    └────┬────┘
        │                │               │
        └────────────────┼───────────────┘
                         ▼
                 ┌──────────────┐
                 │ Shared Storage│
                 │   (NFS/EFS)   │
                 └──────────────┘
```

**Note:** SQLite doesn't support concurrent writes from multiple processes. For multi-instance deployment, consider using:
- Read replicas (read-only instances)
- Message queue for write coordination
- Or migrate to PostgreSQL (future support)

---

## Installation

### Method 1: pip (Recommended)

```bash
# Install E.R.I.I.
pip install erii

# Verify installation
python -c "import erii; print(erii.__version__)"
```

### Method 2: From Source

```bash
# Clone repository
git clone https://github.com/yourusername/erii.git
cd erii

# Install in development mode
pip install -e .

# Run tests
python -m unittest discover tests
```

### Method 3: Docker

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install E.R.I.I.
RUN pip install --no-cache-dir erii uvicorn[standard]

# Expose API port
EXPOSE 8000

# Create data directory
RUN mkdir -p /data/erii_memory

# Start server
CMD ["python", "-m", "erii.server.app", \
     "--storage-dir", "/data/erii_memory", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
```

Build and run:
```bash
docker build -t erii-server .
docker run -d \
  -p 8000:8000 \
  -v /path/to/data:/data \
  -e ERII_API_KEY="your-secret-key-here" \
  --name erii \
  erii-server
```

---

## Configuration

### Environment Variables

```bash
# Required
export ERII_API_KEY="your-secret-key-minimum-32-bytes-long"

# Optional
export ERII_STORAGE_DIR="./erii_memory"          # Data directory
export ERII_LOG_LEVEL="INFO"                     # DEBUG, INFO, WARNING, ERROR
export ERII_MAX_REQUEST_BODY_BYTES="8388608"    # 8MB default
export ERII_ALLOW_LOOPBACK="false"               # Dev mode (insecure)
```

### API Key Generation

```bash
# Generate a secure API key (64 characters)
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Important:** Store API keys securely:
- Use environment variables or secrets manager
- Never commit to version control
- Rotate periodically (e.g., every 90 days)

### Storage Configuration

#### SQLite (Default)

```python
from erii import ERIIEngine, SQLiteStorage

# Production configuration
storage = SQLiteStorage(
    db_path="/data/erii_memory/erii.db",
)

engine = ERIIEngine(storage_driver=storage)
```

**SQLite Tuning:**
```sql
-- Set in application initialization
PRAGMA journal_mode=WAL;        -- Write-Ahead Logging
PRAGMA synchronous=NORMAL;      -- Balance safety/performance
PRAGMA cache_size=-64000;       -- 64MB cache
PRAGMA temp_store=MEMORY;       -- Temp tables in RAM
```

#### FileStorage

```python
from erii import ERIIEngine, FileStorage

storage = FileStorage(
    storage_dir="/data/erii_memory"
)

engine = ERIIEngine(storage_driver=storage)
```

---

## Security Hardening

### 1. API Authentication

E.R.I.I. uses API key authentication (single-owner model):

```python
# Server setup
from erii.server.app import configure_server_access

configure_server_access(
    api_key="your-secret-key-here",
    allow_unauthenticated_loopback=False  # Never true in production
)
```

**Client Authentication:**
```bash
curl -H "Authorization: Bearer your-secret-key-here" \
     https://your-server.com/api/v1/recall \
     -d '{"agent_id": "agent1", "user_id": "user1", "query": "hello"}'
```

### 2. Network Security

**Firewall Rules:**
```bash
# Allow only HTTPS (443) and SSH (22)
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
sudo ufw deny 8000/tcp  # Block direct API access
sudo ufw enable
```

**Reverse Proxy (Nginx):**
```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL Configuration
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Security Headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000" always;

    # Proxy to E.R.I.I.
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
```

### 3. File Permissions

```bash
# Storage directory
chmod 700 /data/erii_memory
chown erii:erii /data/erii_memory

# Database files
chmod 600 /data/erii_memory/*.db
```

### 4. Secrets Management

**AWS Secrets Manager:**
```python
import boto3
import json

def get_api_key():
    client = boto3.client('secretsmanager', region_name='us-east-1')
    response = client.get_secret_value(SecretId='erii/api-key')
    secret = json.loads(response['SecretString'])
    return secret['api_key']

configure_server_access(api_key=get_api_key())
```

---

## Rate Limiting

See [`docs/deployment/rate-limiting.md`](rate-limiting.md) for detailed configuration.

### Quick Setup (Nginx)

```nginx
# Define rate limit zone
limit_req_zone $binary_remote_addr zone=erii_api:10m rate=100r/m;

location /api/ {
    # Apply rate limit
    limit_req zone=erii_api burst=20 nodelay;
    limit_req_status 429;
    
    proxy_pass http://127.0.0.1:8000;
}
```

### Application-Level Rate Limiting

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/v1/recall")
@limiter.limit("10/minute")
def api_recall(request: Request, req: RecallRequest):
    # ...
```

---

## Monitoring & Logging

### Application Logging

```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/erii/app.log'),
        logging.StreamHandler()
    ]
)
```

### Health Check Endpoint

```python
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": __version__,
        "timestamp": datetime.utcnow().isoformat()
    }
```

**Monitor with:**
```bash
# Simple uptime monitor
*/5 * * * * curl -f https://your-domain.com/health || alert-script.sh
```

### Metrics Collection

**Prometheus Integration:**
```python
from prometheus_client import Counter, Histogram, generate_latest

recall_counter = Counter('erii_recalls_total', 'Total recall requests')
recall_duration = Histogram('erii_recall_duration_seconds', 'Recall duration')

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type="text/plain")
```

### Log Aggregation

**Example: Send logs to CloudWatch**
```python
import watchtower

logger.addHandler(watchtower.CloudWatchLogHandler(
    log_group='/aws/erii/production'
))
```

---

## Backup & Recovery

### Automated Backups

**Backup Script:**
```bash
#!/bin/bash
# backup-erii.sh

BACKUP_DIR="/backup/erii"
STORAGE_DIR="/data/erii_memory"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p $BACKUP_DIR

# Backup SQLite database
sqlite3 $STORAGE_DIR/erii.db ".backup $BACKUP_DIR/erii_$DATE.db"

# Compress
tar -czf $BACKUP_DIR/erii_$DATE.tar.gz -C $STORAGE_DIR .

# Upload to S3 (optional)
aws s3 cp $BACKUP_DIR/erii_$DATE.tar.gz s3://your-bucket/backups/

# Keep only last 30 days
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete

echo "Backup completed: erii_$DATE.tar.gz"
```

**Cron Schedule:**
```cron
# Daily backup at 2 AM
0 2 * * * /usr/local/bin/backup-erii.sh
```

### Recovery

```bash
# 1. Stop the service
sudo systemctl stop erii

# 2. Restore from backup
tar -xzf /backup/erii/erii_20260810.tar.gz -C /data/erii_memory/

# 3. Verify permissions
chmod 700 /data/erii_memory
chmod 600 /data/erii_memory/*.db

# 4. Start the service
sudo systemctl start erii
```

### Point-in-Time Recovery (SQLite WAL)

```bash
# SQLite with WAL mode keeps recent transactions
cp /data/erii_memory/erii.db /restore/erii.db
cp /data/erii_memory/erii.db-wal /restore/erii.db-wal

# Apply WAL to database
sqlite3 /restore/erii.db "PRAGMA wal_checkpoint(FULL);"
```

---

## Scaling

### Vertical Scaling

**Increase Resources:**
- More RAM → Larger cache, better query performance
- More CPU → Handle more concurrent requests
- Faster disk → Quicker database operations

**Configuration Tuning:**
```python
# Increase worker processes
uvicorn erii.server.app:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker
```

### Horizontal Scaling (Read Replicas)

```
┌──────────┐      ┌──────────┐
│ Writer   │──────▶│ Primary  │
│ Instance │      │ Database │
└──────────┘      └────┬─────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
    ┌────────┐    ┌────────┐    ┌────────┐
    │Reader 1│    │Reader 2│    │Reader 3│
    └────────┘    └────────┘    └────────┘
```

**SQLite Replication:**
```bash
# Use Litestream for replication
litestream replicate /data/erii_memory/erii.db s3://bucket/db
```

### Caching Layer

```python
from functools import lru_cache
from datetime import datetime, timedelta

# Cache recall results
@lru_cache(maxsize=1000)
def cached_recall(agent_id: str, user_id: str, query: str):
    return engine.recall(agent_id, user_id, query)
```

---

## Troubleshooting

### Common Issues

#### 1. Database Locked

**Symptom:** `sqlite3.OperationalError: database is locked`

**Solution:**
```python
# Enable WAL mode
connection.execute("PRAGMA journal_mode=WAL")

# Increase timeout
connection = sqlite3.connect(db_path, timeout=30.0)
```

#### 2. High Memory Usage

**Symptom:** Process using > 2GB RAM

**Diagnosis:**
```python
import tracemalloc

tracemalloc.start()
# ... run operations ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
```

**Solution:**
- Reduce cache size
- Limit concurrent requests
- Add memory limits (Docker: `--memory=2g`)

#### 3. Slow Queries

**Diagnosis:**
```sql
EXPLAIN QUERY PLAN SELECT * FROM nodes WHERE agent_id=? AND user_id=?;
```

**Solution:**
- Ensure indexes exist
- Reduce data volume (archive old memories)
- Upgrade to SSD storage

#### 4. API Timeouts

**Symptom:** 504 Gateway Timeout

**Solution:**
```nginx
# Increase Nginx timeouts
proxy_connect_timeout 120s;
proxy_send_timeout 120s;
proxy_read_timeout 120s;
```

### Debug Mode

```bash
# Enable debug logging
export ERII_LOG_LEVEL=DEBUG

# Run with debugger
python -m pdb -m erii.server.app
```

### Getting Help

- **GitHub Issues:** https://github.com/yourusername/erii/issues
- **Documentation:** https://erii.readthedocs.io
- **Community:** [Discord/Slack link]

---

## Production Checklist

Before going live:

- [ ] API key generated (≥32 bytes) and stored securely
- [ ] SSL/TLS certificate configured
- [ ] Rate limiting enabled
- [ ] Firewall rules configured
- [ ] Automated backups scheduled
- [ ] Monitoring/alerting set up
- [ ] Health check endpoint tested
- [ ] Load testing completed
- [ ] Recovery procedure documented and tested
- [ ] Security audit completed
- [ ] Logging configured and centralized
- [ ] Resource limits set (CPU, memory, disk)

---

## Additional Resources

- [Rate Limiting Guide](rate-limiting.md)
- [Security Model](../SECURITY.md)
- [API Reference](../api-reference.md)
- [Architecture Decision Records](../adr/)

---

**Document Version:** 1.0  
**Last Review:** 2026-08-10  
**Next Review:** 2026-11-10
