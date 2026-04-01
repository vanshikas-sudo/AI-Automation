# Phase 0 — Stabilize & Prepare

> **Status**: IN PROGRESS
> **Goal**: Finalize planning artifacts, set up Docker dev environment, ensure V1 is clean before V2 work begins.
> **Prerequisite**: None (this is the starting phase)

---

## Tasks

### 0.1 — Create Docker Compose for Local Dev

**Files to create**: `docker-compose.yml`, `Dockerfile`, `.dockerignore`
**What**:

- PostgreSQL 16 container (port 5432)
- Redis 7 container (port 6379)
- App container (FastAPI + Uvicorn)
- Celery worker container (shared codebase, different entrypoint)
- Celery Beat container (scheduler)
- Volume mounts for data persistence
- `.env` loading via `env_file`
- Health checks for all services

**Acceptance**: `docker compose up` starts all 5 services; app connects to PG + Redis.

### 0.2 — Clean Up V1: Extract Static Configs to Constants

**Files to modify**: `app/mcp/tool_registry.py`, `app/core/intent_router.py`
**What**: Move hardcoded values to clearly labeled `_V1_DEFAULTS` dicts at top of files. This doesn't change behavior but makes V2 migration cleaner by isolating what gets replaced.
**Acceptance**: All tests still pass. Behavior identical.

### 0.3 — Add requirements-dev.txt

**Files to create**: `requirements-dev.txt`
**What**: Dev/test dependencies separate from production: pytest, pytest-asyncio, httpx (test client), ruff (linter), mypy, etc.
**Acceptance**: `pip install -r requirements-dev.txt` works; `pytest` runs.

### 0.4 — Baseline Test Coverage

**What**: Run existing tests, ensure they all pass. Document any that fail. Fix blocking failures.
**Acceptance**: `pytest` exits 0 (or all known failures documented).

### 0.5 — Create `.env.template` for V2

**Files to create**: `.env.template`
**What**: Template with ALL V2 env vars (PG, Redis, JWT secret, etc.) — separate from V1's `.env.example` which remains for backward compat.
**Acceptance**: Both `.env.example` (V1) and `.env.template` (V2) exist.

---

## Completion Criteria

- [ ] Docker Compose starts PG + Redis + App + Workers
- [ ] V1 tests pass
- [ ] Static values isolated into `_V1_DEFAULTS`
- [ ] Dev dependencies documented
- [ ] V2 env template ready
