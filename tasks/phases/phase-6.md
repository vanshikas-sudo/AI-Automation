# Phase 6 — Enterprise Hardening & Production Readiness

> **Status**: NOT STARTED
> **Goal**: Make the platform production-safe. Security, observability, resilience, performance — everything needed to deploy for a paying client.
> **Prerequisite**: Phase 5 complete (admin UI functional)
> **Estimated Prompts**: 8-10

---

## Tasks

### 6.1 — Full RBAC Enforcement

**Files to modify**: All route files, all agent files

**What**:

- Every API endpoint enforces role-based permissions
- Agent execution respects user role (viewer can't trigger writes)
- Tenant isolation verified at every DB query (WHERE tenant_id = ?)
- WhatsApp users: permissions checked after phone→user resolution
- Admin-only routes require `admin` or `owner` role
- Write operations: require `user`, `admin`, or `owner`
- Read operations: `viewer` and above

**Acceptance**: Viewer cannot create invoice; user can; role change takes effect immediately.

---

### 6.2 — OpenTelemetry Distributed Tracing

**Files to create**: `app/observability/tracing.py`

**What**:

- OpenTelemetry SDK initialization
- Auto-instrument: FastAPI, httpx, SQLAlchemy, Redis, Celery
- Custom spans: intent classification, agent execution, MCP tool call, LLM call
- Trace ID propagated across: webhook → handler → agent → MCP → response
- Export to: Jaeger (dev) or OTLP collector (prod)
- Trace context in structured logs

**Acceptance**: Single request traceable end-to-end in Jaeger; see time per component.

---

### 6.3 — Prometheus Metrics + Grafana Dashboards

**Files to create**: `app/observability/metrics.py`

**What**:

- `prometheus-fastapi-instrumentator` for HTTP metrics
- Custom metrics:
  - `agentflow_messages_total` (counter, by tenant, channel, intent)
  - `agentflow_agent_duration_seconds` (histogram, by agent type)
  - `agentflow_mcp_tool_calls_total` (counter, by mcp_server, tool_name, status)
  - `agentflow_llm_tokens_total` (counter, by provider, task_type)
  - `agentflow_automation_executions_total` (counter, by rule, status)
  - `agentflow_mcp_health` (gauge, by server)
- Grafana dashboard JSON (provisioned via Docker):
  - System overview: request rate, error rate, latency p50/p95/p99
  - Agent performance: execution time by type, failure rates
  - MCP health: connection status, tool call latency
  - LLM usage: tokens per tenant, cost estimate
  - Automation: triggers per day, success/failure rates

**Acceptance**: Grafana dashboard shows live metrics; alert on error rate spike.

---

### 6.4 — Rate Limiting (Per-Tenant Tiers)

**Files to create**: `app/middleware/rate_limiter.py`

**What**:

- Redis-backed sliding window rate limiter
- Configurable per tenant plan:
  - Starter: 100 messages/hour, 1000/day
  - Pro: 500 messages/hour, 10000/day
  - Enterprise: 2000 messages/hour, unlimited/day
- Separate limits for: messages, API calls, automation triggers
- Response: 429 Too Many Requests with retry-after header
- WhatsApp: send "Rate limit reached, please wait" message
- Rate limit bypass for health checks and admin API

**Acceptance**: Exceed rate limit → 429 returned; Redis tracks sliding window correctly.

---

### 6.5 — Comprehensive Error Handling + Circuit Breakers

**Files to create**: `app/core/error_handler.py`, `app/core/circuit_breaker.py`

**What**:

- Global exception handler: catch all unhandled exceptions → log + return graceful response
- Circuit breaker pattern for external services (MCP, LLM, WhatsApp API):
  - Closed: normal operation
  - Open: after 3 consecutive failures, skip calls for 30s
  - Half-open: allow 1 test call, if success → close
- Retry with exponential backoff for transient failures
- MCP timeout: configurable per-connection (default 30s)
- LLM timeout: configurable per-provider (default 60s)
- User notification: if something fails after retries, inform user clearly

**Acceptance**: MCP goes down → circuit breaker trips → graceful error to user → MCP comes back → auto-recovers.

---

### 6.6 — Audit Logging

**Files to create**: `app/services/audit_service.py`

**What**:

- Every data-modifying operation logged to `event_log` table:
  - Who (user_id, tenant_id)
  - What (action: create/update/delete, resource type, resource ID)
  - When (timestamp)
  - How (channel: WhatsApp/API/admin-ui, ip_address)
  - Details (before/after values for updates)
- Immutable: audit entries cannot be deleted
- Queryable: admin API + dashboard UI for audit trail
- Retention: configurable, default 90 days

**Acceptance**: Create invoice → audit log shows who, when, what; view in dashboard.

---

### 6.7 — Credential Encryption

**Files to create**: `app/security/encryption.py`

**What**:

- MCP credentials (`auth_config` JSONB) encrypted at rest using Fernet (symmetric)
- Encryption key from env var: `ENCRYPTION_KEY`
- Key rotation support: re-encrypt all credentials with new key
- Never log decrypted credentials
- Admin API: credentials shown as masked in responses (`***key123` → `***123`)

**Acceptance**: DB dump shows encrypted blobs, not plaintext keys; decryption works at runtime.

---

### 6.8 — CI/CD Pipeline

**Files to create**: `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`

**What**:

- **CI (on PR)**:
  - Lint: ruff
  - Type check: mypy
  - Tests: pytest with PostgreSQL + Redis services
  - Coverage: fail if < 70%
- **Deploy (on merge to main)**:
  - Build Docker images
  - Push to registry (GHCR or ECR)
  - Deploy to staging (auto)
  - Deploy to production (manual approval)

**Acceptance**: PR fails CI → lint/test errors shown; merge → auto-deploy to staging.

---

### 6.9 — Load Testing

**Files to create**: `tests/load/locustfile.py`

**What**:

- Locust load test simulating:
  - 100 concurrent tenants
  - 50 WhatsApp messages/second
  - 10 automation triggers/minute
  - Mix of CRUD, report, chat intents
- Metrics to capture: p50, p95, p99 latency; error rate; throughput
- Target: p95 < 5s for simple queries, < 30s for reports
- Identify bottlenecks: DB, Redis, MCP, LLM

**Acceptance**: Load test runs; results documented; bottlenecks identified and addressed.

---

### 6.10 — Health Check Endpoints

**Files to modify**: `app/routes/webhook.py`

**What**:

- `GET /health` — basic liveness (returns 200)
- `GET /health/ready` — readiness (checks all dependencies):
  - PostgreSQL: can query
  - Redis: can ping
  - Celery: workers alive
  - MCP connections: at least 1 healthy per active tenant
- `GET /health/detailed` — full status JSON (admin-only):
  - All component statuses
  - Active tenants count
  - Queue depth
  - Worker count

**Acceptance**: Kubernetes-compatible health checks; readiness fails if DB is down.

---

## Completion Criteria

- [ ] RBAC enforced on all endpoints
- [ ] Distributed tracing end-to-end
- [ ] Prometheus metrics + Grafana dashboard
- [ ] Rate limiting per tenant tier
- [ ] Circuit breakers on all external services
- [ ] Audit logging for all mutations
- [ ] Credentials encrypted at rest
- [ ] CI/CD pipeline running
- [ ] Load test passing targets
- [ ] Health checks for all dependencies
