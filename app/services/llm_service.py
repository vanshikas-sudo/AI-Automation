import logging
from collections import defaultdict

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

# In-memory conversation sessions: phone_number → list of messages
_sessions: dict[str, list[dict[str, str]]] = defaultdict(list)

# Max messages to keep per session (system + user/assistant pairs)
MAX_HISTORY = 20


class LLMService:
    """Handles LLM interaction via LangGraph agent with MCP tools."""

    def __init__(self, agent):
        self.agent = agent

    async def get_reply(self, phone_number: str, user_message: str) -> str:
        """
        Send a user message to the LangGraph agent with conversation history.
        The agent will call MCP tools (e.g. Zoho) when needed.
        """
        session = _sessions[phone_number]

        # Add the user's message
        session.append({"role": "user", "content": user_message})

        # Trim history if too long (keep last N messages)
        if len(session) > MAX_HISTORY:
            session[:] = session[-MAX_HISTORY:]

        try:
            messages = self._to_langchain_messages(session)
            response = await self.agent.ainvoke({"messages": messages})
            # Extract the last AIMessage from the response
            reply = self._extract_reply(response)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error("Agent call failed for %s: %s\n%s", phone_number, e, tb)
            with open("agent_error.log", "a") as f:
                f.write(f"\n--- {phone_number} ---\n{e}\n{tb}\n")
            reply = "Sorry, I'm having trouble processing your request right now. Please try again."

        # Store assistant reply in session
        session.append({"role": "assistant", "content": reply})

        return reply

    @staticmethod
    def _to_langchain_messages(session: list[dict[str, str]]) -> list:
        """Convert session dicts to LangChain message objects."""
        messages = []
        for msg in session:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))
        return messages

    @staticmethod
    def _extract_reply(response: dict) -> str:
        """Extract the final AI reply from the agent response."""
        for msg in reversed(response["messages"]):
            if isinstance(msg, AIMessage) and msg.content:
                return msg.content.strip()
        return "I couldn't generate a response. Please try again."

    @staticmethod
    def clear_session(phone_number: str) -> None:
        """Clear conversation history for a user."""
        _sessions.pop(phone_number, None)

    @staticmethod
    def get_session(phone_number: str) -> list[dict[str, str]]:
        """Get conversation history for a user (for debugging)."""
        return list(_sessions.get(phone_number, []))
