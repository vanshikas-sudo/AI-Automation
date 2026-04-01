"""
Webhook routes — thin layer that delegates to core/message_handler.

The webhook itself just:
  1. Validates the Meta signature
  2. Parses the payload
  3. Dispatches to handle_message() in the background
"""

import asyncio
import logging
import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Query, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.config import get_settings
from app.core.message_handler import handle_message
from app.services.whatsapp_service import WhatsAppService
from app.utils.validators import verify_webhook_signature

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Message deduplication (WhatsApp sends duplicate webhooks) ────
_seen_message_ids: OrderedDict[str, float] = OrderedDict()
_DEDUP_MAX = 500  # max tracked IDs
_DEDUP_TTL = 120  # seconds


def _is_duplicate(message_id: str) -> bool:
    """Check if we've already processed this message ID."""
    now = time.monotonic()
    # Evict old entries
    while _seen_message_ids and len(_seen_message_ids) > _DEDUP_MAX:
        _seen_message_ids.popitem(last=False)
    if message_id in _seen_message_ids:
        return True
    _seen_message_ids[message_id] = now
    return False


# ── Webhook verification (Meta sends GET to confirm URL) ─────────

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("Webhook verified successfully")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Incoming messages (Meta sends POST with message payload) ─────

@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    logger.info("Webhook POST received (%d bytes)", len(body))

    settings = get_settings()
    if settings.whatsapp_app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_webhook_signature(body, signature, settings.whatsapp_app_secret):
            logger.warning("Invalid webhook signature — rejecting request")
            raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    wa = WhatsAppService(request.app.state.http_client)
    messages = wa.parse_webhook_payload(payload)

    if not messages:
        logger.info("No text messages in payload (status update or non-text)")

    for msg in messages:
        if _is_duplicate(msg.message_id):
            logger.info("Skipping duplicate message %s from %s", msg.message_id, msg.from_number)
            continue
        logger.info("Received from %s (%s): %s", msg.from_number, msg.name, msg.text)
        background_tasks.add_task(handle_message, msg, request.app.state)

    return {"status": "ok"}


# ── Send a message via API (internal — requires API key) ─────────

class SendMessageRequest(BaseModel):
    to: str
    message: str


def _require_api_key(request: Request) -> None:
    """Light API-key guard so only authorised callers can push messages."""
    settings = get_settings()
    expected = getattr(settings, "internal_api_key", "")
    if not expected:
        # Key not configured → endpoint is disabled
        raise HTTPException(status_code=501, detail="Internal API key not configured")
    provided = request.headers.get("X-API-Key", "")
    if provided != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/messages/send")
async def send_message(body: SendMessageRequest, request: Request):
    _require_api_key(request)
    wa = WhatsAppService(request.app.state.http_client)
    result = await wa.send_text_message(to=body.to, body=body.message)
    return {"status": "sent", "detail": result}


# ── Health / Debug endpoints ─────────────────────────────────────

@router.get("/health")
async def health_check(request: Request):
    """
    Health probe for Azure App Service (and any load balancer).

    Returns 200 when the app is ready to handle traffic.
    Returns 503 when a critical component failed to initialise — Azure will
    remove the instance from rotation and attempt a restart.

    Critical:  LLM model must be present (app is useless without it).
    Degraded:  MCP not connected → CHAT still works, ZOHO_CRUD/REPORT won't.
               We still return 200 so Azure doesn't restart needlessly, but
               the 'degraded' flag lets your monitoring dashboard alert you.
    """
    from fastapi.responses import JSONResponse

    settings = get_settings()
    mcp = getattr(request.app.state, "mcp_manager", None)
    llm_ready = getattr(request.app.state, "llm_model", None) is not None
    mcp_connected = bool(mcp and mcp.is_connected)

    # --- determine overall status -------------------------------------------
    if not llm_ready:
        status, http_code = "unhealthy", 503
    elif not mcp_connected:
        status, http_code = "degraded", 200   # still alive, just limited
    else:
        status, http_code = "ok", 200

    payload = {
        "status": status,
        "architecture": "v2-intent-routed",
        "components": {
            "llm": {
                "ready": llm_ready,
                "provider": settings.llm_provider.value,
                "model": settings.resolved_model,
            },
            "mcp": {
                "connected": mcp_connected,
                "tools_registered": mcp.registry.tool_count if mcp else 0,
                "zoho_org_id": mcp.zoho_org_id if mcp else None,
            },
        },
    }
    return JSONResponse(content=payload, status_code=http_code)


@router.get("/test-report")
async def test_report(request: Request, fy: str = "2025-2026"):
    """Debug: generate a report without WhatsApp delivery."""
    from app.services.report_collector import collect_report_data
    from app.services.pdf_report_service import generate_fiscal_report_pdf
    from app.core.intent_router import Intent
    from langgraph.prebuilt import create_react_agent
    from app.core.prompt_builder import build_prompt

    mcp = request.app.state.mcp_manager
    model = request.app.state.llm_model
    tools = mcp.registry.get_for_intent(Intent.REPORT)
    prompt = build_prompt(Intent.REPORT, zoho_org_id=mcp.zoho_org_id)
    agent = create_react_agent(model, tools, prompt=prompt)

    try:
        data = await asyncio.wait_for(
            collect_report_data(agent, fy, org_id=mcp.zoho_org_id or "", tool_registry=mcp.registry),
            timeout=300,
        )
        pdf_path = generate_fiscal_report_pdf(data)
        return {"status": "ok", "pdf_path": pdf_path, "data_keys": list(data.keys())}
    except asyncio.TimeoutError:
        return {"status": "timeout", "detail": "Data collection timed out after 5 min"}
    except Exception as e:
        logger.exception("Test report failed")
        return {"status": "error", "detail": str(e)}
