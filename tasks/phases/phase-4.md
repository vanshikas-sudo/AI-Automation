# Phase 4 — Event-Driven Automation Engine

> **Status**: NOT STARTED
> **Goal**: Users can set up "when X happens, do Y" automations via WhatsApp conversation. Scheduled tasks, triggers, conditions, and actions — all managed conversationally.
> **Prerequisite**: Phase 3 complete (collaborative agents, supervisor, confirmation)
> **Estimated Prompts**: 10-12

---

## What This Unlocks

After this phase, a user can say:

- "Send an email when invoices are overdue by 3+ days" → automated daily check
- "Notify me on WhatsApp when a payment above ₹1 lakh is received" → real-time trigger
- "Generate a weekly sales report every Monday at 9 AM" → scheduled report
- "List my automations" / "Pause overdue reminder" / "Delete weekly report" → manage rules

---

## Tasks

### 4.1 — Event Bus (Redis Streams)

**Files to create**:

- `app/events/__init__.py`
- `app/events/event_bus.py`
- `app/events/models.py`

**What**:

- `EventBus` wraps Redis Streams for publish/subscribe
- Events published to streams per-tenant: `events:{tenant_id}`
- Consumer groups for each processor (agents, logger, notifier)
- Event schema:
  ```python
  class Event(BaseModel):
      event_id: str
      tenant_id: str
      event_type: str        # "invoice.overdue_check", "agent.task_completed"
      payload: dict
      source: str
      correlation_id: str
      timestamp: datetime
  ```
- Auto-log all events to `event_log` DB table (audit trail)
- Configurable retention: 7 days default

**Acceptance**: Publish event → consumer receives it → logged to DB.

---

### 4.2 — Event Rule Model + CRUD API

**Files to create**:

- `app/db/models/event_rule.py`
- `app/routes/admin/event_rules.py`

**Schema** (from v2-architecture.md):

```python
class EventRule:
    id, tenant_id, created_by
    name: str                    # "Overdue Invoice Reminder"
    description: str
    trigger_config: JSONB        # {type: "schedule", schedule: "0 9 * * *"}
    conditions: JSONB            # [{field: "days_overdue", operator: "gt", value: 3}]
    actions: JSONB               # [{agent: "email", action: "send", params: {...}}]
    is_active: bool
    last_triggered, trigger_count
    created_at, updated_at
```

**API endpoints**:

- `POST /admin/tenants/{id}/event-rules` — create rule
- `GET /admin/tenants/{id}/event-rules` — list rules
- `PUT /admin/event-rules/{id}` — update
- `DELETE /admin/event-rules/{id}` — delete
- `POST /admin/event-rules/{id}/toggle` — activate/deactivate
- `GET /admin/event-rules/{id}/history` — execution history

**Acceptance**: Can CRUD event rules via API; rules stored in DB with all fields.

---

### 4.3 — Natural Language → Event Rule Parser

**Files to create**: `app/core/event_rule_parser.py`

**What**:

- LLM-powered parser: takes user message → structured EventRule
- Input: "Send email when invoices overdue by more than 3 days"
- Output:
  ```json
  {
    "name": "Overdue Invoice Email Reminder",
    "trigger": { "type": "schedule", "schedule": "0 9 * * *" },
    "conditions": [{ "field": "days_overdue", "operator": "gt", "value": 3 }],
    "actions": [
      {
        "agent": "email",
        "action": "send_reminder",
        "params": { "template": "overdue" }
      }
    ]
  }
  ```
- Validates: all referenced tools/agents exist for tenant
- Handles ambiguity: asks clarifying questions if needed
  - "How often should I check? Daily at 9 AM?"
  - "Which email template? Professional reminder or friendly nudge?"
- Integration: works through the Confirmation Engine (user approves before saving)

**Acceptance**: User describes automation in plain English → structured rule generated → confirmed → saved.

---

### 4.4 — Scheduler Service (Celery Beat)

**Files to create**: `app/worker/scheduler.py`
**Files to modify**: `app/worker/celery_app.py`

**What**:

- Dynamic Celery Beat schedule loaded from `event_rules` table
- On app start: load all active rules with `trigger.type == "schedule"`
- Register each as Celery periodic task
- On rule create/update/delete: update Beat schedule dynamically
- Cron support: `"0 9 * * *"` (every day at 9 AM), `"0 9 * * 1"` (every Monday)
- Interval support: `"every 1 hour"`, `"every 30 minutes"`
- Timezone-aware: respect tenant's timezone

