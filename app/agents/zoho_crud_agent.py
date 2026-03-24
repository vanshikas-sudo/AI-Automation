"""
Zoho CRUD Agent — Handles Zoho Books + CRM operations.

Uses LangGraph ReAct agent with the ZOHO_CRUD tool subset.
Includes conversation history from SessionManager so the user
can have multi-turn interactions ("create invoice" → "add line item").
"""

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

        tools = registry.get_for_intent(Intent.ZOHO_CRUD)
        prompt = build_prompt(Intent.ZOHO_CRUD, zoho_org_id=mcp_manager.zoho_org_id)

        agent = create_react_agent(model, tools, prompt=prompt)

        SessionManager.add_user_message(msg.from_number, msg.text)
        messages = SessionManager.get_langchain_messages(msg.from_number)

        try:
            response = await agent.ainvoke({"messages": messages})
            reply = SessionManager.extract_reply(response)
        except Exception as e:
            logger.error(
                "ZohoCrudAgent failed for %s: %s", msg.from_number, e, exc_info=True
            )
            reply = "Sorry, I'm having trouble processing your Zoho request. Please try again."

        SessionManager.add_assistant_message(msg.from_number, reply)
        return reply


# Module-level singleton
zoho_crud_agent = ZohoCrudAgent()
