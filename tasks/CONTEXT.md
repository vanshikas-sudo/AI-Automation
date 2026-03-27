# PROJECT CONTEXT — Intelligence Reference

> **THIS FILE IS THE SINGLE SOURCE OF TRUTH.**
> Every Copilot/Claude session MUST read this file before doing ANY work.
> It contains: what the product is, what's been built, what's hardcoded, what's next, decisions made, and lessons learned.
> Update this file after EVERY implementation session.

---

## 1. PRODUCT IDENTITY

**Name**: AgentFlow (working title)
**Type**: Multi-Tenant AI Automation Platform
**Selling Point**: Any business connects their SaaS tools (Zoho, Tally, Salesforce, etc.) via MCP, and gets an AI assistant on WhatsApp/Slack/Web that can read data, take actions, and run scheduled automations — all configured through conversation, no code.
**Target Buyer**: SMBs and mid-market companies that use Zoho/Tally/Salesforce ecosystem and want AI-powered operations automation without hiring developers.
**Revenue Model**: Per-tenant SaaS subscription (starter/pro/enterprise tiers).

### What Makes This a Product (Not a Project)

- **MCP-Agnostic**: Works with ANY platform that exposes an MCP server (Zoho, Tally, Salesforce, Xero, custom)
- **Multi-Tenant**: Each client gets isolated data, configs, agents, and MCP connections
- **Self-Service**: Clients configure MCPs, agents, automations through admin UI — no dev needed
- **Event-Driven**: Not just Q&A — handles "when X happens, do Y" automations
- **Collaborative Agents**: Multiple AI agents work together to handle complex multi-step workflows
- **Enterprise Security**: JWT, RBAC, audit logs, tenant isolation, encrypted credentials

---

## 2. CURRENT STATE (V1) — What Exists Today

### Architecture

```
WhatsApp → FastAPI Webhook → Regex Intent Router → Agent → LLM + Single Zoho MCP → WhatsApp
```

### Working Features

- [x] WhatsApp webhook (receive + send messages, send documents)
- [x] HMAC-SHA256 signature verification
- [x] Regex-based intent classification (CLEAR, CHAT, ZOHO_CRUD, REPORT)
- [x] Chat Agent (direct LLM, no tools)
- [x] Zoho CRUD Agent (LangGraph ReAct with MCP tools)
- [x] Report Agent (fiscal year PDF generation via MCP data + ReportLab)
- [x] Multi-provider LLM factory (Anthropic, OpenAI, Azure, Google, Groq)
- [x] MCP client with transport fallback (streamable_http → SSE)
- [x] Tool registry with intent-scoped tool subsets
- [x] In-memory session management (10 msg window, 30-min TTL)
- [x] Zoho org auto-detection and user selection flow
- [x] PDF report with KPIs, charts, tables, insights

### Tech Stack (V1)

- Python 3.11+, FastAPI, Uvicorn
- LangChain + LangGraph + langchain-mcp-adapters
- ReportLab + Matplotlib (PDF)
- httpx (async HTTP)
- Pydantic Settings (.env loading)

### File Structure

```
app/
├── __init__.py
├── config.py                 # Pydantic Settings, env vars
├── main.py                   # FastAPI app, lifespan init
├── agents/
│   ├── base_agent.py         # Abstract BaseAgent
│   ├── chat_agent.py         # Direct LLM (no tools)
│   ├── zoho_crud_agent.py    # LangGraph ReAct + Zoho tools
│   └── report_agent.py       # Fiscal report generation
├── core/
│   ├── intent_router.py      # Regex intent classification
│   ├── message_handler.py    # Main message routing logic
│   ├── prompt_builder.py     # Intent-scoped prompt construction
│   └── session_manager.py    # In-memory session + org tracking
├── mcp/
│   ├── client.py             # MCP connection (transport fallback)
│   ├── manager.py            # Facade: client + registry + org detect
│   ├── tool_registry.py      # Tool whitelist + intent scoping
│   └── tool_executor.py      # Tool invocation + timeout
├── providers/
│   └── llm_factory.py        # Multi-provider LLM creation
├── routes/
│   └── webhook.py            # FastAPI endpoints
├── services/
│   ├── whatsapp_service.py   # WhatsApp Cloud API integration
│   ├── report_collector.py   # Zoho data collection for reports
│   └── pdf_report_service.py # PDF generation (ReportLab)
├── utils/
│   └── validators.py         # HMAC signature verification
└── test/
    ├── conftest.py
    ├── test_validators.py
    ├── test_webhook.py
    └── test_whatsapp_service.py
```

