# Phase 2 — De-Hardcode: MCP-Agnostic Core

> **Status**: NOT STARTED
> **Goal**: Remove ALL Zoho-specific hardcoding. The platform works with ANY MCP server — Zoho, Tally, Salesforce, Xero, custom. Everything is configured from DB.
> **Prerequisite**: Phase 1 complete (DB, Redis, auth, tenants)
> **Estimated Prompts**: 10-15

---

## Why This Phase Matters

This is what transforms the project into a product. After this phase:

- A client using Tally can plug in Tally MCP and get the same experience as Zoho
- Tool whitelists, intents, prompts, org detection — all DB-driven
- No code deploy needed to onboard a new MCP provider

---

## Tasks

### 2.1 — Dynamic Tool Registry (DB-Driven)

**Files to modify**: `app/mcp/tool_registry.py`
**Files to create**: `app/db/models/tool_config.py`

**What**:

- New DB table `tool_configs`:
  ```
  tool_configs(id, mcp_connection_id, tool_name, group_name, is_whitelisted, is_read_only, description)
  ```
- On MCP connect: auto-discover all tools from server, INSERT into `tool_configs` if not exists
- Whitelist/blacklist managed per-tenant in `mcp_connections` table
- Remove `TOOL_GROUPS` hardcoded dict entirely
- `get_for_intent()` queries DB instead of static mapping
- Tool groups (invoices, contacts, etc.) become tags, not hardcoded sets

**V1 Compat**: If no `tool_configs` rows exist, fall back to `_V1_DEFAULTS` from Phase 0.

**Acceptance**: Connect a non-Zoho MCP; its tools auto-register; whitelist from DB works.

---

### 2.2 — Dynamic Intent Patterns (DB-Driven)

**Files to modify**: `app/core/intent_router.py`
**Files to create**: `app/db/models/intent_config.py`

**What**:

- New DB table `intent_patterns`:
  ```
  intent_patterns(id, tenant_id, intent_name, regex_pattern, priority, is_active)
  ```
- `IntentRouter` loads patterns from DB (cached in Redis, 5-min TTL)
- Falls back to V1 defaults if no tenant patterns configured
- Intent is now a string, not an Enum (Enum kept as `DEFAULT_INTENTS` for fallback)
- Tenants can define custom intents: `TALLY_CRUD`, `SALESFORCE_OPS`, etc.
- Admin API: CRUD for intent patterns

**Acceptance**: Tenant creates custom intent "tally_ops" with regex; messages match it correctly.

---

### 2.3 — Dynamic Prompt Templates (DB-Driven)

**Files to modify**: `app/core/prompt_builder.py`
**Files to create**: `app/db/models/prompt_template.py`

**What**:

- New DB table `prompt_templates`:
  ```
  prompt_templates(id, tenant_id, intent, content, version, is_active)
  ```
- Template variables: `{provider_name}`, `{org_id}`, `{org_id_param}`, `{tool_list}`, `{org_name}`
- `PromptBuilder.build()` loads template from DB → renders variables → returns prompt
- Default templates seeded for common intents (CRUD, REPORT, CHAT)
- Remove all "Zoho" mentions from default prompts — use `{provider_name}` instead

**Example template**:

```
You are a concise WhatsApp assistant integrated with {provider_name}.
Use the available tools to fulfill the user's request.
Always confirm details before creating or modifying records.
The organization_id to use: {org_id}
```

**Acceptance**: Change provider_name → prompts reflect it; no "Zoho" in prompts for Tally tenant.

---

### 2.4 — MCP-Agnostic Org Detection

**Files to modify**: `app/mcp/manager.py`

**What**:

- `MCPManager` reads org discovery config from `mcp_connections` table:
  - `org_discovery_tool`: which tool to call (e.g., `ZohoBooks_list_organizations`)
  - `org_id_field`: which field in response is the ID (e.g., `organization_id`)
  - `org_name_field`: which field is the name (e.g., `name`)
- Generic `_fetch_organizations()` method:
  1. Find discovery tool from config
  2. Call it
  3. Extract orgs using configured field names
  4. Store in session context
