"""
Celery Tasks — The actual job definitions that workers execute.

Tasks:
  - evaluate_all_rules:  Periodic (Beat) — checks which rules should fire now.
  - evaluate_single_rule: Evaluate one rule — fetch data, check conditions, dispatch actions.
  - execute_job:          Process a single action job (send email, WhatsApp, report, etc.).

Workers are separate processes. They create their own connections (Redis, MCP, HTTP)
rather than sharing state with the FastAPI process.

Stability fixes (v1.1):
  - Single event loop per worker (no new loops/thread pools per call)
  - Shared httpx client pool (no socket exhaustion)
  - Shared Redis connection (no connection pool exhaustion)
  - SSE stream timeout (no indefinite hangs)
  - org_id injected dynamically from rule into every MCP call
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

import httpx
from celery import shared_task

from app.automation.models import (
    ActionConfig,
    EventRule,
    JobPayload,
    JobStatus,
    RuleStatus,
    TaskExecution,
    TriggerType,
)
from app.automation.trigger_engine import evaluate_conditions, parse_mcp_response
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MCP_URL = os.getenv("MCP_ZOHO_URL", "")
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_API_URL = os.getenv("WHATSAPP_API_URL", "https://graph.facebook.com/v21.0")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

# SSE stream read timeout — prevents indefinite hangs
SSE_TIMEOUT_SECONDS = 90


# ── Stable async runner ──────────────────────────────────
# One dedicated event loop for the entire worker process.
# This avoids creating/destroying loops per task which corrupts state.

_worker_loop: asyncio.AbstractEventLoop | None = None


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    """Get or create the single worker event loop."""
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop


def _run_async(coro):
    """Run async code from sync Celery task context using a single, stable event loop."""
    loop = _get_worker_loop()
    return loop.run_until_complete(coro)


# ── Shared connection pools (module-level, reused across tasks) ──

_http_client: httpx.AsyncClient | None = None
_whatsapp_client: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    """Reusable httpx client for MCP calls — avoids socket exhaustion."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=120.0)
    return _http_client


async def _get_whatsapp_client() -> httpx.AsyncClient:
    """Reusable httpx client for WhatsApp API calls."""
    global _whatsapp_client
    if _whatsapp_client is None or _whatsapp_client.is_closed:
        _whatsapp_client = httpx.AsyncClient(timeout=30.0)
    return _whatsapp_client


# Shared rule store — created once, reused
_rule_store = None


async def _get_rule_store():
    """Get a shared RuleStore instance (single Redis connection)."""
    global _rule_store
    if _rule_store is None:
        from app.automation.rule_store import RuleStore
        _rule_store = RuleStore(REDIS_URL)
    return _rule_store


async def _get_dlq():
    """Create a DLQ instance."""
    from app.automation.dlq import DeadLetterQueue
    return DeadLetterQueue(REDIS_URL)


# ── Dynamic org_id injection ─────────────────────────────

def _inject_org_id(params: dict | None, org_id: str) -> dict:
    """
    Inject organization_id into MCP tool params if the rule carries one.
    This is the KEY fix: org_id is resolved at rule creation time (from user session)
    and stored on the rule. At execution time, we inject it into every MCP call.
    """
    if not org_id:
        return params or {}

    params = dict(params or {})

    # Handle nested query_params structure (Zoho MCP tools expect this)
    if "query_params" in params:
        qp = dict(params["query_params"])
        if not qp.get("organization_id"):
            qp["organization_id"] = org_id
        params["query_params"] = qp
    else:
        # Top-level injection for tools that take organization_id directly
        if not params.get("organization_id"):
            params["organization_id"] = org_id

    return params


# ── Org resolution for rules (fetches dynamically if not stored) ──

