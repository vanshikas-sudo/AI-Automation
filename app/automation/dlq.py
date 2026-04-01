"""
Dead Letter Queue — Handles jobs that exhaust all retries.

Failed jobs are moved to a Redis list keyed by "dlq:jobs".
Provides listing, retry, and purge functionality.
"""

from __future__ import annotations

import logging
import ssl
from datetime import datetime

import redis.asyncio as aioredis

from app.automation.models import JobPayload, JobStatus

logger = logging.getLogger(__name__)

DLQ_KEY = "automation:dlq:jobs"
DLQ_MAX_SIZE = 1000


class DeadLetterQueue:
    """Async Redis-backed DLQ for failed automation jobs."""

    def __init__(self, redis_url: str):
        kwargs = {"decode_responses": True}
        if redis_url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = ssl.CERT_NONE
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url, **kwargs
        )

    async def push(self, job: JobPayload) -> None:
        """Move a failed job to the DLQ."""
        job.status = JobStatus.DEAD
        job.completed_at = datetime.utcnow()
        await self._redis.lpush(DLQ_KEY, job.model_dump_json())
        await self._redis.ltrim(DLQ_KEY, 0, DLQ_MAX_SIZE - 1)
        logger.warning(
            "Job moved to DLQ: %s (rule=%s, error=%s)",
            job.job_id, job.rule_name, job.error,
        )

    async def list_jobs(self, limit: int = 50) -> list[JobPayload]:
        """List jobs in the DLQ (newest first)."""
        items = await self._redis.lrange(DLQ_KEY, 0, limit - 1)
        return [JobPayload.model_validate_json(item) for item in items]

    async def size(self) -> int:
        return await self._redis.llen(DLQ_KEY)

    async def retry_job(self, job_id: str) -> bool:
        """
        Find a job in the DLQ by ID, remove it, and re-enqueue it.

        Returns True if the job was found and re-enqueued.
        """
        items = await self._redis.lrange(DLQ_KEY, 0, -1)
        for raw in items:
            job = JobPayload.model_validate_json(raw)
            if job.job_id == job_id:
                await self._redis.lrem(DLQ_KEY, 1, raw)
                # Reset state and re-enqueue
                job.status = JobStatus.QUEUED
                job.retries = 0
                job.error = None
                job.completed_at = None
                from app.automation.action_executor import dispatch_jobs
                dispatch_jobs([job])
                logger.info("DLQ job retried: %s", job_id)
                return True
        return False

    async def purge(self) -> int:
        """Clear all DLQ entries. Returns count of purged items."""
        count = await self._redis.llen(DLQ_KEY)
        await self._redis.delete(DLQ_KEY)
        logger.info("DLQ purged: %d jobs removed", count)
        return count

    async def close(self) -> None:
        await self._redis.aclose()
