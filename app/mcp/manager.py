"""
MCP Manager — High-level facade over MCPClient + ToolRegistry.

Composes:
  - MCPClient: raw connection lifecycle
  - ToolRegistry: filtered + cached tool schemas
  - Zoho org ID auto-detection

This is what gets stored on app.state.mcp_manager.
"""

import json
import logging

from app.config import get_settings
from app.mcp.client import MCPClient
from app.mcp.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPManager:
    """High-level MCP manager — connects, filters tools, detects org ID."""

    def __init__(self):
        self.client = MCPClient()
        self.registry = ToolRegistry()
        self.zoho_org_id: str | None = None

    async def initialize(self) -> None:
        """Startup: connect to MCP, register tools, detect org ID."""
        settings = get_settings()
        raw_tools = await self.client.connect(settings.mcp_zoho_url)
        self.registry.register(raw_tools)

        # Auto-detect Zoho org ID
        org_id = settings.zoho_org_id
        if not org_id:
            org_id = await self._fetch_zoho_org_id()
        self.zoho_org_id = org_id

    async def ensure_connected(self) -> None:
        """Lazy reconnect if connection was dropped."""
        settings = get_settings()
        new_tools = await self.client.ensure_connected(settings.mcp_zoho_url)
        if new_tools:
            self.registry.register(new_tools)

    @property
    def is_connected(self) -> bool:
        return self.client.is_connected

    async def close(self) -> None:
        await self.client.close()

    async def _fetch_zoho_org_id(self) -> str | None:
        """Call ZohoBooks_list_organizations to auto-detect org ID."""
        org_tool = self.registry.get_tool("ZohoBooks_list_organizations")
        if not org_tool:
            logger.warning("ZohoBooks_list_organizations not found — cannot auto-detect org ID")
            return None

        try:
            logger.info("Auto-fetching Zoho organization ID…")
            result = await org_tool.ainvoke({})

            text = None
            if isinstance(result, list):
                for block in result:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block["text"]
                        break
            elif isinstance(result, str):
                text = result

            if not text:
                logger.warning("Empty response from ZohoBooks_list_organizations")
                return None

            data = json.loads(text)
            orgs = data.get("organizations", [])
            if not orgs:
                logger.warning("No organizations found in Zoho Books response")
                return None

            org = next((o for o in orgs if o.get("is_default_org")), orgs[0])
            org_id = str(org.get("organization_id", ""))
            org_name = org.get("name", "unknown")
            logger.info("Auto-detected Zoho org: %s (ID: %s)", org_name, org_id)
            return org_id

        except Exception:
            logger.warning("Failed to auto-fetch Zoho org ID", exc_info=True)
            return None
