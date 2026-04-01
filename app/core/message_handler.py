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
from app.agents.report_agent import report_agent, ReportAgent
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

        # ── Check for pending report org selection ───────────
        # If user was asked to pick an org for a report, and they reply
        # with an org name (which would be classified as CHAT), intercept it.
        mcp_mgr = getattr(app_state, "mcp_manager", None)
        pending_fy = ReportAgent.get_pending_report(msg.from_number)
        if pending_fy and intent == Intent.CHAT and mcp_mgr:
            org_id = mcp_mgr.get_org_id_by_name(msg.text)
            if org_id:
                # User selected an org — capture it and trigger the report
                for org in mcp_mgr.zoho_organizations:
                    if str(org["organization_id"]) == org_id:
                        SessionManager.set_org(msg.from_number, org_id, org["name"])
                        logger.info("[MSG] Org selected for pending report: %s (%s)",
                                    org["name"], org_id)
                        break
                ReportAgent.clear_pending_report(msg.from_number)
                logger.info("[MSG] Resuming pending report for FY %s", pending_fy)
                await report_agent.run(msg, app_state, fiscal_year=pending_fy)
                return
            else:
                # User typed something that doesn't match any org — remind them
                org_names = [org["name"] for org in mcp_mgr.zoho_organizations]
                org_list = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(org_names))
                await wa.mark_as_read(msg.message_id)
                await wa.send_text_message(
                    to=msg.from_number,
                    body=(
                        f"I didn't recognize that organization name. "
                        f"Please reply with one of these:\n{org_list}\n\n"
                        f"Or say \"clear\" to cancel."
                    ),
                )
                return

        # If user is replying with an org name during selection, route to ZOHO_CRUD
        if (
            intent == Intent.CHAT
            and SessionManager.get_history(msg.from_number)
            and not SessionManager.has_org(msg.from_number)
        ):
            if (
                mcp_mgr
                and len(mcp_mgr.zoho_organizations) > 1
                and mcp_mgr.get_org_id_by_name(msg.text)
            ):
                intent = Intent.ZOHO_CRUD
                logger.info("[MSG] Redirected to ZOHO_CRUD for org selection: %s", msg.text)

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
