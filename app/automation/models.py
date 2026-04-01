"""
Automation Models — Pydantic schemas for rules, jobs, and task executions.

These are the data contracts for the automation engine. No database dependency —
rules and jobs are stored in Redis.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────


class TriggerType(str, Enum):
    SCHEDULE = "schedule"   # Pure cron — fires at a fixed time (e.g. "daily report at 9 PM")
    POLLING = "polling"     # Periodic check — fires cron, evaluates conditions on fetched data


class RuleStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"           # Moved to DLQ after max retries exhausted


# ── Sub-models ───────────────────────────────────────────


class Condition(BaseModel):
    """A single filter condition evaluated against fetched data items."""
    field: str                          # e.g. "days_overdue", "total", "status"
    operator: str                       # gt, gte, lt, lte, eq, neq, contains, not_contains
    value: Any                          # comparison value


class ActionConfig(BaseModel):
    """Describes a single action to perform when a rule fires."""
    type: str                           # "send_whatsapp", "send_email", "generate_report"
    params: dict[str, Any] = {}         # action-specific parameters


class TriggerConfig(BaseModel):
    """How and when a rule should fire."""
    type: TriggerType
    schedule: str | None = None              # Cron expression: "0 21 * * *" (9 PM daily)
    interval_seconds: int | None = None      # Polling interval (for POLLING type fallback)
    data_source: str | None = None           # MCP tool name to fetch data (e.g. "ZohoBooks_list_invoices")
    data_source_params: dict[str, Any] = {}  # Args passed to the data source tool


# ── Core Models ──────────────────────────────────────────


class EventRule(BaseModel):
    """
    An automation rule: trigger + conditions + actions.

    Examples:
      - Schedule: "Send a daily sales report at 9 PM"
        trigger = {type: "schedule", schedule: "0 21 * * *"}
        conditions = []
        actions = [{type: "generate_report", params: {report_type: "daily_sales"}}]

      - Polling: "Send email when invoice overdue > 30 days"
        trigger = {type: "polling", schedule: "0 9 * * *", data_source: "ZohoBooks_list_invoices"}
        conditions = [{field: "days_overdue", operator: "gt", value: 30}]
        actions = [{type: "send_email", params: {template: "overdue_reminder"}}]
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    org_id: str = ""  # Zoho org ID — resolved dynamically at rule creation time
    trigger: TriggerConfig
    conditions: list[Condition] = []
    actions: list[ActionConfig]
    status: RuleStatus = RuleStatus.ACTIVE
    last_triggered: datetime | None = None
    trigger_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class JobPayload(BaseModel):
    """
    A single unit of work enqueued for a worker to process.

    One rule firing can produce many jobs (one per matched data item per action).
    """
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rule_id: str
    rule_name: str
    action: ActionConfig
    matched_data: dict[str, Any] = {}   # The data item that triggered this
    status: JobStatus = JobStatus.QUEUED
    retries: int = 0
    max_retries: int = 3
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


class TaskExecution(BaseModel):
    """Audit log entry for a completed (or failed) job execution."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rule_id: str
    rule_name: str
    job_id: str
    status: JobStatus
    action_type: str
    result: dict[str, Any] | None = None
    error: str | None = None
    retries: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
