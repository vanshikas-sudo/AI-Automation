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
from app.services.whatsapp_service import IncomingMessage, WhatsAppService

logger = logging.getLogger(__name__)


class ReportAgent(BaseAgent):
    """Collects Zoho data, generates PDF, sends via WhatsApp."""

    name = "report"

    async def run(self, msg: IncomingMessage, app_state, **kwargs) -> str:
        """
        Full report flow:
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
            prompt = build_prompt(Intent.REPORT, zoho_org_id=mcp_manager.zoho_org_id)
            agent = create_react_agent(model, tools, prompt=prompt)

            logger.info("[REPORT] Collecting data for FY %s (user %s)…", fiscal_year, msg.from_number)

            # Reuse existing collector logic
            from app.services.report_collector import collect_report_data
            from app.services.pdf_report_service import generate_fiscal_report_pdf

            data = await asyncio.wait_for(
                collect_report_data(
                    agent, fiscal_year,
                    org_id=mcp_manager.zoho_org_id or "",
                    tool_registry=mcp_manager.registry,
                ),
                timeout=300,
            )
            logger.info("[REPORT] Data collected, generating PDF…")

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
