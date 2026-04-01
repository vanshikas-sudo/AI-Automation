"""
Chat Agent — General conversation with NO tools.

Handles greetings, questions about capabilities, and anything
that doesn't need Zoho tools. Uses direct LLM call instead of
LangGraph agent — saves agent overhead + zero tool schema tokens.
"""

import logging

from langchain_core.messages import SystemMessage

from app.agents.base_agent import BaseAgent
from app.core.intent_router import Intent
from app.core.prompt_builder import build_prompt
from app.core.session_manager import SessionManager
from app.services.whatsapp_service import IncomingMessage

logger = logging.getLogger(__name__)


class ChatAgent(BaseAgent):
    """No-tools agent for general conversation. Direct LLM call."""

    name = "chat"

    async def run(self, msg: IncomingMessage, app_state, **kwargs) -> str:
        model = app_state.llm_model
        prompt = build_prompt(Intent.CHAT)

        SessionManager.add_user_message(msg.from_number, msg.text)
        messages = SessionManager.get_langchain_messages(msg.from_number)

        try:
            # Direct LLM call — no agent, no tools, minimal tokens
            full_messages = [SystemMessage(content=prompt)] + messages
            response = await model.ainvoke(full_messages)
            reply = response.content.strip() if response.content else (
                "I couldn't generate a response. Please try again."
            )
        except Exception as e:
            logger.error("ChatAgent failed for %s: %s", msg.from_number, e, exc_info=True)
            reply = "Sorry, I'm having trouble right now. Please try again."

        SessionManager.add_assistant_message(msg.from_number, reply)
        return reply


# Module-level singleton
chat_agent = ChatAgent()