---

## 3. HARDCODED / STATIC THINGS THAT MUST BECOME DYNAMIC

This is the critical list. Every item here blocks the product from being MCP-agnostic and multi-tenant.

### 3.1 Tool Registry — Hardcoded Zoho Tool Names

**File**: `app/mcp/tool_registry.py` (lines 18-73)
**Problem**: `TOOL_GROUPS` dict has 40 hardcoded `ZohoBooks_*` tool names in Python source
**Blocks**: Can't use non-Zoho MCPs; can't configure tools per-tenant
**V2 Solution**: DB table `mcp_tools(tool_name, group, mcp_connection_id)` — loaded dynamically per-tenant

### 3.2 Intent Patterns — Hardcoded Regex with `|zoho)`

**File**: `app/core/intent_router.py` (lines 26-53)
**Problem**: ZOHO_CRUD regex includes Zoho-specific keywords + `|zoho)\b`
**Blocks**: Can't adapt vocabulary for Tally/Salesforce; can't add custom intents
**V2 Solution**: DB table `intent_patterns(intent, regex, tenant_id)` — dynamic compilation

### 3.3 Intent Enum — Hardcoded Python Enum

**File**: `app/core/intent_router.py` (lines 18-20)
**Problem**: `class Intent(str, Enum)` with `ZOHO_CRUD`, `REPORT`, `CLEAR`, `CHAT`
**Blocks**: Can't add new intents without code deploy
**V2 Solution**: String-based intents loaded from DB, Enum kept only as fallback defaults

### 3.4 MCP URL — Single Zoho URL in ENV

**File**: `app/config.py` (line 46), `.env.example`
**Problem**: `mcp_zoho_url` env var — single MCP per deployment
**Blocks**: Multi-MCP, multi-tenant
**V2 Solution**: DB table `mcp_connections` per-tenant; `config.py` becomes bootstrap-only

### 3.5 MCP Client Key — Hardcoded "zoho"

**File**: `app/mcp/client.py` (line 43)
**Problem**: `MultiServerMCPClient({"zoho": conn_cfg})` — key is literal "zoho"
**V2 Solution**: Dynamic key from `mcp_connections.server_name`

### 3.6 Org Detection — Calls `ZohoBooks_list_organizations`

**File**: `app/mcp/manager.py` (lines 28-114)
**Problem**: `_fetch_zoho_organizations()` hardcoded to Zoho response schema
**Blocks**: Other MCPs have different org/account discovery
**V2 Solution**: Adapter pattern — `mcp_provider_schemas(provider, org_list_tool, org_id_field, org_name_field)`

### 3.7 Prompts — Mention "Zoho" Explicitly

**File**: `app/core/prompt_builder.py` (lines 43-102)
**Problem**: Base prompt says "Zoho integration", tool-specific instructions reference `ZohoBooks_*`
**Blocks**: Non-Zoho deployments get wrong prompts
**V2 Solution**: DB table `prompt_templates(intent, tenant_id, content)` with `{provider}` placeholders

### 3.8 Report Collector — Hardcoded Zoho Tool Calls

