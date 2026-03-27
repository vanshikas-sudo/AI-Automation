# Phase 3 — Multi-Agent Collaboration & Task Planning

> **Status**: NOT STARTED
> **Goal**: Agents collaborate on complex multi-step requests. A supervisor coordinates, a planner decomposes, and agents execute in parallel where possible.
> **Prerequisite**: Phase 2 complete (MCP-agnostic core, dynamic configs)
> **Estimated Prompts**: 8-10

---

## Tasks

### 3.1 — Hybrid Intent Classifier (Regex + LLM Fallback)

**Files to modify**: `app/core/intent_router.py`

**What**:

- Phase 1: Regex patterns (from DB) — fast, zero cost
- If confidence < 0.8: Phase 2: LLM classification
- LLM classifier uses tenant's available tools + intents as context
- Returns: `ClassificationResult(intent, confidence, sub_intent, entities)`
- Entity extraction: amounts, dates, names, IDs from the message
- Cost control: use Groq (cheapest) for classification, not main LLM

**Acceptance**: Ambiguous messages ("handle overdue stuff") correctly classified via LLM fallback.

---

### 3.2 — Task Planner (DAG Decomposition)

**Files to create**:

- `app/core/task_planner.py`
- `app/models/task_dag.py`

**What**:

- `TaskPlanner.plan(message, intent, context)` → `TaskDAG`
- Uses LLM to decompose complex requests into sub-tasks
- Each sub-task has: agent_type, action, params, dependencies
- DAG validation: no cycles, all deps resolvable, tools available
- Simple requests (single intent) → single-node DAG (no overhead)
- Complex requests ("create invoice AND email it") → multi-node DAG

**TaskDAG model**:

```python
class TaskNode:
    id: str
    agent_type: str
    action: str
    params: dict
    dependencies: list[str]  # IDs of tasks that must complete first
    status: str              # pending, running, completed, failed

class TaskDAG:
    nodes: list[TaskNode]
    def get_execution_batches() -> list[list[TaskNode]]  # parallel-safe groups
    def validate() -> bool
```

**Acceptance**: "Create an invoice for SO-001 and email it" → DAG with 4 tasks, correct dependencies.

---

### 3.3 — Supervisor Agent

**Files to create**: `app/agents/supervisor_agent.py`

**What**:

- `SupervisorAgent.execute(dag, context)` → merged result
- Executes DAG in batches (parallel-safe tasks run concurrently)
- Passes output from parent tasks to dependent child tasks
- Handles failures: retry once, then mark failed + notify user
- Merges all results into a single coherent response
- Tracks execution in `task_executions` table

**Acceptance**: DAG with 4 tasks (2 parallel + 2 sequential) executes correctly in right order.

---

### 3.4 — Email Agent

**Files to create**: `app/agents/email_agent.py`

**What**:

- Sends emails via MCP (Zoho Mail MCP, or any email MCP)
- Template rendering: subject + body with variable substitution
- Supports: single send, batch send (with rate limiting)
- Attachment support: PDFs, documents from other agents' output
- Tracks: sent/failed/bounced in event_log

**Acceptance**: Supervisor delegates "send email" task to Email Agent; email sent via MCP.

---

### 3.5 — Search Agent

**Files to create**: `app/agents/search_agent.py`

**What**:

- Cross-MCP data search and aggregation
- Searches across all connected MCPs for the tenant
- Supports: keyword search, filter by field, date range
- Deduplicates results across MCPs (same contact in Books + CRM)
- Returns formatted results optimized for WhatsApp (short, tabular)

**Acceptance**: "Find all invoices over 50000" → searches across all MCPs with invoice tools.

---

### 3.6 — LLM Router (Cost Optimization)

**Files to create**: `app/providers/llm_router.py`
**Files to modify**: `app/providers/llm_factory.py`

**What**:

- `LLMRouter.route(request)` → picks best LLM per task type
- Task types: classification, tool_calling, reasoning, summarization
- Routing rules stored in DB per-tenant (with defaults)
- Fallback chains: if primary fails, try secondary
- Token budget tracking per-tenant (usage limits per plan tier)
- Cost tracking: log tokens used + estimated cost per request

**Acceptance**: Classification uses Groq; tool-calling uses Claude; costs logged.

---

### 3.7 — Confirmation Engine

**Files to create**: `app/core/confirmation_engine.py`
**Files to create**: `app/db/models/confirmation.py`

**What**:

- Before executing destructive/expensive actions: generate preview
- Send preview to user, wait for "confirm" / "cancel"
- Store pending confirmation in DB with expiry (24h)
- On next message from user: check for pending confirmation first
- Configurable: which actions always require confirmation (DB-driven)
- Auto-expire stale confirmations

**Flow**:

```
User: "Delete all overdue invoices"
→ Confirmation Engine generates preview:
  "I'll delete 12 overdue invoices totaling ₹3,45,000. This cannot be undone."
  "Reply 'confirm' to proceed or 'cancel' to abort."
→ User: "confirm"
→ Execute task
```

**Acceptance**: Destructive action pauses for confirmation; user confirms → executes; user cancels → aborts.

---

### 3.8 — Message Handler V2 (Orchestration Flow)

**Files to modify**: `app/core/message_handler.py`

**What**: Rewrite message handler to use new orchestration pipeline:

```
1. Check for pending confirmation → handle if exists
2. Classify intent (hybrid)
3. If simple (single-agent): route directly to agent
4. If complex (multi-step): Task Planner → DAG → Confirmation → Supervisor
5. Publish events for tracking
6. Return response
```

**Acceptance**: Both simple and complex messages handled correctly through new pipeline.

---

## Completion Criteria

- [ ] Hybrid intent classification working (regex + LLM)
- [ ] Task Planner decomposes complex requests into DAGs
- [ ] Supervisor Agent executes DAGs with parallel batches
- [ ] Email Agent sends emails via MCP
- [ ] Search Agent searches across MCPs
- [ ] LLM Router picks cost-optimal provider
- [ ] Confirmation Engine handles user approval for destructive ops
- [ ] End-to-end: "Create invoice for SO-001 and email it" works
