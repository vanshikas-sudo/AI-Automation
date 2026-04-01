"""
Automation Routes — REST API for managing automation rules and monitoring jobs.

Endpoints:
  POST   /automation/rules          — Create a new rule
  GET    /automation/rules          — List all rules (optional ?status= filter)
  GET    /automation/rules/{id}     — Get rule details
  PUT    /automation/rules/{id}     — Update a rule
  DELETE /automation/rules/{id}     — Delete a rule
  POST   /automation/rules/{id}/toggle  — Activate/deactivate
  POST   /automation/rules/{id}/trigger — Manually trigger a rule
  GET    /automation/rules/{id}/history — Execution history

  GET    /automation/dlq            — List dead-letter jobs
  POST   /automation/dlq/{job_id}/retry — Retry a DLQ job
  DELETE /automation/dlq            — Purge DLQ

  GET    /automation/health         — Queue + scheduler health
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.automation.models import (
    ActionConfig,
    Condition,
    EventRule,
    RuleStatus,
    TriggerConfig,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/automation", tags=["automation"])


# ── Request Schemas ──────────────────────────────────────


class CreateRuleRequest(BaseModel):
    name: str
    description: str = ""
    org_id: str = ""  # Zoho org ID — resolved dynamically if empty
    trigger: TriggerConfig
    conditions: list[Condition] = []
    actions: list[ActionConfig]


class UpdateRuleRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger: TriggerConfig | None = None
    conditions: list[Condition] | None = None
    actions: list[ActionConfig] | None = None


# ── Dependency helpers ───────────────────────────────────


def _get_rule_store(request: Request):
    store = getattr(request.app.state, "rule_store", None)
    if not store:
        raise HTTPException(status_code=503, detail="Automation engine not initialized")
    return store


def _get_dlq(request: Request):
    dlq = getattr(request.app.state, "dlq", None)
    if not dlq:
        raise HTTPException(status_code=503, detail="DLQ not initialized")
    return dlq


# ── Rule CRUD ────────────────────────────────────────────


@router.post("/rules", status_code=201)
async def create_rule(body: CreateRuleRequest, request: Request):
    """Create a new automation rule."""
    store = _get_rule_store(request)
    # Resolve org_id: from request body, or from MCP manager auto-detection
    org_id = body.org_id
    if not org_id:
        mcp_mgr = getattr(request.app.state, "mcp_manager", None)
        if mcp_mgr and getattr(mcp_mgr, "zoho_org_id", None):
            org_id = mcp_mgr.zoho_org_id

    rule = EventRule(
        name=body.name,
        description=body.description,
        org_id=org_id,
        trigger=body.trigger,
        conditions=body.conditions,
        actions=body.actions,
    )
    saved = await store.save_rule(rule)
    logger.info("Rule created: %s (%s)", saved.name, saved.id)
    return saved.model_dump(mode="json")


@router.get("/rules")
async def list_rules(
    request: Request,
    status: RuleStatus | None = Query(None),
):
    """List all automation rules, optionally filtered by status."""
    store = _get_rule_store(request)
    rules = await store.list_rules(status=status)
    return [r.model_dump(mode="json") for r in rules]


@router.get("/rules/{rule_id}")
async def get_rule(rule_id: str, request: Request):
    store = _get_rule_store(request)
    rule = await store.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule.model_dump(mode="json")


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: str, body: UpdateRuleRequest, request: Request):
    """Update an existing rule (partial update — only provided fields)."""
    store = _get_rule_store(request)
    rule = await store.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if body.name is not None:
        rule.name = body.name
    if body.description is not None:
        rule.description = body.description
    if body.trigger is not None:
        rule.trigger = body.trigger
    if body.conditions is not None:
        rule.conditions = body.conditions
    if body.actions is not None:
        rule.actions = body.actions

    updated = await store.update_rule(rule)
    return updated.model_dump(mode="json")


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, request: Request):
    store = _get_rule_store(request)
    deleted = await store.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "deleted", "rule_id": rule_id}


@router.post("/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: str, request: Request):
    """Toggle a rule between active and paused."""
    store = _get_rule_store(request)
    rule = await store.toggle_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"rule_id": rule.id, "status": rule.status.value}


@router.post("/rules/{rule_id}/trigger")
async def trigger_rule(rule_id: str, request: Request):
    """Manually trigger a rule (bypasses schedule check)."""
    store = _get_rule_store(request)
    rule = await store.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    try:
        from app.worker.tasks import evaluate_single_rule
        result = evaluate_single_rule.delay(rule_id)
        return {
            "status": "triggered",
            "rule_id": rule_id,
            "task_id": result.id,
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot dispatch to queue (Redis/Celery unavailable): {e}",
        )


@router.get("/rules/{rule_id}/history")
async def get_rule_history(
    rule_id: str,
    request: Request,
    limit: int = Query(20, le=100),
):
    """Get execution history for a rule."""
    store = _get_rule_store(request)
    rule = await store.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    executions = await store.get_executions(rule_id, limit=limit)
    return {
        "rule_id": rule_id,
        "rule_name": rule.name,
        "total_triggers": rule.trigger_count,
        "last_triggered": rule.last_triggered.isoformat() if rule.last_triggered else None,
        "executions": [e.model_dump(mode="json") for e in executions],
    }


# ── Dead Letter Queue ────────────────────────────────────


@router.get("/dlq")
async def list_dlq(
    request: Request,
    limit: int = Query(50, le=200),
):
    """List jobs in the dead-letter queue."""
    dlq = _get_dlq(request)
    jobs = await dlq.list_jobs(limit=limit)
    count = await dlq.size()
    return {
        "total": count,
        "jobs": [j.model_dump(mode="json") for j in jobs],
    }


@router.post("/dlq/{job_id}/retry")
async def retry_dlq_job(job_id: str, request: Request):
    """Retry a specific DLQ job."""
    dlq = _get_dlq(request)
    retried = await dlq.retry_job(job_id)
    if not retried:
        raise HTTPException(status_code=404, detail="Job not found in DLQ")
    return {"status": "retried", "job_id": job_id}


@router.delete("/dlq")
async def purge_dlq(request: Request):
    """Purge all jobs from the DLQ."""
    dlq = _get_dlq(request)
    count = await dlq.purge()
    return {"status": "purged", "count": count}


# ── Health / Status ──────────────────────────────────────


@router.get("/health")
async def automation_health(request: Request):
    """Automation engine health check."""
    store = getattr(request.app.state, "rule_store", None)
    dlq = getattr(request.app.state, "dlq", None)

    rule_count = 0
    active_count = 0
    dlq_count = 0

    if store:
        all_rules = await store.list_rules()
        rule_count = len(all_rules)
        active_count = sum(1 for r in all_rules if r.status == RuleStatus.ACTIVE)

    if dlq:
        dlq_count = await dlq.size()

    # Check Celery worker status (may fail if Redis broker is unavailable)
    workers = {}
    try:
        from app.worker.celery_app import celery_app
        inspector = celery_app.control.inspect(timeout=2)
        workers = inspector.ping() or {}
    except Exception:
        pass

    return {
        "status": "ok" if workers else "degraded",
        "rules_total": rule_count,
        "rules_active": active_count,
        "dlq_size": dlq_count,
        "workers_online": len(workers),
        "worker_names": list(workers.keys()),
    }
