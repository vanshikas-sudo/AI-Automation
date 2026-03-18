import asyncio
import logging
import os
import re

from fastapi import APIRouter, Request, Query, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.config import get_settings
from app.services.whatsapp_service import WhatsAppService, IncomingMessage
from app.services.llm_service import LLMService
from app.utils.validators import verify_webhook_signature

logger = logging.getLogger(__name__)
router = APIRouter()

# Pattern to detect fiscal report requests
_REPORT_PATTERN = re.compile(
    r"\b(fiscal\s*(year)?\s*report|annual\s*report|year(ly)?\s*report|generate\s*report|fy\s*report)\b",
    re.IGNORECASE,
)

# Pattern to extract fiscal year from a message, e.g. "2024-2025" or "2025"
_FY_PATTERN = re.compile(r"(20\d{2})\s*[-–/]\s*(20\d{2})")
_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")


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


# ── Background message processing ────────────────────────────────

def _detect_report_request(text: str) -> str | None:
    """Return fiscal year string if the message is a report request, else None."""
    if not _REPORT_PATTERN.search(text):
        return None
    # Try to extract explicit FY like "2024-2025"
    m = _FY_PATTERN.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # Try single year
    m = _YEAR_PATTERN.search(text)
    if m:
        year = int(m.group(1))
        return f"{year}-{year + 1}"
    # Default
    return "2024-2025"


async def _process_report(msg: IncomingMessage, app_state, fiscal_year: str) -> None:
    """Generate a fiscal year PDF report and send it via WhatsApp."""
    from app.services.report_collector import collect_report_data
    from app.services.pdf_report_service import generate_fiscal_report_pdf

    wa = WhatsAppService(app_state.http_client)

    # Mark as read — non-blocking; don't let a failure here stop the report
    try:
        await wa.mark_as_read(msg.message_id)
    except Exception:
        logger.warning("Could not mark message %s as read", msg.message_id, exc_info=True)

    # Send "Generating" heads-up — also non-blocking
    try:
        await wa.send_text_message(
            to=msg.from_number,
            body=f"📊 Generating your Fiscal Year {fiscal_year} report. "
                 f"This may take a minute — please hang tight!",
        )
    except Exception:
        logger.warning("Could not send 'generating' notice to %s", msg.from_number, exc_info=True)

    pdf_path = None
    try:
        agent = app_state.mcp_agent
        logger.info("[REPORT] Collecting data for FY %s (user %s)…", fiscal_year, msg.from_number)

        # Timeout after 5 minutes so it doesn't hang forever
        data = await asyncio.wait_for(
            collect_report_data(agent, fiscal_year),
            timeout=300,
        )
        logger.info("[REPORT] Data collected, generating PDF…")

        pdf_path = generate_fiscal_report_pdf(data)
        logger.info("[REPORT] PDF generated at %s, sending document…", pdf_path)

        await wa.send_document(
            to=msg.from_number,
            file_path=pdf_path,
            filename=f"Fiscal_Report_{fiscal_year.replace('/', '-')}.pdf",
            caption=f"Fiscal Year {fiscal_year} Financial Report",
        )
        logger.info("[REPORT] Report PDF sent to %s for FY %s", msg.from_number, fiscal_year)

    except asyncio.TimeoutError:
        logger.error("[REPORT] Timed out collecting data for %s FY %s", msg.from_number, fiscal_year)
        try:
            await wa.send_text_message(
                to=msg.from_number,
                body="Sorry, the report is taking too long to generate. Please try again later.",
            )
        except Exception:
            logger.exception("[REPORT] Could not send timeout error to %s", msg.from_number)

    except Exception:
        logger.exception("[REPORT] Failed to generate/send report for %s", msg.from_number)
        try:
            await wa.send_text_message(
                to=msg.from_number,
                body="Sorry, I couldn't generate the report right now. Please try again later.",
            )
        except Exception:
            logger.exception("[REPORT] Could not send error message to %s", msg.from_number)

    finally:
        if pdf_path and os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except OSError:
                pass


async def _process_message(msg: IncomingMessage, app_state) -> None:
    """Process a single message in the background (after 200 returned to Meta)."""
    logger.info("[MSG] Processing message from %s: %s", msg.from_number, msg.text[:100])
    try:
        # Check if the user is asking for a fiscal year report
        fiscal_year = _detect_report_request(msg.text)
        if fiscal_year:
            logger.info("[MSG] Report request detected: FY %s", fiscal_year)
            await _process_report(msg, app_state, fiscal_year)
            return

        wa = WhatsAppService(app_state.http_client)
        await wa.mark_as_read(msg.message_id)

        settings = get_settings()
        if settings.llm_api_key:
            llm = LLMService(app_state.mcp_agent)
            reply = await llm.get_reply(msg.from_number, msg.text)
        else:
            reply = f"Echo: {msg.text}"

        await wa.send_text_message(to=msg.from_number, body=reply)
        logger.info("[MSG] Reply sent to %s: %s", msg.from_number, reply[:80])
    except Exception:
        logger.exception("[MSG] Failed to process message from %s", msg.from_number)


# ── Incoming messages (Meta sends POST with message payload) ─────

@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    logger.info("Webhook POST received (%d bytes)", len(body))

    # Validate signature from Meta (skip if app_secret not configured)
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
        logger.info(
            "Received from %s (%s): %s",
            msg.from_number, msg.name, msg.text,
        )
        # Process in background so Meta gets 200 immediately
        background_tasks.add_task(_process_message, msg, request.app.state)

    # Meta expects a 200 OK quickly — return before processing
    return {"status": "ok"}


# ── Send a message via API ───────────────────────────────────────

class SendMessageRequest(BaseModel):
    to: str
    message: str


@router.post("/messages/send")
async def send_message(body: SendMessageRequest, request: Request):
    wa = WhatsAppService(request.app.state.http_client)
    result = await wa.send_text_message(to=body.to, body=body.message)
    return {"status": "sent", "detail": result}


# ── Health / Debug endpoints ─────────────────────────────────────

@router.get("/health")
async def health_check(request: Request):
    """Quick health check: is the server + MCP agent alive?"""
    settings = get_settings()
    mcp_ok = getattr(request.app.state, "mcp_service", None)
    agent_ok = getattr(request.app.state, "mcp_agent", None) is not None
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider.value,
        "llm_model": settings.resolved_model,
        "mcp_connected": bool(mcp_ok and mcp_ok.is_connected),
        "agent_ready": agent_ok,
    }


@router.get("/test-report")
async def test_report(request: Request, fy: str = "2025-2026"):
    """Debug endpoint: generate a report and return the file path (no WhatsApp)."""
    from app.services.report_collector import collect_report_data
    from app.services.pdf_report_service import generate_fiscal_report_pdf

    agent = request.app.state.mcp_agent
    try:
        data = await asyncio.wait_for(
            collect_report_data(agent, fy),
            timeout=300,
        )
        pdf_path = generate_fiscal_report_pdf(data)
        return {"status": "ok", "pdf_path": pdf_path, "data_keys": list(data.keys())}
    except asyncio.TimeoutError:
        return {"status": "timeout", "detail": "Data collection timed out after 5 min"}
    except Exception as e:
        logger.exception("Test report failed")
        return {"status": "error", "detail": str(e)}
