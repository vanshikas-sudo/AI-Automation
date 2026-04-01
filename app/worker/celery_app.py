"""
Celery Application — Central configuration for the task queue.

Broker + backend: Redis (as per architecture decision D3).

Start worker:
    celery -A app.worker.celery_app worker --loglevel=info --pool=solo

Start beat scheduler:
    celery -A app.worker.celery_app beat --loglevel=info

Combined (dev only):
    celery -A app.worker.celery_app worker --beat --loglevel=info --pool=solo
"""

from __future__ import annotations

import os
import ssl

from dotenv import load_dotenv
load_dotenv()

from celery import Celery
from celery.schedules import crontab

# Read Redis URL from env (same as FastAPI config, but worker is a separate process)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

# Configure SSL for rediss:// (Upstash, Redis Cloud, etc.)
_broker_ssl = None
if REDIS_URL.startswith("rediss://"):
    _broker_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}

celery_app = Celery(
    "automation",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.worker.tasks"],
)

_ssl_config = {}
if _broker_ssl:
    _ssl_config = {
        "broker_use_ssl": _broker_ssl,
        "redis_backend_use_ssl": _broker_ssl,
    }

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Timezone
    timezone=TIMEZONE,
    enable_utc=False,

    # Reliability
    task_track_started=True,
    task_acks_late=True,                # Ack only after task completes (crash-safe)
    worker_prefetch_multiplier=1,       # Fair scheduling — one task at a time per worker

    # Retry defaults
    task_default_retry_delay=60,        # 60s between retries
    task_max_retries=3,

    # Result expiry
    result_expires=86400,               # 24 hours

    # Worker pool — solo for Windows compatibility, prefork on Linux
    worker_pool="solo",

    # SSL for cloud Redis
    **_ssl_config,

    # Beat schedule — loaded dynamically, but we define a baseline heartbeat
    beat_schedule={
        "evaluate-active-rules": {
            "task": "app.worker.tasks.evaluate_all_rules",
            "schedule": crontab(minute="*/5"),  # Every 5 minutes — check which rules need firing
        },
    },
)