async def _resolve_org_id_for_rule(rule: EventRule) -> str:
    """
    Resolve the org_id for a rule. Priority:
      1. rule.org_id (set at creation time from user session)
      2. Fetch from MCP via ZohoBooks_list_organizations (single-org auto-select)
      3. Empty string (will let MCP use its default)
    """
    if rule.org_id:
        return rule.org_id

    # Try to auto-detect from MCP (only if single org available)
    try:
        raw = await _call_mcp_tool_raw("ZohoBooks_list_organizations", {"query_params": {}})
        data = json.loads(raw) if isinstance(raw, str) else raw
        orgs = []
        if isinstance(data, dict):
            orgs = data.get("organizations", [])
        if len(orgs) == 1:
            org_id = str(orgs[0].get("organization_id", ""))
            logger.info("Auto-detected single org for rule '%s': %s", rule.name, org_id)
            return org_id
        if len(orgs) > 1:
            logger.warning(
                "Rule '%s' has no org_id and %d orgs available. "
                "Cannot auto-select. Rule will use MCP default.",
                rule.name, len(orgs),
            )
    except Exception as e:
        logger.warning("Failed to auto-detect org for rule '%s': %s", rule.name, e)

    return ""


# ── MCP Tool Calling ─────────────────────────────────────

async def _call_mcp_tool_raw(tool_name: str, params: dict | None = None) -> str:
    """
    Call an MCP tool via HTTP POST using streamable_http protocol.
    Uses a shared httpx client to avoid socket exhaustion.
    """
    if not MCP_URL:
        raise RuntimeError("MCP_ZOHO_URL not configured — set it in .env")

    client = await _get_http_client()
    try:
        # Step 1: Initialize session
        init_payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "celery-worker", "version": "1.0"},
            },
            "id": 1,
        }
        init_resp = await client.post(MCP_URL, json=init_payload)
        init_resp.raise_for_status()
        session_id = init_resp.headers.get("mcp-session-id", "")

        headers = {}
        if session_id:
            headers["mcp-session-id"] = session_id

        # Step 2: Send initialized notification
        notif_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        await client.post(MCP_URL, json=notif_payload, headers=headers)

        # Step 3: Call the tool
        tool_payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": params or {},
            },
            "id": 2,
        }
        resp = await client.post(MCP_URL, json=tool_payload, headers=headers)
        resp.raise_for_status()

        # Handle 202 Accepted — result comes via SSE stream
        if resp.status_code == 202:
            logger.info("MCP tool %s returned 202, reading SSE stream...", tool_name)
            return await _read_mcp_sse_result(client, headers)

        # Direct JSON response (200)
        raw_text = resp.text
        content_type = resp.headers.get("content-type", "")
        logger.info(
            "MCP tool %s response (%d bytes, ct=%s): %s",
            tool_name, len(raw_text), content_type, raw_text[:500],
        )

        # Some MCP servers return SSE even on 200
        if "event-stream" in content_type or raw_text.strip().startswith("event:"):
            return _parse_sse_text(raw_text)

        try:
            result = resp.json()
        except Exception:
            return _parse_sse_text(raw_text)
        return _extract_mcp_result(result)

    except Exception as e:
        logger.error("MCP tool call failed (%s): %s", tool_name, e)
        raise


async def _call_mcp_tool(tool_name: str, params: dict | None = None, org_id: str = "") -> str:
    """Call MCP tool with automatic org_id injection."""
    final_params = _inject_org_id(params, org_id)
    return await _call_mcp_tool_raw(tool_name, final_params)


async def _read_mcp_sse_result(client: httpx.AsyncClient, headers: dict) -> str:
    """Read the result from an SSE stream after a 202 response, with timeout."""
    sse_headers = {**headers, "Accept": "text/event-stream"}
    try:
        async with asyncio.timeout(SSE_TIMEOUT_SECONDS):
            async with client.stream("GET", MCP_URL, headers=sse_headers) as stream:
                collected = []
                async for line in stream.aiter_lines():
                    line = line.strip()
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                            if data.get("id") == 2 and "result" in data:
                                return _extract_mcp_result(data)
                            if data.get("id") == 2 and "error" in data:
                                raise RuntimeError(f"MCP error: {data['error']}")
                        except json.JSONDecodeError:
                            collected.append(data_str)
                return "\n".join(collected) if collected else ""
    except TimeoutError:
        logger.error("SSE stream timed out after %ds", SSE_TIMEOUT_SECONDS)
        raise RuntimeError(f"MCP SSE stream timed out after {SSE_TIMEOUT_SECONDS}s")


