"""
In-Memory Store — Development fallback when Redis is unavailable.

Provides the same interface as RuleStore and DeadLetterQueue but uses
plain Python dicts/lists. Data is lost on restart — for dev/testing only.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from app.automation.models import (
    EventRule,
    JobPayload,
    JobStatus,
    RuleStatus,
    TaskExecution,
)

logger = logging.getLogger(__name__)

MAX_EXECUTIONS_PER_RULE = 100
DLQ_MAX_SIZE = 1000


class InMemoryRuleStore:
    """Dict-backed rule store for development without Redis."""

    def __init__(self):
        self._rules: dict[str, EventRule] = {}
        self._executions: dict[str, list[str]] = defaultdict(list)  # rule_id → [json strings]
        logger.warning("Using IN-MEMORY rule store — data will be lost on restart")

    async def save_rule(self, rule: EventRule) -> EventRule:
        self._rules[rule.id] = rule
        logger.info("Rule saved: %s (%s)", rule.name, rule.id)
        return rule

    async def get_rule(self, rule_id: str) -> EventRule | None:
        return self._rules.get(rule_id)

    async def list_rules(self, status: RuleStatus | None = None) -> list[EventRule]:
        rules = list(self._rules.values())
        if status is not None:
            rules = [r for r in rules if r.status == status]
        return rules

    async def get_active_rules(self) -> list[EventRule]:
        return await self.list_rules(status=RuleStatus.ACTIVE)

    async def update_rule(self, rule: EventRule) -> EventRule:
        rule.updated_at = datetime.utcnow()
        return await self.save_rule(rule)

    async def delete_rule(self, rule_id: str) -> bool:
        existed = rule_id in self._rules
        self._rules.pop(rule_id, None)
        self._executions.pop(rule_id, None)
        logger.info("Rule deleted: %s (existed=%s)", rule_id, existed)
        return existed

    async def toggle_rule(self, rule_id: str) -> EventRule | None:
        rule = self._rules.get(rule_id)
        if not rule:
            return None
        rule.status = (
            RuleStatus.PAUSED if rule.status == RuleStatus.ACTIVE else RuleStatus.ACTIVE
        )
        return await self.update_rule(rule)

    async def mark_triggered(self, rule_id: str) -> None:
        rule = self._rules.get(rule_id)
        if rule:
            rule.last_triggered = datetime.utcnow()
            rule.trigger_count += 1

    async def log_execution(self, execution: TaskExecution) -> None:
        self._executions[execution.rule_id].insert(0, execution.model_dump_json())
        self._executions[execution.rule_id] = self._executions[execution.rule_id][:MAX_EXECUTIONS_PER_RULE]

    async def get_executions(self, rule_id: str, limit: int = 20) -> list[TaskExecution]:
        items = self._executions.get(rule_id, [])[:limit]
        return [TaskExecution.model_validate_json(item) for item in items]

    async def close(self) -> None:
        pass


class InMemoryDeadLetterQueue:
    """List-backed DLQ for development without Redis."""

    def __init__(self):
        self._jobs: list[str] = []  # JSON strings
        logger.warning("Using IN-MEMORY DLQ — data will be lost on restart")

    async def push(self, job: JobPayload) -> None:
        job.status = JobStatus.DEAD
        job.completed_at = datetime.utcnow()
        self._jobs.insert(0, job.model_dump_json())
        self._jobs = self._jobs[:DLQ_MAX_SIZE]
        logger.warning("Job moved to DLQ: %s (rule=%s)", job.job_id, job.rule_name)

    async def list_jobs(self, limit: int = 50) -> list[JobPayload]:
        return [JobPayload.model_validate_json(j) for j in self._jobs[:limit]]

    async def size(self) -> int:
        return len(self._jobs)

    async def retry_job(self, job_id: str) -> bool:
        for i, raw in enumerate(self._jobs):
            job = JobPayload.model_validate_json(raw)
            if job.job_id == job_id:
                self._jobs.pop(i)
                job.status = JobStatus.QUEUED
                job.retries = 0
                job.error = None
                job.completed_at = None
                logger.info("DLQ job retried (in-memory): %s", job_id)
                return True
        return False

    async def purge(self) -> int:
        count = len(self._jobs)
        self._jobs.clear()
        return count

    async def close(self) -> None:
        pass
