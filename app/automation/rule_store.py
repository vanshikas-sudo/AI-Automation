"""
Rule Store — Redis-backed CRUD for automation rules.

Rules are stored as JSON in Redis:
  - Individual rule: "automation:rule:{rule_id}" → JSON string
  - Rule index set:  "automation:rules" → set of rule IDs
  - Execution log:   "automation:executions:{rule_id}" → list of TaskExecution JSON strings
"""

from __future__ import annotations

import logging
import ssl
from datetime import datetime

import redis.asyncio as aioredis

from app.automation.models import EventRule, RuleStatus, TaskExecution

logger = logging.getLogger(__name__)

RULES_INDEX_KEY = "automation:rules"
RULE_KEY_PREFIX = "automation:rule:"
EXEC_KEY_PREFIX = "automation:executions:"
MAX_EXECUTIONS_PER_RULE = 100


class RuleStore:
    """Async Redis-backed storage for automation rules and execution history."""

    def __init__(self, redis_url: str):
        kwargs = {"decode_responses": True}
        if redis_url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = ssl.CERT_NONE
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url, **kwargs
        )

    # ── Rule CRUD ────────────────────────────────────────

    async def save_rule(self, rule: EventRule) -> EventRule:
        """Create or overwrite a rule."""
        key = f"{RULE_KEY_PREFIX}{rule.id}"
        await self._redis.set(key, rule.model_dump_json())
        await self._redis.sadd(RULES_INDEX_KEY, rule.id)
        logger.info("Rule saved: %s (%s)", rule.name, rule.id)
        return rule

    async def get_rule(self, rule_id: str) -> EventRule | None:
        key = f"{RULE_KEY_PREFIX}{rule_id}"
        data = await self._redis.get(key)
        if data:
            return EventRule.model_validate_json(data)
        return None

    async def list_rules(self, status: RuleStatus | None = None) -> list[EventRule]:
        rule_ids = await self._redis.smembers(RULES_INDEX_KEY)
        rules: list[EventRule] = []
        for rule_id in rule_ids:
            rule = await self.get_rule(rule_id)
            if rule and (status is None or rule.status == status):
                rules.append(rule)
        return rules

    async def get_active_rules(self) -> list[EventRule]:
        return await self.list_rules(status=RuleStatus.ACTIVE)

    async def update_rule(self, rule: EventRule) -> EventRule:
        rule.updated_at = datetime.utcnow()
        return await self.save_rule(rule)

    async def delete_rule(self, rule_id: str) -> bool:
        key = f"{RULE_KEY_PREFIX}{rule_id}"
        deleted = await self._redis.delete(key)
        await self._redis.srem(RULES_INDEX_KEY, rule_id)
        exec_key = f"{EXEC_KEY_PREFIX}{rule_id}"
        await self._redis.delete(exec_key)
        logger.info("Rule deleted: %s (existed=%s)", rule_id, bool(deleted))
        return bool(deleted)

    async def toggle_rule(self, rule_id: str) -> EventRule | None:
        rule = await self.get_rule(rule_id)
        if not rule:
            return None
        rule.status = (
            RuleStatus.PAUSED if rule.status == RuleStatus.ACTIVE else RuleStatus.ACTIVE
        )
        return await self.update_rule(rule)

    async def mark_triggered(self, rule_id: str) -> None:
        """Update last_triggered timestamp and increment trigger_count."""
        rule = await self.get_rule(rule_id)
        if rule:
            rule.last_triggered = datetime.utcnow()
            rule.trigger_count += 1
            await self.save_rule(rule)

    # ── Execution History ────────────────────────────────

    async def log_execution(self, execution: TaskExecution) -> None:
        """Append an execution record to the rule's history (capped list)."""
        key = f"{EXEC_KEY_PREFIX}{execution.rule_id}"
        await self._redis.lpush(key, execution.model_dump_json())
        await self._redis.ltrim(key, 0, MAX_EXECUTIONS_PER_RULE - 1)

    async def get_executions(
        self, rule_id: str, limit: int = 20
    ) -> list[TaskExecution]:
        key = f"{EXEC_KEY_PREFIX}{rule_id}"
        items = await self._redis.lrange(key, 0, limit - 1)
        return [TaskExecution.model_validate_json(item) for item in items]

    # ── Lifecycle ────────────────────────────────────────

    async def close(self) -> None:
        await self._redis.aclose()
