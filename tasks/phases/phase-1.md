# Phase 1 — Foundation Infrastructure

> **Status**: NOT STARTED
> **Goal**: Add PostgreSQL, Redis, auth, and tenant isolation. V1 still works but now has real infrastructure behind it.
> **Prerequisite**: Phase 0 complete (Docker, clean V1)
> **Estimated Prompts**: 8-12

---

## Tasks

### 1.1 — SQLAlchemy + Alembic Setup

**Files to create**:

- `app/db/__init__.py`
- `app/db/engine.py` — async SQLAlchemy engine + session factory
- `app/db/base.py` — declarative base
- `alembic.ini` — Alembic config
- `alembic/env.py` — migration env (async)
- `alembic/versions/` — migration folder

**What**:

- Async engine using `asyncpg`
- Session factory with `async_sessionmaker`
- Alembic configured for async migrations
- Connection string from env: `DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/agentflow`

**Acceptance**: `alembic upgrade head` runs without error against Docker PG.

---

### 1.2 — Tenant & User Models

**Files to create**:

- `app/db/models/tenant.py` — Tenant model
- `app/db/models/user.py` — User model
- `app/db/models/__init__.py` — re-export all models

**Schema**:

```python
class Tenant(Base):
    __tablename__ = "tenants"
    id: UUID (PK)
    name: str
    slug: str (unique)
    plan: str (default "starter")  # starter, pro, enterprise
    settings: JSONB ({})           # feature flags, limits
    is_active: bool (default True)
    created_at, updated_at: timestamps

class User(Base):
    __tablename__ = "users"
    id: UUID (PK)
    tenant_id: UUID (FK → tenants)
    phone_number: str (nullable)
    email: str (nullable)
    name: str
    role: str (default "user")     # owner, admin, user, viewer
    hashed_password: str (nullable) # for admin UI login
    channel_identifiers: JSONB ({}) # {"whatsapp": "+91xxx", "slack": "U123"}
    is_active: bool
    created_at: timestamp
    UNIQUE(tenant_id, phone_number)
```

**Alembic migration**: Auto-generate from models.
**Acceptance**: Tables exist in PG after migration; can INSERT a tenant + user.

---

### 1.3 — MCP Connection Model

**Files to create**: `app/db/models/mcp_connection.py`

**Schema**:

```python
class MCPConnection(Base):
    __tablename__ = "mcp_connections"
    id: UUID (PK)
    tenant_id: UUID (FK → tenants)
    server_name: str              # human label: "Zoho Books", "Tally", "Salesforce CRM"
    provider_type: str            # "zoho_books", "tally", "salesforce", "custom"
    server_url: str               # MCP endpoint URL
    transport: str (default "streamable_http")  # streamable_http, sse, stdio
    auth_config: JSONB ({})       # encrypted credentials, API keys
    tool_whitelist: ARRAY(str)    # [] means "all tools allowed"
    tool_blacklist: ARRAY(str)    # tools explicitly denied
    org_id: str (nullable)        # provider-specific org/account ID
    org_discovery_tool: str (nullable)  # e.g. "ZohoBooks_list_organizations"
    org_id_field: str (nullable)        # e.g. "organization_id"
    org_name_field: str (nullable)      # e.g. "name"
    is_active: bool
    health_status: str (default "unknown")
    last_health_check: timestamp
    created_at: timestamp
    UNIQUE(tenant_id, server_name)
```

**Key Design**: `org_discovery_tool`, `org_id_field`, `org_name_field` make org detection MCP-agnostic. Each provider defines how to discover its orgs.

**Acceptance**: Table exists; can INSERT a Zoho Books connection config.

---

### 1.4 — Redis Session Manager

**Files to create**: `app/services/redis_service.py`
**Files to modify**: `app/core/session_manager.py`

**What**:

- `redis_service.py`: async Redis client (aioredis/redis-py async), connection pool
- Modify `SessionManager` to use Redis instead of class-level dicts
- Key format: `session:{tenant_id}:{user_id}:history` (list of messages)
- Key format: `session:{tenant_id}:{user_id}:context` (org selection, etc.)
- TTL on all keys (configurable per-tenant, default 30 min)
- Backward compat: if Redis unavailable, fall back to in-memory (feature flag)

