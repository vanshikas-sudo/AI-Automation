"""
Report Agent — Fiscal report data collection + PDF generation + WhatsApp delivery.

Uses the REPORT tool subset (read-only, ~26 tools) instead of the full CRUD
set (~37 tools), saving ~3,300 tokens per report request.

This is a single-shot agent: no conversation history, just the mega-prompt.
It reuses the existing report_collector and pdf_report_service modules.
"""

import asyncio
import logging
import os

from langgraph.prebuilt import create_react_agent

from app.agents.base_agent import BaseAgent
from app.core.intent_router import Intent
from app.core.prompt_builder import build_prompt
from app.core.session_manager import SessionManager
from app.services.whatsapp_service import IncomingMessage, WhatsAppService

logger = logging.getLogger(__name__)

# Key used in SessionManager to remember a pending report request
_PENDING_REPORT_KEY = "__pending_report_fy"


class ReportAgent(BaseAgent):
    """Collects Zoho data, generates PDF, sends via WhatsApp."""

    name = "report"

    # ── Org resolution (mirrors ZohoCrudAgent logic) ─────────

    @staticmethod
    def _resolve_org_id(phone: str, mcp_manager) -> str | None:
        """Get org ID: session selection > single-org auto > None."""
        org = SessionManager.get_org(phone)
        if org:
            return org["org_id"]
        if mcp_manager.zoho_org_id:
            return mcp_manager.zoho_org_id
        return None

    @staticmethod
    def _try_capture_org_selection(phone: str, user_text: str, mcp_manager) -> bool:
        """If the user's message matches an org name, save it. Returns True if captured."""
        if SessionManager.has_org(phone):
            return False
        if len(mcp_manager.zoho_organizations) <= 1:
            return False
        org_id = mcp_manager.get_org_id_by_name(user_text)
        if org_id:
            for org in mcp_manager.zoho_organizations:
                if str(org["organization_id"]) == org_id:
                    SessionManager.set_org(phone, org_id, org["name"])
                    logger.info("[REPORT] User %s selected org: %s (%s)",
                                phone, org["name"], org_id)
                    return True
        return False

    @classmethod
    def set_pending_report(cls, phone: str, fiscal_year: str) -> None:
        """Remember that this user has a pending report waiting for org selection."""
        SessionManager.add_assistant_message(phone, f"{_PENDING_REPORT_KEY}:{fiscal_year}")

    @classmethod
    def get_pending_report(cls, phone: str) -> str | None:
        """Check if user has a pending report. Returns fiscal_year or None."""
        for item in reversed(SessionManager.get_history(phone)):
            if item["role"] == "assistant" and item["content"].startswith(_PENDING_REPORT_KEY):
                return item["content"].split(":", 1)[1]
        return None

    @classmethod
    def clear_pending_report(cls, phone: str) -> None:
        """Remove the pending report marker from session."""
        history = SessionManager.get_history(phone)
        SessionManager._sessions[phone] = [
            h for h in history
            if not (h["role"] == "assistant" and h["content"].startswith(_PENDING_REPORT_KEY))
        ]

    async def run(self, msg: IncomingMessage, app_state, **kwargs) -> str:
        """
        Full report flow:
          0. Check org — if not resolved, ask user to select one first
          1. Send "generating..." notice
          2. Build agent with REPORT tool subset
          3. Collect data via report_collector
          4. Generate PDF
          5. Send document via WhatsApp
          6. Clean up temp file

        Returns empty string (all communication happens inline).
        """
        fiscal_year = kwargs.get("fiscal_year", "2024-2025")
        wa = WhatsAppService(app_state.http_client)
        mcp_manager = app_state.mcp_manager
        model = app_state.llm_model

        # Mark as read
        try:
            await wa.mark_as_read(msg.message_id)
        except Exception:
            logger.warning("Could not mark message %s as read", msg.message_id, exc_info=True)

        # ── Step 0: Ensure org is resolved ───────────────────
        # Try to capture org selection from user's reply
        self._try_capture_org_selection(msg.from_number, msg.text, mcp_manager)

        org_id = self._resolve_org_id(msg.from_number, mcp_manager)

        if not org_id:
            # Multiple orgs exist, none selected — ask user to pick
            if len(mcp_manager.zoho_organizations) > 1:
                org_names = [org["name"] for org in mcp_manager.zoho_organizations]
                org_list = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(org_names))
                self.set_pending_report(msg.from_number, fiscal_year)
                await wa.send_text_message(
                    to=msg.from_number,
                    body=(
                        f"📊 I'd like to generate your FY {fiscal_year} report, "
                        f"but I need to know which organization to use.\n\n"
                        f"Please reply with the organization name:\n{org_list}"
                    ),
                )
                logger.info("[REPORT] Asked user %s to select org (FY %s)",
                            msg.from_number, fiscal_year)
                return ""
            else:
                # No orgs found at all — error
                logger.error("[REPORT] No Zoho organizations available")
                await wa.send_text_message(
                    to=msg.from_number,
                    body="Sorry, no Zoho organizations are configured. "
                         "Please check the server setup.",
                )
                return ""

        # Clear any pending report marker now that we have an org
        self.clear_pending_report(msg.from_number)

        logger.info("[REPORT] Using org_id=%s for user %s FY %s",
                    org_id, msg.from_number, fiscal_year)

        # Send "generating" notice
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
            # Build agent with REPORT-scoped tools (fewer than full CRUD)
            tools = mcp_manager.registry.get_for_intent(Intent.REPORT)
            prompt = build_prompt(Intent.REPORT, zoho_org_id=org_id)
            agent = create_react_agent(model, tools, prompt=prompt)

            logger.info("[REPORT] Collecting data for FY %s (user %s, org %s)…",
                        fiscal_year, msg.from_number, org_id)

            # Reuse existing collector logic
            from app.services.report_collector import collect_report_data
            from app.services.pdf_report_service import generate_fiscal_report_pdf

            data = await asyncio.wait_for(
                collect_report_data(
                    agent, fiscal_year,
                    org_id=org_id,
                    tool_registry=mcp_manager.registry,
                ),
                timeout=300,
            )

            # Sanity check: did we actually get data?
            total_sales = data.get("total_sales", 0)
            total_invoices = len(data.get("monthly_sales", []))
            logger.info("[REPORT] Data collected — total_sales=%.2f, monthly_entries=%d",
                        total_sales, total_invoices)

            if total_sales == 0 and not data.get("sales_breakdown"):
                logger.warning("[REPORT] All data is zero — org_id may be wrong or no data in Zoho")

            pdf_path = generate_fiscal_report_pdf(data)
            logger.info("[REPORT] PDF generated at %s, sending…", pdf_path)

            await wa.send_document(
                to=msg.from_number,
                file_path=pdf_path,
                filename=f"Fiscal_Report_{fiscal_year.replace('/', '-')}.pdf",
                caption=f"Fiscal Year {fiscal_year} Financial Report",
            )
            logger.info("[REPORT] Sent to %s for FY %s", msg.from_number, fiscal_year)

        except asyncio.TimeoutError:
            logger.error("[REPORT] Timed out for %s FY %s", msg.from_number, fiscal_year)
            try:
                await wa.send_text_message(
                    to=msg.from_number,
                    body="Sorry, the report is taking too long. Please try again later.",
                )
            except Exception:
                logger.exception("[REPORT] Could not send timeout error to %s", msg.from_number)

        except Exception:
            logger.exception("[REPORT] Failed for %s", msg.from_number)
            try:
                await wa.send_text_message(
                    to=msg.from_number,
                    body="Sorry, I couldn't generate the report right now. Please try again later.",
                )
            except Exception:
                logger.exception("[REPORT] Could not send error to %s", msg.from_number)

        finally:
            if pdf_path and os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass

        return ""  # all communication handled inline


# Module-level singleton
report_agent = ReportAgent()