**File**: `app/services/report_collector.py` (lines 388-394, 690-695)
**Problem**: Tool names like `ZohoBooks_list_invoices` hardcoded in dict + agent prompt
**Blocks**: Can't generate reports from non-Zoho data
**V2 Solution**: DB table `report_definitions(report_id, tool_list, tenant_id, mcp_connection_id)`

### 3.9 Agent Singletons — Module-Level Instances

**File**: `app/agents/zoho_crud_agent.py` (line 99), others
**Problem**: `zoho_crud_agent = ZohoCrudAgent()` as module-level singleton
**Blocks**: Can't have per-tenant agent config, can't create agents from UI
**V2 Solution**: Agent factory pattern, loaded from DB per-tenant with tool permissions

### 3.10 Session Manager — In-Memory, No Persistence

**File**: `app/core/session_manager.py`
**Problem**: Class-level dicts, lost on restart, no tenant scoping
**V2 Solution**: Redis-backed with DB persistence fallback, tenant-scoped keys

---

## 4. V2 TARGET ARCHITECTURE

See `tasks/v2-architecture.md` for the full deep-dive. Summary:

### Layers (bottom-up)

1. **Data Layer**: PostgreSQL + Redis + S3/MinIO
2. **Event Layer**: Event Bus (Redis Streams) + Celery Task Queue + Scheduler + DLQ
3. **MCP Layer**: Multi-MCP Connection Pool + Dynamic Tool Registry + Circuit Breaker
4. **Agent Layer**: Supervisor Agent coordinating specialized agents (CRUD, Email, Report, Search, Workflow, Notification, Custom)
5. **Brain Layer**: Hybrid Intent Classifier + Task Planner (DAG) + Confirmation Engine
6. **API Layer**: FastAPI + Auth (JWT/RBAC) + Tenant Resolver + Rate Limiting
7. **Channel Layer**: WhatsApp + Slack + Web Chat + Email + REST API
8. **Observability**: OpenTelemetry + Prometheus + Grafana + Structured Logging
9. **Admin UI**: Next.js dashboard for tenant management, MCP config, agent setup, automation rules

### Key V2 Data Flow

```
Channel Message
  → API Gateway (auth + tenant resolve + rate limit)
  → Message Handler (normalize across channels)
  → Hybrid Intent Classifier (regex fast-path + LLM fallback)
  → Task Planner (decompose complex requests into DAG)
  → Confirmation Engine (preview actions, get user approval)
  → Supervisor Agent (coordinate sub-agents in parallel batches)
  → Sub-Agents execute via Multi-MCP tools
  → Results merged → Response sent back on channel
  → Events published → Audit logged → Metrics recorded
```

---

## 5. DATABASE TABLES (V2)

Core tables needed (see `v2-architecture.md` for full DDL):

| Table                | Purpose                                                            |
| -------------------- | ------------------------------------------------------------------ |
| `tenants`            | Multi-tenant isolation (id, name, slug, plan, settings)            |
| `users`              | Per-tenant users with roles (phone, email, role, channel_ids)      |
| `mcp_connections`    | Per-tenant MCP server configs (url, transport, credentials, tools) |
| `sessions`           | Persistent sessions (Redis primary, DB backup)                     |
| `messages`           | Conversation history with metadata                                 |
| `event_rules`        | User-configured automations (trigger + conditions + actions)       |
| `event_log`          | Audit trail for all events                                         |
| `task_executions`    | Job queue history (status, retries, results)                       |
| `confirmations`      | Pending user approval for sensitive actions                        |
| `agents`             | Per-tenant agent configurations                                    |
| `intent_patterns`    | Dynamic intent regex patterns per-tenant                           |
| `prompt_templates`   | Per-tenant, per-intent prompt templates                            |
| `report_definitions` | Per-tenant report configs (which tools, which fields)              |

---

## 6. IMPLEMENTATION PHASES

### Phase 0: Stabilize V1 + Prep (CURRENT)