def _extract_mcp_result(result: dict) -> str:
    """Extract text content from a JSON-RPC MCP response."""
    if "result" in result:
        content = result["result"]
        if isinstance(content, dict) and "content" in content:
            for block in content["content"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block["text"]
        if isinstance(content, str):
            return content
        return json.dumps(content)
    return json.dumps(result)


def _parse_sse_text(text: str) -> str:
    """Parse SSE-formatted text response to extract JSON-RPC result."""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data_str = line[6:]
            try:
                data = json.loads(data_str)
                if "result" in data:
                    return _extract_mcp_result(data)
            except json.JSONDecodeError:
                continue
    return text


# ── Beat Task: Evaluate all active rules ─────────────────

@celery_app.task(name="app.worker.tasks.evaluate_all_rules", bind=True)
def evaluate_all_rules(self):
    """
    Periodic task (fired by Beat every 5 min).
    Loads all active rules from Redis. For each rule whose schedule matches
    "now", dispatches evaluate_single_rule.
    """
    logger.info("Beat: evaluating active rules...")

    async def _run():
        store = await _get_rule_store()
        try:
            rules = await store.get_active_rules()
        except Exception as e:
            logger.error("Beat: failed to load rules from Redis: %s", e)
            return {"status": "error", "error": str(e)}

        now = datetime.now(ZoneInfo(TIMEZONE))
        dispatched = 0
        for rule in rules:
            try:
                if _should_fire(rule, now):
                    logger.info("Rule '%s' should fire — dispatching evaluation", rule.name)
                    evaluate_single_rule.delay(rule.id)
                    dispatched += 1
                else:
                    logger.debug("Rule '%s' — not due yet", rule.name)
            except Exception as e:
                logger.error("Beat: error checking rule '%s': %s", rule.name, e)

        return {"status": "checked", "rules": len(rules), "dispatched": dispatched}

    try:
        return _run_async(_run())
    except Exception as e:
        logger.error("Beat: evaluate_all_rules crashed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


def _should_fire(rule: EventRule, now: datetime) -> bool:
    """Check if a rule's schedule means it should fire now."""
    if not rule.trigger.schedule:
        return False

    # Don't re-fire if triggered within the last 4 minutes (Beat runs every 5 min)
    if rule.last_triggered:
        last = rule.last_triggered
        # Make both datetimes comparable (strip tzinfo if needed)
        if now.tzinfo and not last.tzinfo:
            last = last.replace(tzinfo=now.tzinfo)
        elif last.tzinfo and not now.tzinfo:
            now = now.replace(tzinfo=last.tzinfo)
        since_last = (now - last).total_seconds()
        if since_last < 240:
            return False

    try:
        return _cron_matches_now(rule.trigger.schedule, now)
    except Exception as e:
        logger.warning("Invalid cron for rule '%s': %s", rule.name, e)
        return False


def _cron_matches_now(cron_expr: str, now: datetime) -> bool:
    """Simple cron expression matcher."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    fields = [now.minute, now.hour, now.day, now.month, now.weekday()]
    # Cron weekday: 0=Sunday, Python weekday: 0=Monday
    fields[4] = (fields[4] + 1) % 7

    for field_val, cron_part in zip(fields, parts):
        if not _cron_field_matches(field_val, cron_part):
            return False
    return True


def _cron_field_matches(value: int, pattern: str) -> bool:
    """Check if a single cron field matches a value."""
    if pattern == "*":
        return True
    if pattern.startswith("*/"):
        step = int(pattern[2:])
        return value % step == 0
    if "," in pattern:
        return value in {int(p) for p in pattern.split(",")}
    if "-" in pattern:
        low, high = pattern.split("-", 1)
        return int(low) <= value <= int(high)
    return value == int(pattern)


# ── Evaluate a Single Rule ───────────────────────────────

@celery_app.task(name="app.worker.tasks.evaluate_single_rule", bind=True, max_retries=2)
def evaluate_single_rule(self, rule_id: str):
    """
    Evaluate a single rule:
      1. Load rule from Redis
      2. Resolve org_id (from rule or auto-detect)
      3. If POLLING: fetch data from MCP with org_id, evaluate conditions
      4. Build jobs for matched items x actions
      5. Dispatch jobs to queue
    """
    logger.info("Evaluating rule: %s", rule_id)

    async def _run():
        store = await _get_rule_store()
        rule = await store.get_rule(rule_id)
        if not rule or rule.status != RuleStatus.ACTIVE:
            logger.warning("Rule %s not found or inactive — skipping", rule_id)
            return {"status": "skipped"}

        # Resolve org_id dynamically
        org_id = await _resolve_org_id_for_rule(rule)
        if org_id and not rule.org_id:
            # Persist the auto-detected org_id on the rule for future runs
            rule.org_id = org_id
            await store.save_rule(rule)
            logger.info("Persisted auto-detected org_id '%s' on rule '%s'", org_id, rule.name)

        matched_items: list[dict] = [{}]  # Default: one empty item for schedule-only

        if rule.trigger.type == TriggerType.POLLING and rule.trigger.data_source:
            try:
                all_items = []
                # Inject org_id into data_source_params
                ds_params = _inject_org_id(rule.trigger.data_source_params, org_id)

                for attempt in range(3):
                    raw = await _call_mcp_tool_raw(rule.trigger.data_source, ds_params)
                    all_items = parse_mcp_response(raw)
                    if all_items:
                        break
                    logger.warning(
                        "Rule '%s': MCP parse returned 0 items (attempt %d/3), retrying...",
                        rule.name, attempt + 1,
                    )
                    await asyncio.sleep(2)

                logger.info("Rule '%s': parsed %d items from %s (org=%s)",
                            rule.name, len(all_items), rule.trigger.data_source, org_id)
                matched_items = evaluate_conditions(all_items, rule.conditions)

                if not matched_items:
                    logger.info("Rule '%s': no items matched conditions", rule.name)
                    await store.mark_triggered(rule_id)
                    return {"status": "no_matches", "total": len(all_items)}

            except Exception as e:
                logger.error("Rule '%s' data fetch failed: %s", rule.name, e)
                raise self.retry(exc=e, countdown=60)

        # Build and dispatch jobs
        from app.automation.action_executor import build_jobs, dispatch_jobs
        jobs = build_jobs(rule, matched_items)
        task_ids = dispatch_jobs(jobs)

        # Mark triggered BEFORE dispatch returns (prevents duplicate firing)
        await store.mark_triggered(rule_id)
        return {
            "status": "dispatched",
            "jobs": len(jobs),
            "task_ids": task_ids,
            "org_id": org_id,
        }

    return _run_async(_run())


# ── Execute a Single Job ─────────────────────────────────

@celery_app.task(
    name="app.worker.tasks.execute_job",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def execute_job(self, job_payload: dict):
    """
    Execute a single action job.
    Resolves org_id from the parent rule and passes it to all MCP calls.
    """
    job = JobPayload.model_validate(job_payload)
    logger.info(
        "Executing job %s: action=%s, rule=%s (attempt %d/%d)",
        job.job_id, job.action.type, job.rule_name, self.request.retries + 1, job.max_retries + 1,
    )

    execution = TaskExecution(
        rule_id=job.rule_id,
        rule_name=job.rule_name,
        job_id=job.job_id,
        status=JobStatus.PROCESSING,
        action_type=job.action.type,
        started_at=datetime.utcnow(),
    )

    try:
        # Resolve org_id from the parent rule
        org_id = _run_async(_get_rule_org_id(job.rule_id))
        result = _run_async(_execute_action(job.action, job.matched_data, org_id))
        execution.status = JobStatus.COMPLETED
        execution.result = result
        execution.completed_at = datetime.utcnow()
        execution.retries = self.request.retries
        logger.info("Job %s completed: %s", job.job_id, result)

    except Exception as exc:
        logger.error("Job %s failed: %s", job.job_id, exc)
        execution.retries = self.request.retries

        if self.request.retries < job.max_retries:
            countdown = 60 * (2 ** self.request.retries)
            execution.status = JobStatus.FAILED
            execution.error = str(exc)
            _run_async(_log_execution(execution))
            raise self.retry(exc=exc, countdown=countdown)
        else:
            execution.status = JobStatus.DEAD
            execution.error = str(exc)
            execution.completed_at = datetime.utcnow()
            job.error = str(exc)
            job.retries = self.request.retries
            _run_async(_move_to_dlq(job))

    _run_async(_log_execution(execution))
    return execution.model_dump(mode="json")


async def _get_rule_org_id(rule_id: str) -> str:
    """Fetch org_id from the rule in Redis."""
    store = await _get_rule_store()
    rule = await store.get_rule(rule_id)
    return rule.org_id if rule else ""


async def _log_execution(execution: TaskExecution) -> None:
    """Log execution to Redis."""
    store = await _get_rule_store()
    await store.log_execution(execution)


async def _move_to_dlq(job: JobPayload) -> None:
    """Move a failed job to the DLQ."""
    dlq = await _get_dlq()
    try:
        await dlq.push(job)
    finally:
        await dlq.close()


# ── Action Handlers ──────────────────────────────────────

async def _execute_action(action: ActionConfig, matched_data: dict, org_id: str = "") -> dict:
    """Route to the correct action handler based on action.type."""
    handlers = {
        "send_whatsapp": _action_send_whatsapp,
        "send_email": _action_send_email,
        "generate_report": _action_generate_report,
        "call_mcp_tool": _action_call_mcp_tool,
    }

    handler = handlers.get(action.type)
    if not handler:
        raise ValueError(f"Unknown action type: {action.type}")

    return await handler(action.params, matched_data, org_id)


async def _action_send_whatsapp(params: dict, matched_data: dict, org_id: str = "") -> dict:
    """Send a WhatsApp message via the Cloud API."""
    to = params.get("to", "")
    if not to and "phone" in matched_data:
        to = matched_data["phone"]
    if not to and "mobile" in matched_data:
        to = matched_data["mobile"]

    if not to:
        raise ValueError("No recipient phone number — set 'to' in params or ensure data has 'phone' field")

    body = params.get("body", "") or params.get("message", "")
    template = params.get("template", "")
    summary_template = params.get("summary_template", "")
    item_template = params.get("item_template", "")

    # Aggregated data: _items list present
    if "_items" in matched_data and (summary_template or item_template):
        items = matched_data["_items"]
        count = matched_data.get("_count", len(items))
        total_amount = sum(float(i.get("total", 0) or 0) for i in items)
        paid_count = sum(1 for i in items if str(i.get("status", "")).lower() == "paid")
        overdue_count = sum(1 for i in items if str(i.get("status", "")).lower() == "overdue")
        summary_data = {
            "_count": str(count),
            "_total_amount": f"{total_amount:,.2f}",
            "_paid_count": str(paid_count),
            "_overdue_count": str(overdue_count),
        }
        parts = []
        if summary_template:
            parts.append(_render_template(summary_template, summary_data))
        if item_template:
            for i, item in enumerate(items, 1):
                item_with_idx = {**item, "_idx": str(i)}
                parts.append(_render_template(item_template, item_with_idx))
        body = "\n".join(parts)
    elif template and matched_data:
        body = _render_template(template, matched_data)
    elif not body:
        if matched_data and any(k for k in matched_data if not k.startswith("_")):
            readable_parts = []
            for k, v in matched_data.items():
                if not k.startswith("_"):
                    readable_parts.append(f"\u2022 *{k}*: {v}")
            body = "\U0001f4cb *Automation Alert*\n\n" + "\n".join(readable_parts[:20])
        else:
            body = "\u26a0\ufe0f Automation triggered but no data was available to format. Please check your rule configuration."

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }
    base_url = f"{WHATSAPP_API_URL}/{WHATSAPP_PHONE_NUMBER_ID}"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    client = await _get_whatsapp_client()
    resp = await client.post(f"{base_url}/messages", headers=headers, json=payload)
    resp.raise_for_status()
    result = resp.json()

    msg_id = result.get("messages", [{}])[0].get("id", "unknown")
    return {"sent": True, "message_id": msg_id, "to": to}


async def _action_send_email(params: dict, matched_data: dict, org_id: str = "") -> dict:
    """Send an email via MCP email tool."""
    tool_name = params.get("tool", "ZohoCRM_Send_Mail")
    email_params = {**params}
    email_params.pop("tool", None)

    if "to" not in email_params and "email" in matched_data:
        email_params["to"] = matched_data["email"]
    if "subject" not in email_params:
        email_params["subject"] = f"Automated notification \u2014 {matched_data.get('name', 'N/A')}"
    if "body" not in email_params:
        template = params.get("template", "")
        if template:
            email_params["body"] = _render_template(template, matched_data)

    result = await _call_mcp_tool(tool_name, email_params, org_id)
    return {"sent": True, "tool": tool_name, "response": result[:500]}


async def _action_generate_report(params: dict, matched_data: dict, org_id: str = "") -> dict:
    """
    Generate a report by fetching data from MCP tools, computing summary
    statistics, and optionally sending the result via WhatsApp.
    org_id is injected into tool_params automatically.
    """
    tool_name = params.get("data_tool", "ZohoBooks_list_invoices")
    tool_params = params.get("tool_params", {})
    report_type = params.get("report_type", "generic")
    title = params.get("title", "Report")

    # Inject org_id into tool_params
    tool_params = _inject_org_id(tool_params, org_id)

    result = await _call_mcp_tool_raw(tool_name, tool_params)
    data = parse_mcp_response(result)
    logger.info("Report '%s': fetched %d records from %s (org=%s)", title, len(data), tool_name, org_id)

    summary = _build_report_summary(data, report_type, title)

    if params.get("send_to"):
        await _action_send_whatsapp(
            {"to": params["send_to"], "body": summary},
            matched_data,
            org_id,
        )

    return {
        "records": len(data),
        "tool": tool_name,
        "org_id": org_id,
        "status": "sent" if params.get("send_to") else "generated",
        "summary": summary[:500],
    }


def _build_report_summary(data: list[dict], report_type: str, title: str) -> str:
    """
    Build a human-readable WhatsApp summary from ANY fetched data.
    Dynamically detects fields and formats them — no hardcoded report types.
    """
    today_str = datetime.now().strftime("%d %b %Y")

    if not data:
        return f"📊 *{title}*\n📅 {today_str}\n\nNo records found."

    lines = [f"📊 *{title}*", f"📅 {today_str}", ""]

    # ── Auto-detect financial fields ──
    amount_fields = ["total", "amount", "grand_total", "sub_total", "bcy_total"]
    balance_fields = ["balance", "due", "amount_due", "bcy_balance"]
    currency = _detect_currency(data)

    total_amount = 0.0
    total_balance = 0.0
    amt_field = next((f for f in amount_fields if any(f in item for item in data)), None)
    bal_field = next((f for f in balance_fields if any(f in item for item in data)), None)

    for item in data:
        if amt_field:
            total_amount += float(item.get(amt_field, 0) or 0)
        if bal_field:
            total_balance += float(item.get(bal_field, 0) or 0)

    # ── Financial summary ──
    if amt_field:
        lines.append(f"💰 *Total Amount:* {currency} {total_amount:,.2f}")
    if bal_field and total_balance > 0:
        paid = total_amount - total_balance
        lines.append(f"✅ *Paid:* {currency} {paid:,.2f}")
        lines.append(f"⏳ *Outstanding:* {currency} {total_balance:,.2f}")
    lines.append("")

    # ── Status breakdown (if status field exists) ──
    status_field = next((f for f in ["status", "state"] if any(f in item for item in data)), None)
    if status_field:
        status_counts: dict[str, int] = {}
        for item in data:
            s = str(item.get(status_field, "unknown")).lower()
            status_counts[s] = status_counts.get(s, 0) + 1

        status_icons = {
            "paid": "✅", "overdue": "⚠️", "sent": "📨", "draft": "✏️",
            "open": "📂", "closed": "🔒", "unpaid": "🔴", "pending": "⏳",
            "partially_paid": "🟡", "active": "🟢", "void": "🚫",
        }
        lines.append(f"📋 *Breakdown ({len(data)} records):*")
        for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
            icon = status_icons.get(status, "•")
            lines.append(f"  {icon} {status.replace('_', ' ').title()}: {count}")
        lines.append("")

    # ── Recent items (top 5) ──
    # Auto-detect the best fields to display
    name_field = _detect_field(data, ["invoice_number", "bill_number", "reference_number",
                                       "expense_id", "journal_number", "salesorder_number",
                                       "contact_name", "item_name", "name", "number"])
    desc_field = _detect_field(data, ["customer_name", "vendor_name", "contact_name",
                                       "description", "account_name", "category_name"])
    date_field = _detect_field(data, ["date", "due_date", "created_time", "last_modified_time"])

    # Sort by date if available
    sorted_data = data
    if date_field:
        sorted_data = sorted(data, key=lambda x: str(x.get(date_field, "")), reverse=True)

    top_items = sorted_data[:5]
    if top_items and name_field:
        lines.append(f"📝 *Recent Records:*")
        for item in top_items:
            name_val = item.get(name_field, "N/A")
            parts = [f"*{name_val}*"]
            if desc_field and item.get(desc_field):
                parts.append(str(item[desc_field]))
            if amt_field and item.get(amt_field):
                parts.append(f"{currency} {float(item.get(amt_field, 0) or 0):,.2f}")
            if status_field and item.get(status_field):
                s = str(item[status_field]).lower()
                icon = status_icons.get(s, "•") if status_field else "•"
                parts.append(icon)

            lines.append(f"  • {' — '.join(parts)}")

    return "\n".join(lines)


def _detect_currency(data: list[dict]) -> str:
    """Auto-detect currency from data."""
    for item in data:
        for field in ["currency_code", "currency", "currency_symbol"]:
            if item.get(field):
                return str(item[field])
    return "INR"


def _detect_field(data: list[dict], candidates: list[str]) -> str | None:
    """Find the first field name that exists in the data."""
    for field in candidates:
        if any(field in item for item in data[:5]):
            return field
    return None


async def _action_call_mcp_tool(params: dict, matched_data: dict, org_id: str = "") -> dict:
    """Generic MCP tool call action."""
    tool_name = params.get("tool")
    if not tool_name:
        raise ValueError("'tool' parameter required for call_mcp_tool action")

    tool_params = {k: v for k, v in params.items() if k != "tool"}
    for key, val in matched_data.items():
        if key not in tool_params:
            tool_params[key] = val

    result = await _call_mcp_tool(tool_name, tool_params, org_id)
    return {"tool": tool_name, "response": result[:1000]}


def _render_template(template: str, data: dict) -> str:
    """Simple template rendering: replace {field} placeholders with data values."""
    result = template
    for key, value in data.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result
