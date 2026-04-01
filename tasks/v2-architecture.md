# V2 — Enterprise AI Automation Platform: Architecture & Implementation Plan

> **Upgrade from**: WhatsApp AI Chatbot with Zoho MCP (V1 — single-tenant, in-memory, single MCP)
> **Upgrade to**: Multi-Tenant, Event-Driven, Collaborative AI Automation Platform (V2 — enterprise-grade)

---

## Table of Contents

1. [V1 Current Architecture Summary](#1-v1-current-architecture-summary)
2. [V1 Limitations & Gaps](#2-v1-limitations--gaps)
3. [V2 Architecture Overview](#3-v2-architecture-overview)
4. [V2 Component Deep-Dive](#4-v2-component-deep-dive)
5. [Event-Driven Architecture](#5-event-driven-architecture)
6. [Database Schema Design](#6-database-schema-design)
7. [Use Case Walkthrough](#7-use-case-walkthrough)
8. [Implementation Plan (Phased)](#8-implementation-plan-phased)
9. [Technology Stack](#9-technology-stack)
10. [Migration Strategy (V1 → V2)](#10-migration-strategy-v1--v2)

---

## 1. V1 Current Architecture Summary

### What We Have Today

```
WhatsApp → FastAPI Webhook → Regex Intent Router → Agent (Chat/CRUD/Report) → LLM + Zoho MCP → WhatsApp Response
```

**Components**:

- **Entry**: FastAPI + Uvicorn with WhatsApp webhook
- **Routing**: Regex-based intent classification (CLEAR, CHAT, ZOHO_CRUD, REPORT)
- **Agents**: Chat Agent (direct LLM), Zoho CRUD Agent (LangGraph ReAct), Report Agent (PDF gen)
- **MCP**: Single Zoho MCP server with ~40 whitelisted tools out of ~248
- **Sessions**: In-memory dict, 10 msg sliding window, 30-min TTL
- **LLM**: Multi-provider via factory (Anthropic, OpenAI, Azure, Google, Groq)
- **Reports**: ReportLab + Matplotlib → PDF → WhatsApp document

### V1 Data Flow

```
User (WhatsApp)
  → POST /webhook (HMAC-SHA256 verified)
  → Parse IncomingMessage
  → Regex Intent Classification
  → Route to Agent:
      • CHAT → Direct LLM (no tools)
      • ZOHO_CRUD → LangGraph ReAct + Zoho MCP tools (org-scoped)
      • REPORT → Collect data via MCP → Generate PDF → Send document
      • CLEAR → Wipe session
  → Session: add messages, manage history
  → Response: send_text_message (4096 char limit)
```

---

## 2. V1 Limitations & Gaps

| Category           | Limitation                         | Impact                                       | V2 Solution                           |
| ------------------ | ---------------------------------- | -------------------------------------------- | ------------------------------------- |
| **Persistence**    | In-memory sessions                 | Lost on restart/deploy                       | PostgreSQL + Redis                    |
| **Multi-tenancy**  | None — single user pool            | Can't serve multiple clients                 | Tenant isolation at every layer       |
| **Auth**           | No authentication or authorization | Anyone with number has full access           | JWT + OAuth 2.0 + RBAC                |
| **MCP**            | Single Zoho MCP server             | Can't integrate Mail, Desk, etc. separately  | Multi-MCP connection pool             |
| **Agents**         | Independent, non-collaborative     | Can't handle "create invoice AND send email" | Supervisor + collaborative agents     |
| **Events**         | No event/scheduling system         | No "do X when Y happens"                     | Event bus + scheduler + triggers      |
| **Scaling**        | Single process, single server      | Can't handle concurrent load                 | Celery workers + horizontal scaling   |
| **Database**       | None                               | No audit trail, no persistent config         | PostgreSQL with proper schema         |
| **Queue**          | None — synchronous processing      | Long tasks block webhook response            | Celery + Redis task queue             |
| **Observability**  | Basic logging only                 | No metrics, no tracing, no alerting          | OpenTelemetry + Prometheus + Grafana  |
| **Channels**       | WhatsApp only                      | Limited reach                                | Multi-channel: Slack, Web, Email, API |
| **Error Handling** | Graceful degradation (empty tools) | No retry, no dead-letter queue               | DLQ + retry with exponential backoff  |

---

## 3. V2 Architecture Overview

### Design Principles

1. **Multi-Tenant by Default** — Every resource is scoped to a tenant
2. **Event-Driven Core** — Asynchronous, decoupled, scalable
3. **Collaborative Agents** — Supervisor pattern with task decomposition
4. **Multi-MCP** — Connection pool across Zoho ecosystem + extensible
5. **Enterprise Security** — JWT, RBAC, audit logging, encryption at rest
6. **Observable** — Structured logging, metrics, distributed tracing
7. **Resilient** — Circuit breakers, retries, dead-letter queues, graceful degradation

### High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                     MULTI-CHANNEL INPUT LAYER                         │
│  WhatsApp │ Slack │ Web Chat │ Email Inbound │ REST API (Programmatic)│
└─────────────────────────────┬──────────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────────┐
│                   API GATEWAY & SECURITY LAYER                        │
│  Rate Limiting │ JWT + OAuth 2.0 │ Tenant Resolver │ RBAC             │
└─────────────────────────────┬──────────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────────┐
│                     APPLICATION CORE                                   │
│                                                                        │
│  ┌─────────────┐   ┌──────────────────────────────────────────────┐   │
│  │  Message     │   │  AI BRAIN — ORCHESTRATION ENGINE             │   │
│  │  Ingestion   │──▶│  Intent Classifier (Hybrid: Regex + LLM)    │   │
│  │  (Normalize  │   │  Task Planner (DAG decomposition)           │   │
│  │  across      │   │  Agent Orchestrator (dispatch + merge)       │   │
│  │  channels)   │   │  Confirmation Engine (user approval flows)   │   │
│  └─────────────┘   └──────────────┬───────────────────────────────┘   │
│                                    │                                    │
│  ┌─────────────────────────────────▼──────────────────────────────┐    │
│  │              COLLABORATIVE AGENT SYSTEM                        │    │
│  │  ┌──────────────────────────────────────────────────────────┐  │    │
│  │  │              SUPERVISOR AGENT                            │  │    │
│  │  │   Coordinates │ Merges Results │ Resolves Conflicts      │  │    │
│  │  └────────┬─────┬──────┬──────┬──────┬──────┬──────────────┘  │    │
│  │           │     │      │      │      │      │                  │    │
│  │  ┌────┐┌────┐┌────┐┌────┐┌─────┐┌──────┐┌──────┐             │    │
│  │  │CRUD││Mail││Rpt ││Srch││Wkflw││Notif ││Custom│             │    │
│  │  │Agt ││Agt ││Agt ││Agt ││Agt  ││Agt   ││Agt   │             │    │
│  │  └────┘└────┘└────┘└────┘└─────┘└──────┘└──────┘             │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                        │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐  │
│  │Session Mgr   │ │Prompt Builder│ │LLM Router    │ │Cache Layer   │  │
│  │(Redis-backed)│ │(Tenant-aware)│ │(Cost-optimzd)│ │(Redis)       │  │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘  │
│                                                                        │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │              MULTI-MCP ORCHESTRATION LAYER                     │    │
│  │  MCP Manager (Connection Pool + Circuit Breaker + Health)      │    │
│  │  ┌─────────┐┌─────────┐┌─────────┐┌─────────┐┌─────────┐     │    │
│  │  │Zoho     ││Zoho     ││Zoho     ││Zoho     ││Custom   │     │    │
│  │  │Books MCP││CRM MCP  ││Mail MCP ││Desk MCP ││MCP Svrs │     │    │
│  │  └─────────┘└─────────┘└─────────┘└─────────┘└─────────┘     │    │
│  │  Tool Registry (Dynamic Discovery + Permission-Scoped)         │    │
│  └────────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────────┐
│                   EVENT-DRIVEN ARCHITECTURE                            │
│  ┌───────────────┐  ┌────────────────┐  ┌────────────────┐            │
│  │ Event Bus     │  │ Scheduler      │  │ Task Queue     │            │
│  │ (Redis        │  │ (APScheduler/  │  │ (Celery +      │            │
│  │  Streams /    │  │  Celery Beat)  │  │  Redis)        │            │
│  │  RabbitMQ)    │  │ Cron + Trigger │  │ Priority + DLQ │            │
│  └───────────────┘  └────────────────┘  └────────────────┘            │
└────────────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────────┐
│                        DATA LAYER                                      │
│  PostgreSQL (Tenants, Users, Events, Audit)                            │
│  Redis (Sessions, Cache, Pub/Sub, Queues)                              │
│  Object Storage — S3/MinIO (PDFs, Reports, Attachments)                │
└────────────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────────┐
│                    OBSERVABILITY LAYER                                  │
│  Structured Logging (ELK/Loki) │ Metrics (Prometheus+Grafana)          │
│  Distributed Tracing (OpenTelemetry) │ Alerting (PagerDuty/Slack)      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. V2 Component Deep-Dive

### 4.1 Multi-Channel Input Layer

**Purpose**: Accept messages from any channel, normalize into a unified `IncomingMessage` format.

```python
# Unified message model
class IncomingMessage(BaseModel):
    tenant_id: str
    channel: ChannelType  # WHATSAPP, SLACK, WEB, EMAIL, API
    user_id: str          # Canonical user identifier
    session_id: str       # Cross-channel session tracking
    text: str
    attachments: list[Attachment] = []
    metadata: dict = {}   # Channel-specific data
    timestamp: datetime
```

**Channel Adapters** (one per channel):

- `WhatsAppAdapter` — existing webhook logic, enhanced with tenant resolution
- `SlackAdapter` — Slack Events API + interactive messages
- `WebChatAdapter` — WebSocket endpoint for real-time web chat
- `EmailAdapter` — inbound email parsing (SMTP / webhook from Zoho Mail)
- `APIAdapter` — REST endpoint for programmatic access

### 4.2 API Gateway & Security Layer

**Tenant Resolution Flow**:

```
Request → Extract tenant_id (from header / JWT / phone mapping)
        → Load tenant config (DB lookup, cached in Redis)
        → Inject tenant context into request lifecycle
        → All downstream operations scoped to tenant
```

**RBAC Roles**:
| Role | Permissions |
|------|------------|
| `owner` | Full access, manage users, billing, MCP connections |
| `admin` | Configure agents, events, templates. No billing. |
| `user` | Chat, trigger actions within allowed scope |
| `viewer` | Read-only access to reports and history |

**Security Measures**:

- JWT tokens with tenant_id + user_id + role claims
- API key auth for programmatic access
- Rate limiting per tenant (configurable tiers)
- Webhook signature verification (per channel)
- Input sanitization and validation at gateway

### 4.3 AI Brain — Orchestration Engine

#### Intent Classifier (Hybrid)

```python
class IntentClassifier:
    """Hybrid intent classification: fast regex first, LLM fallback for ambiguous cases."""

    def classify(self, message: str, tenant_config: TenantConfig) -> ClassificationResult:
        # Phase 1: Regex patterns (zero cost, <1ms)
        result = self._regex_classify(message)
        if result.confidence >= 0.8:
            return result

        # Phase 2: LLM classification (for complex/ambiguous intents)
        return self._llm_classify(message, tenant_config)
```

New intents in V2:

- `CRUD` — Zoho CRUD operations (existing)
- `REPORT` — Report generation (existing)
- `WORKFLOW` — Multi-step automated flows ("create invoice AND send email")
- `EVENT_SETUP` — Schedule/trigger configuration ("when due > 3 days, send reminder")
- `SEARCH` — Cross-module data search
- `NOTIFICATION` — Alert/reminder setup
- `ADMIN` — Configuration, user management
- `CHAT` — Fallback conversational (existing)

#### Task Planner (DAG Decomposition)

**Purpose**: Break complex user requests into a Directed Acyclic Graph (DAG) of sub-tasks.

Example: _"Create an invoice for sales order SO-001 and email it to the customer"_

```
TaskDAG:
  ├── Task 1: Fetch sales order SO-001 details (CRUD Agent)
  │     └── Output: sales_order_data
  ├── Task 2: Create invoice from sales order (CRUD Agent) [depends on Task 1]
  │     └── Output: invoice_id, invoice_pdf
  ├── Task 3: Get customer email from contact (CRUD Agent) [depends on Task 1]
  │     └── Output: customer_email
  └── Task 4: Send invoice email to customer (Email Agent) [depends on Task 2, Task 3]
        └── Output: email_sent_confirmation
```

```python
class TaskPlanner:
    """Decomposes complex requests into executable task DAGs."""

    async def plan(self, intent: ClassificationResult, context: SessionContext) -> TaskDAG:
        # Use LLM to decompose into sub-tasks
        tasks = await self._decompose(intent.message, context)
        # Build dependency graph
        dag = self._build_dag(tasks)
        # Validate: no cycles, all dependencies resolvable
        dag.validate()
        return dag
```

#### Confirmation Engine

**Purpose**: For destructive/expensive operations, get user approval before execution.

```python
class ConfirmationEngine:
    """Manages user approval flows for sensitive operations."""

    ALWAYS_CONFIRM = {"delete", "send_bulk_email", "create_payment", "modify_invoice"}

    async def request_confirmation(self, task: Task, user_id: str) -> ConfirmationRequest:
        preview = await self._generate_preview(task)
        # Store pending confirmation in DB
        confirmation = ConfirmationRequest(
            id=uuid4(),
            tenant_id=task.tenant_id,
            user_id=user_id,
            task_summary=preview,
            task_data=task.serialize(),
            status="pending",
            expires_at=datetime.utcnow() + timedelta(hours=24)
        )
        await self._store(confirmation)
        # Send preview to user on their channel
        await self._send_preview(user_id, preview)
        return confirmation
```

User sees:

```
🔔 Action Confirmation Required:

I'll do the following:
1. ✅ Fetch Sales Order SO-001 (read-only)
2. ⚠️ Create Invoice INV-00234 for ₹45,000 (ZohoBooks)
3. ⚠️ Send email to client@example.com with invoice attached

Reply "confirm" to proceed or "cancel" to abort.
```

### 4.4 Collaborative Agent System

#### Supervisor Agent

```python
class SupervisorAgent:
    """Orchestrates specialized agents, merges results, handles conflicts."""

    async def execute(self, dag: TaskDAG, context: AgentContext) -> ExecutionResult:
        results = {}

        for batch in dag.get_execution_batches():  # Parallel-safe batches
            batch_tasks = []
            for task in batch:
                agent = self._select_agent(task)
                batch_tasks.append(self._run_agent(agent, task, context, results))

            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            for task, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    await self._handle_failure(task, result, context)
                else:
                    results[task.id] = result

        return self._merge_results(results, dag)
```

#### Specialized Agents

| Agent                  | Responsibility                                 | MCP Servers Used       |
| ---------------------- | ---------------------------------------------- | ---------------------- |
| **Zoho CRUD Agent**    | Create/Read/Update/Delete across Zoho modules  | Books, CRM, Inventory  |
| **Email Agent**        | Send emails, manage templates, bulk operations | Mail MCP               |
| **Report Agent**       | Generate fiscal reports, custom dashboards     | Books, CRM (read-only) |
| **Search Agent**       | Cross-module search, data aggregation          | All MCPs (read-only)   |
| **Workflow Agent**     | Multi-step automated flows, conditional logic  | All MCPs               |
| **Notification Agent** | Alerts, reminders, escalation chains           | Mail, WhatsApp         |
| **Custom Agent**       | Tenant-defined agents with custom tool access  | Configurable           |

### 4.5 Multi-MCP Orchestration Layer

```python
class MCPConnectionPool:
    """Manages connections to multiple MCP servers with health checks and circuit breakers."""

    def __init__(self):
        self.connections: dict[str, MCPConnection] = {}
        self.circuit_breakers: dict[str, CircuitBreaker] = {}

    async def get_connection(self, server_id: str, tenant_id: str) -> MCPConnection:
        key = f"{tenant_id}:{server_id}"

        # Check circuit breaker
        if self.circuit_breakers[key].is_open:
            raise MCPServerUnavailable(server_id)

        # Get or create connection
        if key not in self.connections or not self.connections[key].is_healthy:
            self.connections[key] = await self._connect(server_id, tenant_id)

        return self.connections[key]
```

**Tenant MCP Configuration** (stored in DB):

```json
{
  "tenant_id": "acme-corp",
  "mcp_connections": [
    {
      "server_id": "zoho-books",
      "url": "https://acme.zohomcp.in/books/mcp/message?key=xxx",
      "transport": "streamable_http",
      "enabled": true,
      "tool_whitelist": ["*"], // or specific tools
      "org_id": "12345"
    },
    {
      "server_id": "zoho-crm",
      "url": "https://acme.zohomcp.in/crm/mcp/message?key=xxx",
      "transport": "streamable_http",
      "enabled": true,
      "tool_whitelist": ["list_contacts", "get_contact", "search_contacts"]
    },
    {
      "server_id": "zoho-mail",
      "url": "https://acme.zohomcp.in/mail/mcp/message?key=xxx",
      "transport": "streamable_http",
      "enabled": true
    }
  ]
}
```

### 4.6 LLM Router (Cost Optimization)

```python
class LLMRouter:
    """Routes LLM requests to optimal provider based on task complexity and cost."""

    ROUTING_RULES = {
        "simple_classification": {"provider": "groq", "model": "llama-3.3-70b"},     # Fast, cheap
        "tool_calling":          {"provider": "anthropic", "model": "claude-sonnet"}, # Best tool use
        "complex_reasoning":     {"provider": "openai", "model": "gpt-4o"},           # Deep reasoning
        "summarization":         {"provider": "google", "model": "gemini-flash"},     # Cost-effective
        "fallback":              {"provider": "anthropic", "model": "claude-sonnet"}, # Reliable
    }

    async def route(self, request: LLMRequest) -> LLMResponse:
        rule = self.ROUTING_RULES.get(request.task_type, self.ROUTING_RULES["fallback"])

        try:
            return await self._call_provider(rule, request)
        except ProviderError:
            # Fallback chain
            return await self._call_provider(self.ROUTING_RULES["fallback"], request)
```

---

## 5. Event-Driven Architecture

### 5.1 Event Bus

**Purpose**: Decouple components, enable async processing, support event-driven automations.

```python
# Event schema
class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    event_type: str        # "invoice.created", "due.threshold_exceeded", "user.message"
    payload: dict
    timestamp: datetime
    source: str            # "zoho_crud_agent", "scheduler", "trigger_engine"
    correlation_id: str    # Track related events across the pipeline
```

**Event Types**:

```
# Zoho Events
invoice.created, invoice.updated, invoice.overdue
contact.created, contact.updated
payment.received, payment.overdue
sales_order.created, sales_order.fulfilled

# System Events
user.message_received
agent.task_completed, agent.task_failed
event.rule_triggered
report.generated
notification.sent, notification.failed

# Lifecycle Events
session.started, session.expired
tenant.onboarded, tenant.config_updated
```

### 5.2 Trigger Engine

**Purpose**: Evaluate conditions and fire events when thresholds are met.

**User Request**: _"Send email to all recipients when due has raised to more than 3 days"_

This translates to:

```python
class EventRule(BaseModel):
    """User-configured automation rule stored in DB."""
    rule_id: str
    tenant_id: str
    name: str                    # "Overdue Invoice Reminder"
    description: str
    trigger: TriggerConfig       # WHEN this happens
    conditions: list[Condition]  # IF these are true
    actions: list[ActionConfig]  # DO these things
    is_active: bool = True
    created_by: str
    schedule: str | None = None  # Cron expression for recurring checks

class TriggerConfig(BaseModel):
    type: str       # "schedule" | "event" | "webhook"
    source: str     # "zoho_books" | "system" | "user"
    event: str      # "invoice.overdue_check" | "cron:0 9 * * *"

class Condition(BaseModel):
    field: str      # "days_overdue"
    operator: str   # "gt" | "gte" | "lt" | "eq" | "contains"
    value: Any      # 3

class ActionConfig(BaseModel):
    agent: str      # "email_agent" | "notification_agent"
    action: str     # "send_email"
    params: dict    # {"template": "overdue_reminder", "to": "{{contact.email}}"}
```

**Trigger Evaluation Flow**:

```
Scheduler (every hour) → "Check overdue invoices for tenant X"
    → Trigger Engine evaluates rule
    → Fetches invoices via Zoho Books MCP (days_overdue > 3)
    → For each matching invoice:
        → Creates Event: "due.threshold_exceeded"
        → Event Bus → Task Queue
        → Email Agent picks up task
        → Sends personalized email using template
        → Logs result in audit trail
```

### 5.3 User Confirmation for Event Setup

When user says: _"Send email to all recipients when due has raised to more than 3 days"_

**AI Response Flow**:

```
1. Intent classified as EVENT_SETUP

2. AI parses and structures the rule:
   "I understand you want to set up this automation:

   📋 Rule: Overdue Invoice Reminder
   ⏰ When: Invoice is overdue by more than 3 days
   📧 Action: Send reminder email to invoice recipient
   🔄 Check Frequency: Every day at 9:00 AM

   Details I'll configure:
   • Data source: ZohoBooks - Invoices
   • Condition: days_overdue > 3
   • Email template: Professional overdue reminder
   • Recipients: Each invoice's contact email

   Reply 'confirm' to set up this automation, or tell me what to change."

3. User confirms → Rule saved to DB → Scheduler activated

4. Follow-up: "✅ Automation 'Overdue Invoice Reminder' is now active!
   It will check daily at 9:00 AM and send reminders for invoices
   overdue by more than 3 days.

   You can manage your automations anytime by saying:
   • 'list my automations'
   • 'pause overdue reminder'
   • 'edit overdue reminder threshold to 5 days'"
```

### 5.4 Task Queue (Celery)

```python
# Task definitions
@celery_app.task(bind=True, max_retries=3, retry_backoff=True)
def execute_agent_task(self, task_data: dict):
    """Execute a single agent task from the queue."""
    try:
        task = AgentTask.from_dict(task_data)
        agent = get_agent(task.agent_type)
        result = asyncio.run(agent.execute(task))

        # Publish completion event
        event_bus.publish(Event(
            event_type="agent.task_completed",
            payload={"task_id": task.id, "result": result}
        ))
        return result

    except Exception as exc:
        if self.request.retries >= self.max_retries:
            # Move to Dead Letter Queue
            dlq.push(task_data, error=str(exc))
            event_bus.publish(Event(
                event_type="agent.task_failed",
                payload={"task_id": task_data["id"], "error": str(exc)}
            ))
        raise self.retry(exc=exc)
```

---

## 6. Database Schema Design

### Core Tables

```sql
-- Tenant table
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,        -- URL-safe identifier
    plan VARCHAR(50) DEFAULT 'starter',       -- starter, pro, enterprise
    settings JSONB DEFAULT '{}',              -- Feature flags, limits
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Users table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number VARCHAR(20),                 -- WhatsApp number
    email VARCHAR(255),
    name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user',          -- owner, admin, user, viewer
    channel_identifiers JSONB DEFAULT '{}',   -- {"whatsapp": "+91xxx", "slack": "U123"}
    preferences JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, phone_number)
);

-- MCP Connections (per-tenant)
CREATE TABLE mcp_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    server_name VARCHAR(100) NOT NULL,         -- "zoho-books", "zoho-crm"
    server_url TEXT NOT NULL,
    transport VARCHAR(50) DEFAULT 'streamable_http',
    credentials JSONB DEFAULT '{}',            -- Encrypted at rest
    tool_whitelist TEXT[] DEFAULT '{}',
    org_id VARCHAR(100),
    is_active BOOLEAN DEFAULT true,
    health_status VARCHAR(50) DEFAULT 'unknown',
    last_health_check TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, server_name)
);

-- Sessions (Redis-backed, but DB for persistence)
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES users(id),
    channel VARCHAR(50),
    is_active BOOLEAN DEFAULT true,
    context JSONB DEFAULT '{}',               -- Org selection, preferences
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_activity TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

-- Conversation history
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id),
    role VARCHAR(20) NOT NULL,                -- user, assistant, system
    content TEXT NOT NULL,
    intent VARCHAR(50),
    metadata JSONB DEFAULT '{}',              -- tool_calls, tokens_used, model
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_messages_session ON messages(session_id, created_at);

-- Event rules (user-configured automations)
CREATE TABLE event_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    created_by UUID REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    trigger_config JSONB NOT NULL,            -- {type, source, event}
    conditions JSONB NOT NULL DEFAULT '[]',   -- [{field, operator, value}]
    actions JSONB NOT NULL,                   -- [{agent, action, params}]
    schedule VARCHAR(100),                    -- Cron expression
    is_active BOOLEAN DEFAULT true,
    last_triggered TIMESTAMPTZ,
    trigger_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Event log (audit trail)
CREATE TABLE event_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    event_type VARCHAR(100) NOT NULL,
    source VARCHAR(100),
    payload JSONB DEFAULT '{}',
    correlation_id UUID,
    status VARCHAR(50) DEFAULT 'processed',   -- processed, failed, retrying
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_events_tenant_type ON event_log(tenant_id, event_type, created_at);

-- Task executions (job history)
CREATE TABLE task_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    rule_id UUID REFERENCES event_rules(id),
    agent_type VARCHAR(100),
    status VARCHAR(50) DEFAULT 'queued',      -- queued, running, completed, failed, retrying
    input_data JSONB,
    output_data JSONB,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    queued_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Pending confirmations
CREATE TABLE confirmations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES users(id),
    task_summary TEXT NOT NULL,
    task_data JSONB NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',     -- pending, confirmed, cancelled, expired
    expires_at TIMESTAMPTZ NOT NULL,
    responded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 7. Use Case Walkthrough

### Use Case: "Send email to all recipients when invoice due exceeds 3 days"

**Step-by-step execution in V2**:

```
┌──────────────────────────────────────────────────────────────────┐
│ USER (WhatsApp): "Send an email to all the recipients when      │
│ the due has raised to more than 3 days threshold"               │
└──────────────────┬───────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 1: Message Ingestion                                        │
│ • WhatsApp webhook receives message                              │
│ • Tenant resolved from phone number mapping                      │
│ • User authenticated (known WhatsApp number → tenant user)       │
│ • Message normalized to IncomingMessage                          │
└──────────────────┬───────────────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 2: Intent Classification (Hybrid)                           │
│ • Regex check: "email" + "when" + "due" → EVENT_SETUP (0.7)     │
│ • LLM fallback confirms: EVENT_SETUP (0.95)                     │
│ • Sub-intent: SCHEDULE_RECURRING_CHECK                           │
└──────────────────┬───────────────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 3: Task Planner decomposes the request                      │
│ • Parse: trigger=overdue_check, condition=days>3, action=email   │
│ • Validate: tenant has ZohoBooks + ZohoMail connected            │
│ • Generate EventRule structure                                    │
└──────────────────┬───────────────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 4: Confirmation Engine asks user to verify                  │
│                                                                  │
│ AI → WhatsApp:                                                   │
│ "I'll set up this automation for you:                            │
│                                                                  │
│  📋 Rule: Overdue Invoice Email Reminder                         │
│  ⏰ Check: Daily at 9:00 AM IST                                 │
│  📊 Source: ZohoBooks Invoices                                   │
│  ⚠️ Condition: Invoice overdue by more than 3 days               │
│  📧 Action: Send reminder email to each invoice's contact        │
│  📝 Template: Professional overdue payment reminder              │
│                                                                  │
│  Reply 'confirm' to activate, or tell me what to change."        │
└──────────────────┬───────────────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 5: User confirms → "confirm"                                │
│ • ConfirmationEngine marks as confirmed                          │
│ • EventRule saved to PostgreSQL                                  │
│ • Celery Beat schedule registered (cron: "0 9 * * *")           │
│ • Audit log entry created                                        │
│                                                                  │
│ AI → WhatsApp:                                                   │
│ "✅ Automation activated! I'll check daily at 9 AM and send      │
│  reminders for invoices overdue by 3+ days.                      │
│  Say 'list automations' to see all your active rules."           │
└──────────────────┬───────────────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 6: Daily Execution (9:00 AM IST, automated)                 │
│                                                                  │
│ Celery Beat fires → Trigger Engine evaluates rule:               │
│   → Zoho CRUD Agent fetches overdue invoices via Books MCP       │
│   → Filter: days_overdue > 3                                     │
│   → Found: 5 invoices matching                                   │
│                                                                  │
│ For each matching invoice:                                        │
│   → Create task in queue (priority: normal)                      │
│   → Email Agent picks up:                                        │
│     1. Fetch contact details (CRM/Books MCP)                     │
│     2. Render email template with invoice data                   │
│     3. Send via Zoho Mail MCP                                    │
│     4. Log result in event_log                                   │
│                                                                  │
│ Summary event published → Notification Agent → WhatsApp:         │
│ "📊 Daily Overdue Check Complete:                                │
│  • 5 overdue invoices found (>3 days)                            │
│  • 5 reminder emails sent successfully                           │
│  • Total outstanding: ₹2,34,500"                                │
└──────────────────────────────────────────────────────────────────┘
```

### Use Case: "Create an invoice for sales order SO-001 and send it to client@example.com"

```
User Message → Intent: WORKFLOW
  → Task Planner creates DAG:
     Task 1: Fetch SO-001 (CRUD Agent → Books MCP) [parallel]
     Task 2: Get contact email (CRUD Agent → Books MCP) [parallel]
     Task 3: Create Invoice from SO (CRUD Agent → Books MCP) [depends: Task 1]
     Task 4: Send invoice email (Email Agent → Mail MCP) [depends: Task 2, Task 3]
  → Confirmation Engine: preview all 4 steps → user confirms
  → Supervisor executes DAG (Task 1 & 2 parallel, then 3, then 4)
  → Result merged → "Invoice INV-00234 created and emailed to client@example.com ✅"
```

---

## 8. Implementation Plan (Phased)

### Phase 1: Foundation (Weeks 1–3)

> Get the infrastructure right. No new features, just a solid base.

- [ ] **1.1** Set up PostgreSQL database with Alembic migrations
- [ ] **1.2** Implement tenant model + user model + basic RBAC
- [ ] **1.3** Replace in-memory sessions with Redis-backed SessionManager
- [ ] **1.4** Add JWT authentication middleware (API key for WhatsApp webhooks)
- [ ] **1.5** Set up Celery + Redis as task queue
- [ ] **1.6** Migrate existing config to per-tenant DB config
- [ ] **1.7** Add structured logging (structlog) with tenant_id context
- [ ] **1.8** Write migration scripts for existing V1 data/config
- [ ] **1.9** Set up Docker Compose for local dev (PostgreSQL + Redis + App + Celery Worker)
- [ ] **1.10** Integration tests for auth + tenant isolation

**Deliverable**: V1 feature-parity running on V2 infrastructure (DB, Redis, auth)

### Phase 2: Multi-MCP & Agent Collaboration (Weeks 4–6)

> Connect multiple Zoho services, make agents work together.

- [ ] **2.1** Build MCPConnectionPool with per-tenant server configs
- [ ] **2.2** Add circuit breaker pattern to MCP connections
- [ ] **2.3** Extend Tool Registry for multi-MCP tool discovery + namespacing
- [ ] **2.4** Implement Supervisor Agent pattern
- [ ] **2.5** Build Email Agent (Zoho Mail MCP integration)
- [ ] **2.6** Build Search Agent (cross-module data aggregation)
- [ ] **2.7** Implement Task Planner with DAG decomposition
- [ ] **2.8** Update Intent Classifier to hybrid model (regex + LLM fallback)
- [ ] **2.9** Implement LLM Router with cost-aware routing
- [ ] **2.10** Add per-tenant MCP management API (CRUD for connections)

**Deliverable**: Multi-agent system handling workflows like "create invoice + send email"

### Phase 3: Event-Driven Engine (Weeks 7–9)

> The scheduling, triggers, and automation backbone.

- [ ] **3.1** Build Event Bus (Redis Streams for lightweight, or RabbitMQ for heavy)
- [ ] **3.2** Implement EventRule model + CRUD API for automation rules
- [ ] **3.3** Build Trigger Engine (condition evaluation against Zoho data)
- [ ] **3.4** Integrate Celery Beat for scheduled rule evaluation
- [ ] **3.5** Build Confirmation Engine (user approval before sensitive actions)
- [ ] **3.6** Implement Dead Letter Queue for failed tasks
- [ ] **3.7** Build Workflow Agent for multi-step conditional flows
- [ ] **3.8** Build Notification Agent (summary reports, alerts)
- [ ] **3.9** Add natural language → EventRule parser (LLM-powered)
- [ ] **3.10** User-facing automation management ("list automations", "pause rule")

**Deliverable**: Users can set up "when X happens, do Y" automations via WhatsApp

### Phase 4: Enterprise Hardening (Weeks 10–12)

> Security, observability, resilience, and production readiness.

- [ ] **4.1** Full RBAC enforcement across all endpoints and agents
- [ ] **4.2** Set up OpenTelemetry for distributed tracing
- [ ] **4.3** Add Prometheus metrics + Grafana dashboards
- [ ] **4.4** Implement rate limiting per tenant (configurable tiers)
- [ ] **4.5** Add comprehensive error handling + retry policies
- [ ] **4.6** Audit logging for all data-modifying operations
- [ ] **4.7** Input sanitization + SQL injection protection (already via ORM)
- [ ] **4.8** Set up CI/CD pipeline (GitHub Actions → Docker → deploy)
- [ ] **4.9** Load testing with k6/Locust (target: 100 concurrent tenants)
- [ ] **4.10** Write operational runbook + API documentation
- [ ] **4.11** Add health check endpoints for all services (MCP, DB, Redis, Queue)
- [ ] **4.12** Implement graceful shutdown + connection draining

**Deliverable**: Production-ready, enterprise-deployable system

### Phase 5: Multi-Channel & Advanced Features (Weeks 13–15)

> Expand beyond WhatsApp, add advanced capabilities.

- [ ] **5.1** Build Slack channel adapter
- [ ] **5.2** Build Web Chat widget (WebSocket endpoint)
- [ ] **5.3** Build Email inbound adapter
- [ ] **5.4** Cross-channel session continuity (start on WhatsApp, continue on web)
- [ ] **5.5** Tenant onboarding wizard (self-service setup flow)
- [ ] **5.6** Custom agent builder (tenant defines agent + tool access)
- [ ] **5.7** Template engine for emails and notifications
- [ ] **5.8** Report scheduling (daily/weekly/monthly automated reports)
- [ ] **5.9** Webhook outbound (notify external systems on events)
- [ ] **5.10** Admin dashboard API (tenant management, usage analytics)

**Deliverable**: Multi-channel platform with self-service tenant management

---

## 9. Technology Stack

### V1 → V2 Migration

| Layer              | V1 (Current)               | V2 (Enterprise)                            |
| ------------------ | -------------------------- | ------------------------------------------ |
| **Framework**      | FastAPI + Uvicorn          | FastAPI + Uvicorn + Celery Workers         |
| **Database**       | None (in-memory)           | PostgreSQL (via SQLAlchemy + Alembic)      |
| **Cache/Sessions** | In-memory dict             | Redis (sessions, cache, pub/sub)           |
| **Task Queue**     | None (sync)                | Celery + Redis (async)                     |
| **Scheduler**      | None                       | Celery Beat + APScheduler                  |
| **Auth**           | None                       | JWT + OAuth 2.0 + RBAC                     |
| **MCP**            | Single Zoho MCP            | Multi-MCP Connection Pool                  |
| **Agents**         | 3 independent agents       | Supervisor + 6+ collaborative agents       |
| **Intent**         | Regex only                 | Hybrid (Regex + LLM fallback)              |
| **LLM**            | Single provider per deploy | LLM Router (cost-optimized multi-provider) |
| **Channels**       | WhatsApp only              | WhatsApp + Slack + Web + Email + API       |
| **Observability**  | Basic print/log            | OpenTelemetry + Prometheus + Grafana       |
| **Events**         | None                       | Event Bus + Triggers + DLQ                 |
| **Deploy**         | Single process             | Docker Compose → Kubernetes-ready          |
| **Storage**        | Temp files only            | S3/MinIO for documents                     |

### New Dependencies (additions to requirements.txt)

```
# Database
sqlalchemy[asyncio]>=2.0
asyncpg>=0.29                    # PostgreSQL async driver
alembic>=1.13                    # Migrations

# Cache & Queue
redis[hiredis]>=5.0              # Redis client with C parser
celery[redis]>=5.3               # Task queue
celery-beat>=0.2                 # Dynamic scheduling

# Auth
python-jose[cryptography]>=3.3  # JWT
passlib[bcrypt]>=1.7             # Password hashing

# Observability
opentelemetry-api>=1.20
opentelemetry-sdk>=1.20
opentelemetry-exporter-otlp>=1.20
prometheus-fastapi-instrumentator>=6.0
structlog>=23.0                  # Structured logging

# Existing (kept as-is)
fastapi>=0.115
uvicorn>=0.32
httpx>=0.27
langchain-core, langgraph, langchain-mcp-adapters
langchain-anthropic, langchain-openai, langchain-google-genai, langchain-groq
reportlab>=4.0
matplotlib>=3.8
pydantic-settings>=2.6
```

---

## 10. Migration Strategy (V1 → V2)

### Step 1: Non-Breaking Infra Addition

- Add PostgreSQL + Redis alongside existing in-memory approach
- Dual-write: save to both in-memory and DB during transition
- Zero downtime, V1 keeps working

### Step 2: Gradual Feature Flag Rollout

```python
# Feature flags in tenant config
FEATURES = {
    "use_redis_sessions": True,      # Phase 1
    "multi_mcp": False,              # Phase 2
    "event_engine": False,           # Phase 3
    "multi_channel": False,          # Phase 5
}
```

### Step 3: Agent Migration

- Keep existing agents as-is
- Add Supervisor Agent as opt-in wrapper
- New agents (Email, Workflow, Notification) added alongside
- Existing CRUD/Report agents get minor upgrades (tenant-awareness)

### Step 4: Cut-Over

- Once all features stable: remove in-memory fallbacks
- Enable full V2 for all tenants
- Deprecate V1 code paths

---

## Figma Architecture Diagrams

### V1 — Current Architecture

**FigJam**: https://www.figma.com/online-whiteboard/create-diagram/5ad70e6d-8402-462b-8666-20011cf85ef9?utm_source=other&utm_content=edit_in_figjam

### V2 — Enterprise Architecture

**FigJam**: https://www.figma.com/online-whiteboard/create-diagram/3efedfac-5653-41ee-872e-31566fbfff4c?utm_source=other&utm_content=edit_in_figjam

### Design File (Editable)

**Figma Design**: https://www.figma.com/design/apw6tfDo4ivafBh2fooekp

---

_Document created: March 27, 2026_
_Project: AI-Automation Platform V2_
