"""
Message Handler — Orchestrates the full message processing flow.

Replaces the inline _process_message logic from webhook.py.
Flow:
  1. Classify intent (zero cost — regex only)
  2. Route to the correct agent
  3. Each agent handles its own tools, prompt, and session management
"""

import logging

from app.config import get_settings
from app.core.intent_router import Intent, classify, extract_fiscal_year
from app.core.session_manager import SessionManager
from app.agents.chat_agent import chat_agent
from app.agents.zoho_crud_agent import zoho_crud_agent
from app.agents.report_agent import report_agent
from app.services.whatsapp_service import WhatsAppService, IncomingMessage

logger = logging.getLogger(__name__)


async def handle_message(msg: IncomingMessage, app_state) -> None:
    """
    Process a single WhatsApp message in the background.

    Steps:
      1. Classify intent (free — regex, no LLM)
      2. Route to correct agent (each has its own scoped tools)
      3. Send reply via WhatsApp
    """
    logger.info("[MSG] Processing from %s: %s", msg.from_number, msg.text[:100])
    wa = WhatsAppService(app_state.http_client)

    try:
        # Step 1: Classify intent (zero tokens)
        intent = classify(msg.text)
        logger.info("[MSG] Intent: %s", intent.value)

        # Step 2: Route by intent
        if intent == Intent.CLEAR:
            SessionManager.clear(msg.from_number)
            await wa.mark_as_read(msg.message_id)
            await wa.send_text_message(
                to=msg.from_number,
                body="Chat history cleared. How can I help you?",
            )
            return

        if intent == Intent.REPORT:
            fiscal_year = extract_fiscal_year(msg.text)
            logger.info("[MSG] Report request: FY %s", fiscal_year)
            await report_agent.run(msg, app_state, fiscal_year=fiscal_year)
            return

        if intent == Intent.ZOHO_CRUD:
            # Ensure MCP is connected (lazy reconnect)
            await app_state.mcp_manager.ensure_connected()
            await wa.mark_as_read(msg.message_id)
            reply = await zoho_crud_agent.run(msg, app_state)
            if reply:
                await wa.send_text_message(to=msg.from_number, body=reply)
                logger.info("[MSG] Reply sent to %s: %s", msg.from_number, reply[:80])
            return

        # Intent.CHAT — no tools, direct LLM call
        await wa.mark_as_read(msg.message_id)
        settings = get_settings()
        if not settings.llm_api_key:
            reply = f"Echo: {msg.text}"
        else:
            reply = await chat_agent.run(msg, app_state)

        if reply:
            await wa.send_text_message(to=msg.from_number, body=reply)
            logger.info("[MSG] Reply sent to %s: %s", msg.from_number, reply[:80])

    except Exception:
        logger.exception("[MSG] Failed to process message from %s", msg.from_number)
        await wa.send_text_message(
            to=msg.from_number,
            body="Sorry, something went wrong processing your message. Please try again.",
        )
