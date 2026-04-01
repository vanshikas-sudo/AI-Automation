"""
Message Handler — Orchestrates the full message processing flow.

Replaces the inline _process_message logic from webhook.py.
Flow:
  1. Classify intent (zero cost — regex only)
  2. Route to the correct agent
  3. Each agent handles its own tools, prompt, and session management
"""

import difflib
import logging
import re

from app.config import get_settings
from app.core.intent_router import Intent, classify, extract_fiscal_year
from app.core.session_manager import SessionManager
from app.agents.chat_agent import chat_agent
from app.agents.zoho_crud_agent import zoho_crud_agent
from app.agents.report_agent import report_agent, ReportAgent
from app.agents.automation_agent import handle_automation_message, get_pending_automation, clear_pending_automation, set_pending_automation, get_pending_confirmation
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

        # ── Intercept pending automation confirmation (yes/no) ────
        pending_conf = get_pending_confirmation(msg.from_number)
        logger.info("[MSG] Pending confirmation for %s: %s", msg.from_number, bool(pending_conf))
        if pending_conf:
            await wa.mark_as_read(msg.message_id)
            reply = await handle_automation_message(msg.text, msg.from_number, app_state)
            if reply:
                await wa.send_text_message(to=msg.from_number, body=reply)
            return

        # ── Reclassify ZOHO_CRUD → AUTOMATION if it's a management command ──
        # e.g. "delete Unpaid bills Summary" matches ZOHO_CRUD due to "bills"
        # but the user means to delete an automation named "Unpaid bills Summary"
        if intent == Intent.ZOHO_CRUD:
            reclassified = await _try_reclassify_as_automation(msg.text, app_state)
            if reclassified:
                intent = Intent.AUTOMATION
                logger.info("[MSG] Reclassified ZOHO_CRUD → AUTOMATION for: %s", msg.text[:80])

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

        # ── Check for pending automation org selection ────────
        pending_auto_text = get_pending_automation(msg.from_number)
        if pending_auto_text and mcp_mgr:
            org_id = mcp_mgr.get_org_id_by_name(msg.text)
            if org_id:
                for org in mcp_mgr.zoho_organizations:
                    if str(org["organization_id"]) == org_id:
                        SessionManager.set_org(msg.from_number, org_id, org["name"])
                        logger.info("[MSG] Org selected for pending automation: %s (%s)",
                                    org["name"], org_id)
                        break
                clear_pending_automation(msg.from_number)
                await wa.mark_as_read(msg.message_id)
                reply = await handle_automation_message(pending_auto_text, msg.from_number, app_state)
                if reply:
                    await wa.send_text_message(to=msg.from_number, body=reply)
                return
            else:
                org_names = [org["name"] for org in mcp_mgr.zoho_organizations]
                org_list = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(org_names))
                await wa.mark_as_read(msg.message_id)
                await wa.send_text_message(
                    to=msg.from_number,
                    body=(
                        f"I didn't recognize that organization name. "
                        f"Please reply with one of these:\n{org_list}\n\n"
                        f"Or say *clear* to cancel."
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

        if intent == Intent.AUTOMATION:
            await wa.mark_as_read(msg.message_id)
            reply = await handle_automation_message(msg.text, msg.from_number, app_state)
            if reply:
                await wa.send_text_message(to=msg.from_number, body=reply)
                logger.info("[MSG] Automation reply sent to %s", msg.from_number)
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

        # Intent.CHAT — check for greetings first
        await wa.mark_as_read(msg.message_id)

        if _is_greeting(msg.text):
            await wa.send_text_message(
                to=msg.from_number,
                body=(
                    "Hey there! 👋 Welcome to *AgentFlow*.\n\n"
                    "Here's what I can do for you:\n\n"
                    "📋 *Zoho CRUD Operations*\n"
                    "   Create, view, update invoices, contacts, items, bills & more.\n\n"
                    "📊 *PDF Report Generation*\n"
                    "   Generate fiscal year financial reports with charts & insights.\n\n"
                    "⚡ *Automations*\n"
                    "   Schedule recurring tasks like daily sales summaries, overdue alerts, and more.\n"
                    "   Just say: _\"every day at 9 PM send me a sales summary\"_\n\n"
                    "Try sending a message like:\n"
                    "• _\"list my invoices\"_\n"
                    "• _\"generate fiscal year report 2024-2025\"_\n"
                    "• _\"list my automations\"_"
                ),
            )
            return

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


# ── Helpers ──────────────────────────────────────────────

_GREETING_PATTERN = re.compile(
    r"^\s*(hi|hello|hey|hola|yo|sup|hii+|helloo+|greetings|good\s*(morning|afternoon|evening|night)|namaste|howdy|what'?s\s*up)\s*[!.?]*\s*$",
    re.IGNORECASE,
)


def _is_greeting(text: str) -> bool:
    """Check if the message is a simple greeting."""
    return bool(_GREETING_PATTERN.match(text.strip()))


_AUTOMATION_MGMT_RE = re.compile(
    r"^\s*(delete|remove|pause|stop|disable|resume|enable|activate|trigger|run|test|fire)\s+(.+)",
    re.IGNORECASE,
)


async def _try_reclassify_as_automation(text: str, app_state) -> bool:
    """
    Check if a ZOHO_CRUD-classified text is actually an automation management
    command targeting a known rule name (e.g. "delete Unpaid bills Summary"
    where 'bills' triggered ZOHO_CRUD but 'Unpaid bills Summary' is a rule).
    """
    m = _AUTOMATION_MGMT_RE.match(text.strip())
    if not m:
        return False

    store = getattr(app_state, "rule_store", None)
    if not store:
        return False

    rules = await store.list_rules()
    if not rules:
        return False

    name_part = m.group(2).strip()
    # Strip optional "automation"/"rule" prefix
    name_part = re.sub(
        r"^(automations?|rules?)\s*", "", name_part, flags=re.IGNORECASE
    ).strip()
    if not name_part:
        return False

    hint_lower = name_part.lower()
    rule_names = [r.name.lower() for r in rules]

    # Exact or partial match
    for rn in rule_names:
        if hint_lower == rn or hint_lower in rn or rn in hint_lower:
            return True

    # Fuzzy match
    close = difflib.get_close_matches(hint_lower, rule_names, n=1, cutoff=0.5)
    return bool(close)