**Acceptance**: Sessions survive app restart when Redis is running; TTL works.

---

### 1.5 — JWT Authentication Middleware

**Files to create**:

- `app/auth/__init__.py`
- `app/auth/jwt.py` — JWT creation, verification, token models
- `app/auth/middleware.py` — FastAPI dependency for auth
- `app/auth/rbac.py` — Role-based permission checks

**What**:

- JWT with claims: `{sub: user_id, tenant_id, role, exp}`
- `get_current_user` dependency for admin API endpoints
- WhatsApp webhook: exempt from JWT (uses HMAC signature instead)
- WhatsApp users: resolved by phone number → tenant + user mapping in DB
- API endpoints: require JWT Bearer token
- RBAC: `require_role("admin")` dependency

**Acceptance**: Admin API returns 401 without token, 200 with valid token; webhook still works without JWT.

---

### 1.6 — Tenant-Aware Config Layer

**Files to create**: `app/services/config_service.py`
**Files to modify**: `app/config.py`

**What**:

- `config_service.py`: Load tenant-specific config from DB (cached in Redis)
- `TenantConfig` model: MCP connections, LLM preferences, feature flags, prompt templates
- `config.py` becomes bootstrap-only: DB URL, Redis URL, JWT secret, log level
- Everything else (MCP URLs, prompts, tool lists) moves to DB via Phase 2
- **For now**: config_service reads from DB but falls back to env vars for V1 compat

**Acceptance**: Can load tenant config from DB; falls back to env correctly.

---

### 1.7 — Structured Logging

**Files to create**: `app/core/logging.py`
**Files to modify**: All files that use `print()` or `logging.getLogger()`

**What**:

- Replace all logging with `structlog`
- Every log includes: `tenant_id`, `user_id`, `request_id`, `intent`, `agent`
- JSON output in production, pretty-print in dev
- Log levels: DEBUG (dev), INFO (prod), ERROR (always)
- Correlation ID passed through entire request lifecycle

**Acceptance**: Logs are structured JSON with tenant context; grep-able by tenant.

---

### 1.8 — Celery + Redis Task Queue

**Files to create**:

- `app/worker/__init__.py`
- `app/worker/celery_app.py` — Celery application config
- `app/worker/tasks.py` — Base task definitions

**What**:

- Celery app with Redis broker + backend
- `celery_app = Celery("agentflow", broker="redis://localhost:6379/1")`
- Base task with tenant context injection
- Simple ping task for health verification
- Docker Compose already runs worker + beat

**Acceptance**: `celery -A app.worker.celery_app worker` starts; ping task executes.

---

### 1.9 — Admin API: Tenant CRUD

**Files to create**:

- `app/routes/admin/__init__.py`
- `app/routes/admin/tenants.py` — CRUD endpoints
- `app/routes/admin/users.py` — CRUD endpoints

**What**:

- `POST /admin/tenants` — create tenant (owner only)
- `GET /admin/tenants` — list tenants (owner only)
- `GET /admin/tenants/{id}` — get tenant details
- `PUT /admin/tenants/{id}` — update tenant
- `POST /admin/tenants/{id}/users` — add user to tenant
- All protected by JWT + RBAC

**Acceptance**: Can CRUD tenants and users via API; auth enforced.

---

### 1.10 — Phone Number → Tenant Resolution

**Files to modify**: `app/core/message_handler.py`, `app/routes/webhook.py`

**What**:

- When WhatsApp message arrives, look up phone number in `users` table
- If found: load `tenant_id`, inject into request context
- If NOT found: send "Your number is not registered. Contact your admin." and reject
- This is the bridge between WhatsApp (no JWT) and tenant isolation
- Cache phone→tenant mapping in Redis for fast lookup

**Acceptance**: Messages from registered numbers work; unknown numbers get rejection message.

---

## Completion Criteria

- [ ] PostgreSQL running with all V1 tables created via Alembic
- [ ] Redis running for sessions + cache
- [ ] JWT auth working on admin API endpoints
- [ ] WhatsApp webhook still works (phone→tenant resolution)
- [ ] Celery worker running and executing tasks
- [ ] Structured logging across all components
- [ ] All V1 tests still pass
- [ ] Can create tenant + user + MCP connection via admin API