- If no discovery config: skip org detection (some MCPs don't have multi-org)

**Acceptance**: Configure Tally org discovery (different tool name + field names); detection works.

---

### 2.5 — Multi-MCP Connection Pool

**Files to modify**: `app/mcp/client.py`, `app/mcp/manager.py`
**Files to create**: `app/mcp/connection_pool.py`

**What**:

- `MCPConnectionPool` class:
  - Manages connections to multiple MCP servers per-tenant
  - Key: `{tenant_id}:{mcp_connection_id}`
  - Lazy connect: only connects when first needed
  - Health check: periodic ping (every 60s)
  - Circuit breaker: after 3 consecutive failures, mark unhealthy, retry after 30s
  - Auto-reconnect on connection drop
- `MCPManager` becomes multi-MCP-aware:
  - `get_tools(tenant_id, intent)` → aggregate tools from ALL active MCP connections
  - Tool namespacing: optional prefix to avoid name collisions across MCPs

**Acceptance**: Tenant has 2 MCP connections; both contribute tools; one goes down → circuit breaker trips → other still works.

---

### 2.6 — Generic Agent Factory

**Files to modify**: `app/agents/zoho_crud_agent.py`, `app/agents/report_agent.py`, `app/agents/chat_agent.py`
**Files to create**: `app/agents/agent_factory.py`, `app/db/models/agent_config.py`

**What**:

- New DB table `agent_configs`:
  ```
  agent_configs(id, tenant_id, name, agent_type, description,
                mcp_connections[], allowed_intents[], llm_provider, llm_model,
                system_prompt_template_id, is_active)
  ```
- `AgentFactory.create(agent_config)` → returns configured agent instance
- Rename `ZohoCrudAgent` → `CRUDAgent` (MCP-agnostic)
- Agent type enum: `CRUD`, `REPORT`, `CHAT`, `EMAIL`, `WORKFLOW`, `SEARCH`, `NOTIFICATION`, `CUSTOM`
- Remove module-level singletons; agents created on-demand per request
- Each agent scoped to its allowed MCP connections + tools

**Acceptance**: Create agent config via admin API; it works for a non-Zoho MCP.

---

### 2.7 — Dynamic Report Collector

**Files to modify**: `app/services/report_collector.py`
**Files to create**: `app/db/models/report_definition.py`

**What**:

- New DB table `report_definitions`:
  ```
  report_definitions(id, tenant_id, name, mcp_connection_id,
                     data_sources JSONB, aggregations JSONB,
                     fiscal_year_start_month, template_id)
  ```
- `data_sources` example:
  ```json
  {
    "invoices": {
      "tool": "ZohoBooks_list_invoices",
      "date_field": "date",
      "amount_field": "total"
    },
    "bills": {
      "tool": "ZohoBooks_list_bills",
      "date_field": "date",
      "amount_field": "total"
    }
  }
  ```
- `ReportCollector.collect()` reads report definition from DB → calls configured tools → normalizes output
- Default report definitions seeded for Zoho Books (same as current V1 behavior)
- Can define new report types per-tenant for any MCP

**Acceptance**: Tenant creates custom report definition for Tally; report generates correctly.

---

### 2.8 — Re-name & Re-label Everything

**Files to modify**: `app/main.py`, `app/config.py`, `.env.example`, all agents

**What**:

- App title: `"WhatsApp Zoho MCP Bot"` → `"AgentFlow Platform"`
- Config: `mcp_zoho_url` → deprecated (moved to DB)
- Config: `zoho_org_id` → deprecated (moved to DB)
- Agent: `ZohoCrudAgent` → `CRUDAgent`
- Intent: `ZOHO_CRUD` → `CRUD` (keep `ZOHO_CRUD` as alias for backward compat)
- All log messages: remove "Zoho" references

**Acceptance**: Grep for "zoho" in non-V1-compat code → zero results.

---

### 2.9 — Admin API: MCP Connection Management

**Files to create**: `app/routes/admin/mcp_connections.py`

**What**:

- `POST /admin/tenants/{id}/mcp-connections` — add MCP connection (URL, transport, auth)
- `GET /admin/tenants/{id}/mcp-connections` — list connections
- `PUT /admin/mcp-connections/{id}` — update (URL, whitelist, etc.)
- `DELETE /admin/mcp-connections/{id}` — remove
- `POST /admin/mcp-connections/{id}/test` — test connectivity + discover tools
- `GET /admin/mcp-connections/{id}/tools` — list discovered tools
- On `test`: connect to MCP, list tools, save to `tool_configs`

**Acceptance**: Add Tally MCP via API; test discovers tools; tools appear in tool_configs.

---

### 2.10 — Admin API: Intent + Prompt + Agent + Report Config

**Files to create**:

- `app/routes/admin/intents.py`
- `app/routes/admin/prompts.py`
- `app/routes/admin/agents.py`
- `app/routes/admin/reports.py`

**What**: Full CRUD endpoints for all dynamic config tables. Each scoped to tenant via JWT.

**Acceptance**: Can configure a complete non-Zoho tenant entirely through admin API.

---

## Completion Criteria

- [ ] Zero hardcoded Zoho tool names in non-default-seed code
- [ ] Tool registry loads tools from DB per-tenant
- [ ] Intent patterns loaded from DB per-tenant
- [ ] Prompts loaded from DB per-tenant with template variables
- [ ] Org detection works for any MCP (configured via DB)
- [ ] Multiple MCPs connected simultaneously per-tenant
- [ ] Agent configs stored in DB, created via factory
- [ ] Report definitions stored in DB, customizable per-tenant
- [ ] Admin API for all dynamic configs
- [ ] V1 behavior preserved via default seeds + fallbacks
