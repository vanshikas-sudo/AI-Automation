"""
Base Agent — Abstract interface for all agents.

Every agent implements:
  - name: identifier string
  - run(): process a message and return a reply
"""

from abc import ABC, abstractmethod
from app.services.whatsapp_service import IncomingMessage


class BaseAgent(ABC):
    """Abstract base for all agent types."""

    name: str = "base"

    @abstractmethod
    async def run(self, msg: IncomingMessage, app_state, **kwargs) -> str:
        """
        Process a message and return the reply text.

        Args:
            msg:       The parsed incoming WhatsApp message.
            app_state: FastAPI app.state (contains llm_model, mcp_manager, etc.)
            **kwargs:  Intent-specific extras (e.g. fiscal_year for reports).

        Returns:
            Reply string to send back via WhatsApp.
        """
        ...
