# Phase 5 — Admin Dashboard UI (Next.js)

> **Status**: NOT STARTED
> **Goal**: A professional, modern admin dashboard where tenants manage their MCP connections, agents, automations, users, and analytics. Not an "AI-looking" theme — clean, minimal, business-grade.
> **Prerequisite**: Phase 4 complete (event engine functional)
> **Estimated Prompts**: 12-15

---

## Design Principles

- **Business-grade look**: Think Linear, Vercel Dashboard, Stripe Dashboard — not GPT-wrapper UI
- **No AI aesthetics**: No glowing gradients, no "futuristic" vibes. Clean typography, clear hierarchy, subtle colors
- **Function-first**: Every screen serves a purpose. No empty dashboards with vanity metrics
- **Mobile-responsive**: Admins may check from phone
- **Dark/Light mode**: Switch in header

---

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **UI**: Tailwind CSS + shadcn/ui
- **State**: TanStack Query (React Query) for API calls
- **Auth**: NextAuth.js with JWT (same JWT as backend)
- **Charts**: Recharts (minimal, clean)
- **Tables**: TanStack Table
- **Forms**: React Hook Form + Zod validation

---

## Tasks

### 5.1 — Next.js Project Setup

**What**:

- Initialize Next.js 14 project in `dashboard/` directory
- Configure Tailwind CSS + shadcn/ui
- Set up NextAuth.js with JWT provider (backend issues tokens)
- API wrapper: typed fetch client for backend admin API
- Docker Compose: add dashboard container
- Proxy API calls to backend via Next.js API routes (BFF pattern)

**Acceptance**: Dashboard runs at `localhost:3000`; login works with JWT.

---

### 5.2 — Layout & Navigation

**What**:

- Sidebar navigation (collapsible):
  - Dashboard (overview)
  - MCP Connections
  - Agents
  - Automations (Event Rules)
  - Users & Roles
  - Conversation History
  - Analytics
  - Settings
- Top bar: tenant name, user avatar, notifications bell, dark/light toggle
- Breadcrumbs on every page
- Mobile: sidebar becomes hamburger menu

**Acceptance**: Full navigation works; responsive on mobile.

---

### 5.3 — Dashboard Overview Page

**What**:

- KPI cards: Active MCPs, Total Agents, Active Automations, Messages Today
- Line chart: Messages per day (7-day trend)
- Bar chart: Actions per agent (this week)
- Recent activity feed: last 10 events
- Quick actions: "Add MCP Connection", "Create Automation"
- System health: MCP connection statuses (green/yellow/red dots)

**Acceptance**: Dashboard shows live data from backend API.

---

### 5.4 — MCP Connections Page

**What**:

- Table: name, provider type, URL (masked), status (healthy/unhealthy), last checked, tools count
- Actions: Edit, Test Connection, View Tools, Deactivate, Delete
- **Add Connection wizard** (multi-step modal):
  1. Select provider type (Zoho Books, Zoho CRM, Tally, Salesforce, Custom) — with logos
  2. Enter URL + authentication (API key, OAuth, etc.)
  3. Test connection → show discovered tools count
  4. Configure: whitelist/blacklist tools, set org discovery config
  5. Save → connection active
- Tool discovery view: expandable table of all tools from this MCP with toggle to enable/disable
- Health indicator: auto-refresh every 60s

**Acceptance**: Add MCP connection through wizard; see tools; toggle whitelist; health shows green.

---

### 5.5 — Agents Configuration Page

**What**:

- Table: agent name, type, LLM model, MCPs assigned, intents handled, active status
- **Create Agent form**:
  - Name, description
  - Type: CRUD, Report, Email, Search, Workflow, Notification, Custom
  - LLM provider + model selection (dropdown from available providers)
  - System prompt template (textarea with variable hints)
  - MCP connections to use (multi-select from tenant's connections)
  - Intents this agent handles (multi-select)
  - Active/inactive toggle
- Edit agent: same form, pre-filled
- Delete agent: with confirmation

**Acceptance**: Create custom agent → assign to MCP + intent → it handles matching messages.

---

### 5.6 — Automations (Event Rules) Page

**What**:

- Table: rule name, trigger type, schedule, status (active/paused), last run, successes/failures
- **Create Automation wizard**:
  1. Name & description
  2. Trigger type: Schedule (cron picker), Webhook (generate URL), Event (select event type)
  3. Conditions builder (visual):
     - Field (dropdown from MCP entities) + Operator (gt, lt, eq, contains) + Value
     - AND/OR logic between conditions
     - Add/remove conditions
  4. Actions: select agent + action + configure params (dynamic form based on agent capabilities)
  5. Preview: "This automation will check daily at 9 AM for invoices overdue by 3+ days and send reminder emails"
  6. Save
- Execution history: expandable per-rule, shows each run with status, items processed, errors
- Quick toggle: active/paused
- DLQ view: failed tasks with retry button

**Acceptance**: Create automation with visual builder → appears in list → history shows executions.

---

### 5.7 — Users & Roles Page

**What**:

- Table: name, email, phone, role, last active, status
- Invite user: email + role assignment
- Edit role: dropdown (owner, admin, user, viewer)
- Deactivate/remove user
- Phone number mapping: which WhatsApp numbers belong to this user
- Slack ID mapping: connect Slack user to platform user

**Acceptance**: Add user with phone → they can use WhatsApp bot; change role → permissions change.

---

### 5.8 — Conversation History Page

**What**:

- Filterable by: user, date range, intent, channel, agent
- Conversation thread view (like chat UI, read-only)
- Each message shows: timestamp, intent classified, agent used, tools called, tokens consumed
- Export: CSV download of conversations
- Search: full-text search across conversations

**Acceptance**: View conversation history; filter by user + date; see tool calls.

---

### 5.9 — Intent Patterns & Prompt Templates Pages

**What**:

- **Intents page**: CRUD for custom intent patterns (regex editor with test input)
- **Prompts page**: CRUD for prompt templates (Markdown editor with variable picker)
- Template variables: `{provider_name}`, `{org_id}`, `{tool_list}`, `{user_name}`, `{org_name}`
- Preview: renders template with sample data

**Acceptance**: Edit intent regex → test with sample message → matches correctly.

---

### 5.10 — Settings Page

**What**:

- Tenant settings: name, logo, timezone, fiscal year start
- LLM configuration: default provider + model, max tokens, temperature
- Notification preferences: WhatsApp/email for automation summaries
- API keys: generate/revoke for programmatic access
- Danger zone: delete tenant (with "type name to confirm")

**Acceptance**: Change timezone → scheduler uses new timezone; generate API key → works for auth.

---

### 5.11 — Analytics Page

**What**:

- Messages per day (line chart)
- Token usage per day (stacked bar: classification, tool-calling, reasoning)
- Estimated cost per day (line chart)
- Top intents (pie chart)
- Agent performance: success/failure rates
- Automation success rates
- MCP health over time

**Acceptance**: Charts show real data; filterable by date range.

---

## Completion Criteria

- [ ] Dashboard running at localhost:3000 with auth
- [ ] Full CRUD for MCP connections via UI
- [ ] Agent configuration via UI
- [ ] Automation builder (visual conditions + actions)
- [ ] User management with roles
- [ ] Conversation history with search
- [ ] Intent and prompt management
- [ ] Analytics with real data
- [ ] Settings page
- [ ] Professional business-grade design (no AI aesthetic)
- [ ] Mobile responsive
- [ ] Dark/light mode