- [x] V1 working end-to-end
- [x] V2 architecture documented
- [x] Figma diagrams created
- [ ] **THIS FILE** (CONTEXT.md) created as intelligence layer
- [ ] Phased implementation plan created (tasks/phases/)
- [ ] Docker Compose for local dev defined

### Phase 1: Foundation Infrastructure

PostgreSQL + Redis + Alembic + Docker Compose + Tenant/User models + JWT auth

### Phase 2: De-Hardcode (MCP-Agnostic Core)

Move all hardcoded items from Section 3 → database-driven + dynamic loading

### Phase 3: Multi-MCP + Agent Collaboration

Connection pool + Supervisor Agent + DAG planner + agent factory

### Phase 4: Event-Driven Engine

Event Bus + Scheduler + Triggers + Confirmation Engine + Automation management

### Phase 5: Admin UI (Next.js Dashboard)

Tenant management + MCP connection wizard + Agent config + Automation rules + Analytics

### Phase 6: Enterprise Hardening

RBAC enforcement + OpenTelemetry + Rate limiting + CI/CD + Load testing

### Phase 7: Multi-Channel

Slack + Web Chat + Email + Cross-channel session continuity

> **Each phase has its own detailed file**: `tasks/phases/phase-N.md`

---

## 7. DECISIONS MADE

| #   | Decision                               | Rationale                                                                   | Date       |
| --- | -------------------------------------- | --------------------------------------------------------------------------- | ---------- |
| D1  | PostgreSQL over MongoDB                | Relational integrity for tenants/users/events; JSONB for flexible fields    | 2026-03-27 |
| D2  | Redis for sessions + cache + event bus | Single infrastructure for 3 concerns; Redis Streams for lightweight pub/sub | 2026-03-27 |
| D3  | Celery + Redis (not RabbitMQ)          | Simpler ops; Redis already in stack; Celery Beat for scheduling             | 2026-03-27 |
| D4  | Next.js for admin UI                   | React ecosystem; SSR for SEO; API routes for BFF pattern                    | 2026-03-27 |
| D5  | Keep LangGraph for agents              | Already in V1; proven tool-calling; no need to switch                       | 2026-03-27 |
| D6  | Alembic for migrations                 | SQLAlchemy integration; version-controlled schema changes                   | 2026-03-27 |
| D7  | MCP-agnostic from Phase 2              | Core differentiator; not just "Zoho bot" but "any MCP platform"             | 2026-03-27 |
| D8  | Feature flags for gradual rollout      | DB-stored per-tenant; zero-downtime migration from V1                       | 2026-03-27 |

---

## 8. LESSONS LEARNED

> Update this section after every correction or mistake during implementation.

| #   | Lesson     | Context | Date |
| --- | ---------- | ------- | ---- |
| L1  | (none yet) |         |      |

---

## 9. ACTIVE CONTEXT

> **Update this section at the START and END of every implementation session.**

### Current Phase: Phase 0 (Planning)

### Last Completed Task: V2 architecture document + Figma diagrams

### Next Task: Create phase-by-phase implementation files, then start Phase 1

### Blockers: None

### Open Questions: None

---

## 10. HOW TO USE THIS FILE

### For Copilot/Claude (every session):

1. **Read this file FIRST** — before touching any code
2. Check Section 9 (Active Context) — what phase are we in? what's next?
3. Check Section 3 (Hardcoded items) — if touching those files, make them dynamic
4. Check Section 7 (Decisions) — don't re-debate settled decisions
5. Check Section 8 (Lessons) — don't repeat past mistakes
6. After work: **UPDATE Section 9** with what was done and what's next

### For the developer:

1. Start each session by telling Copilot: "Read tasks/CONTEXT.md first"
2. Pick the next task from the current phase file
3. After implementation: ask Copilot to update CONTEXT.md Section 9
4. If a mistake was made: ask Copilot to add to Section 8

---

_Last updated: 2026-03-27_
_Phase: 0 — Planning_