**Acceptance**: Create rule with daily schedule → Celery Beat fires task daily → task appears in task_executions.

---

### 4.5 — Trigger Engine (Condition Evaluator)

**Files to create**: `app/events/trigger_engine.py`

**What**:

- `TriggerEngine.evaluate(rule, tenant_context)` → list of matching items
- Steps:
  1. Identify data source from rule actions (which MCP, which tool to list data)
  2. Fetch data via MCP tool call
  3. Evaluate conditions against each item in result
  4. Return items that match ALL conditions
- Operators: `gt`, `gte`, `lt`, `lte`, `eq`, `neq`, `contains`, `not_contains`, `in`, `between`
- Computed fields: `days_overdue` = (today - due_date).days
- Field paths support: `contact.email`, `line_items[0].amount` (nested access)

**Acceptance**: Rule with "days_overdue > 3" → fetches invoices → correctly filters to overdue ones.

---

### 4.6 — Action Executor

**Files to create**: `app/events/action_executor.py`

**What**:

- Takes matched items + action config → creates agent tasks
- For each matching item: creates a `TaskNode` for the appropriate agent
- Queues tasks via Celery for async execution
- Batching: groups related tasks for efficiency (e.g., 50 emails = 1 batch job)
- Rate limiting: respects MCP server limits (configurable per-connection)
- Result tracking: updates `task_executions` table with status

**Acceptance**: Trigger finds 5 overdue invoices → 5 email tasks queued → all execute successfully.

---

### 4.7 — Dead Letter Queue (Failed Task Recovery)

**Files to create**: `app/events/dlq.py`

**What**:

- Tasks that fail after max retries (3 by default) → moved to DLQ
- DLQ stored in Redis list: `dlq:{tenant_id}`
- Also logged to `task_executions` with status="failed"
- Admin API: view DLQ items, retry individual items, purge
- Automatic notification: when DLQ has items, notify tenant admin

**Acceptance**: Deliberately fail a task 3 times → appears in DLQ → manual retry succeeds.

---

### 4.8 — WhatsApp Automation Management (Conversational)

**Files to modify**: `app/core/message_handler.py`, `app/core/intent_router.py`

**What**: New intents for automation management via WhatsApp:

- "List my automations" → shows active rules with status
- "Pause overdue reminder" → deactivates rule
- "Resume overdue reminder" → reactivates
- "Delete weekly report" → removes rule (with confirmation)
- "Edit overdue threshold to 5 days" → updates condition value
- "Show automation history" → last 10 executions with results
- "Set up an automation" → enters guided setup flow

**Acceptance**: Full automation lifecycle manageable via WhatsApp conversation.

---

### 4.9 — Execution Summary Notifications

**Files to create**: `app/agents/notification_agent.py`

**What**:

- After scheduled automation runs, send summary to user
- Example:
  ```
  📊 Daily Overdue Check Complete (9:00 AM):
  • 5 overdue invoices found (>3 days)
  • 5 reminder emails sent ✅
  • 0 failures
  • Total outstanding: ₹2,34,500
  ```
- Configurable: send summary always, only on failures, or never
- Channels: WhatsApp, email, or both (per rule config)

**Acceptance**: Scheduled rule executes → summary sent to user on WhatsApp.

---

### 4.10 — Webhook Triggers (External Events)

**Files to create**: `app/routes/webhooks/triggers.py`

**What**:

- Each event rule can optionally have a webhook trigger URL
- `POST /webhooks/trigger/{rule_id}` → evaluates and executes rule
- HMAC signature verification (webhook secret per-rule)
- Use case: Zoho webhook fires when invoice created → triggers our automation
- Use case: External system notifies → our platform reacts

**Acceptance**: POST to webhook trigger URL → rule evaluates → actions execute.

---

## Completion Criteria

- [ ] Event Bus publishes and consumes events
- [ ] Event rules CRUD via admin API
- [ ] Natural language → structured event rule parser
- [ ] Scheduler fires rules on cron schedule
- [ ] Trigger engine evaluates conditions against MCP data
- [ ] Actions execute via agent system
- [ ] DLQ catches failed tasks with retry capability
- [ ] WhatsApp: "list automations", "pause X", "set up automation"
- [ ] Execution summaries sent to users
- [ ] End-to-end: "email when overdue >3 days" → daily automated execution
