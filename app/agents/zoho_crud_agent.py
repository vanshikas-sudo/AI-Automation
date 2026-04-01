"""
Zoho CRUD Agent — Handles Zoho Books + CRM operations.

Uses LangGraph ReAct agent with the ZOHO_CRUD tool subset.
Includes conversation history from SessionManager so the user
can have multi-turn interactions ("create invoice" → "add line item").
"""

import json
import logging

from langgraph.prebuilt import create_react_agent

from app.agents.base_agent import BaseAgent
from app.core.intent_router import Intent
from app.core.prompt_builder import build_prompt
from app.core.session_manager import SessionManager
from app.services.whatsapp_service import IncomingMessage

logger = logging.getLogger(__name__)


class ZohoCrudAgent(BaseAgent):
    """Full CRUD agent with all whitelisted Zoho tools."""

    name = "zoho_crud"

    async def run(self, msg: IncomingMessage, app_state, **kwargs) -> str:
        model = app_state.llm_model
        mcp_manager = app_state.mcp_manager
        registry = mcp_manager.registry

        # Try to capture org selection from user's reply BEFORE running agent
        self._try_capture_org_selection(msg.from_number, msg.text, mcp_manager)

        # Determine org context for this user
        org_id = self._resolve_org_id(msg.from_number, mcp_manager)

        if org_id:
            # Org known — give full tool set
            tools = registry.get_for_intent(Intent.ZOHO_CRUD)
        elif mcp_manager.zoho_organizations:
            # Multiple orgs known but none selected — no tools, prompt asks
            tools = []
        else:
            # Orgs not fetched yet (startup failed) — give list_organizations
            # tool so the agent can discover orgs and ask user to pick one
            org_tool = registry.get_tool("ZohoBooks_list_organizations")
            tools = [org_tool] if org_tool else []

        prompt = build_prompt(
            Intent.ZOHO_CRUD,
            zoho_org_id=org_id,
            zoho_organizations=mcp_manager.zoho_organizations if not org_id else None,
        )

        agent = create_react_agent(model, tools, prompt=prompt)

        SessionManager.add_user_message(msg.from_number, msg.text)
        messages = SessionManager.get_langchain_messages(msg.from_number)

        try:
            response = await agent.ainvoke({"messages": messages})
            reply = SessionManager.extract_reply(response)

            # If we just discovered orgs via tool call, cache them in mcp_manager
            if not mcp_manager.zoho_organizations:
                self._try_capture_orgs_from_response(response, mcp_manager)

        except Exception as e:
            logger.error(
                "ZohoCrudAgent failed for %s: %s", msg.from_number, e, exc_info=True
            )
            reply = "Sorry, I'm having trouble processing your Zoho request. Please try again."

        SessionManager.add_assistant_message(msg.from_number, reply)
        return reply

    @staticmethod
    def _resolve_org_id(phone: str, mcp_manager) -> str | None:
        """Get the org ID for this user: session selection > single-org auto > None."""
        # 1. User already selected an org in this session
        org = SessionManager.get_org(phone)
        if org:
            return org["org_id"]
        # 2. Only one org exists — auto-use it
        if mcp_manager.zoho_org_id:
            return mcp_manager.zoho_org_id
        # 3. Multiple orgs, none selected yet — return None so prompt asks
        return None

    @staticmethod
    def _try_capture_org_selection(phone: str, user_text: str, mcp_manager) -> None:
        """If the user's message matches an org name, save it to session."""
        if SessionManager.has_org(phone):
            return
        if len(mcp_manager.zoho_organizations) <= 1:
            return
        org_id = mcp_manager.get_org_id_by_name(user_text)
        if org_id:
            # Find the full name
            for org in mcp_manager.zoho_organizations:
                if str(org["organization_id"]) == org_id:
                    SessionManager.set_org(phone, org_id, org["name"])
                    logger.info("User %s selected org: %s (%s)", phone, org["name"], org_id)
                    break

    @staticmethod
    def _try_capture_orgs_from_response(response: dict, mcp_manager) -> None:
        """Extract org data from agent tool responses and cache in mcp_manager."""
        try:
            messages = response.get("messages", [])
            for msg in messages:
                # Look for ToolMessage with list_organizations data
                if not hasattr(msg, "name") or msg.name != "ZohoBooks_list_organizations":
                    continue
                content = msg.content if hasattr(msg, "content") else ""
                text = content if isinstance(content, str) else str(content)

                # Try to parse JSON from the tool response
                data = mcp_manager._parse_json_response(text)
                if not data:
                    continue
                orgs = data.get("organizations", [])
                if not orgs:
                    continue

                mcp_manager.zoho_organizations = [
                    {
                        "name": o.get("name", ""),
                        "organization_id": o.get("organization_id", ""),
                        "is_default_org": o.get("is_default_org", False),
                    }
                    for o in orgs
                ]
                if len(mcp_manager.zoho_organizations) == 1:
                    mcp_manager.zoho_org_id = str(
                        mcp_manager.zoho_organizations[0].get("organization_id", "")
                    )
                org_names = [o["name"] for o in mcp_manager.zoho_organizations]
                logger.info(
                    "Captured %d org(s) from agent tool response: %s",
                    len(mcp_manager.zoho_organizations), org_names,
                )
                return
        except Exception:
            logger.debug("Failed to capture orgs from agent response", exc_info=True)


# Module-level singleton
zoho_crud_agent = ZohoCrudAgent()
