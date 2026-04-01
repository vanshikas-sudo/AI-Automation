"""
Action Executor — Dispatches matched data items to Celery task queue.

When a rule fires (and its conditions match), the action executor:
  1. Creates a JobPayload for each (matched_item × action) pair
  2. Enqueues each job as a Celery task
  3. Logs the dispatch for audit

This is the bridge between the trigger engine and the worker tasks.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.automation.models import ActionConfig, EventRule, JobPayload, JobStatus

logger = logging.getLogger(__name__)


def build_jobs(
    rule: EventRule,
    matched_items: list[dict],
) -> list[JobPayload]:
    """
    Create JobPayload objects for every (matched_item × action) combination.

    For schedule-only rules with no conditions (e.g. "daily report at 9 PM"),
    matched_items will be [{}] — one empty-data job per action.

    If an action has params.aggregate=True, all matched items are bundled
    into a single job with matched_data={"_items": [...], "_count": N}.
    """
    if not matched_items:
        matched_items = [{}]

    jobs: list[JobPayload] = []
    for action in rule.actions:
        if action.params.get("aggregate"):
            # Single job with all items bundled
            job = JobPayload(
                rule_id=rule.id,
                rule_name=rule.name,
                action=action,
                matched_data={"_items": matched_items, "_count": len(matched_items)},
            )
            jobs.append(job)
        else:
            for item in matched_items:
                job = JobPayload(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    action=action,
                    matched_data=item,
                )
                jobs.append(job)

    logger.info(
        "Built %d job(s) for rule '%s': %d items × %d actions",
        len(jobs), rule.name, len(matched_items), len(rule.actions),
    )
    return jobs


def dispatch_jobs(jobs: list[JobPayload]) -> list[str]:
    """
    Enqueue jobs to the Celery task queue.

    Returns list of Celery task IDs for tracking.

    Import is deferred to avoid circular imports and allow the FastAPI
    process to enqueue without running as a Celery worker.
    """
    from app.worker.tasks import execute_job

    task_ids: list[str] = []
    for job in jobs:
        result = execute_job.apply_async(
            kwargs={"job_payload": job.model_dump(mode="json")},
            task_id=job.job_id,
            retry=True,
            retry_policy={
                "max_retries": job.max_retries,
                "interval_start": 10,
                "interval_step": 30,
                "interval_max": 300,
            },
        )
        task_ids.append(result.id)
        logger.info(
            "Job enqueued: %s (rule=%s, action=%s)",
            job.job_id, job.rule_name, job.action.type,
        )

    return task_ids
