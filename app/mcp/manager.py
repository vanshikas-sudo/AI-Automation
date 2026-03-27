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
        self.zoho_organizations: list[dict] = []  # [{name, organization_id}, ...]

    async def initialize(self) -> None:
        """Startup: connect to MCP, register tools, detect org ID."""
        settings = get_settings()
        raw_tools = await self.client.connect(settings.mcp_zoho_url)
        self.registry.register(raw_tools)

        # Auto-detect Zoho organizations
        if settings.zoho_org_id:
            self.zoho_org_id = settings.zoho_org_id
        else:
            await self._fetch_zoho_organizations()
            # If only one org, auto-select it
            if len(self.zoho_organizations) == 1:
                self.zoho_org_id = str(self.zoho_organizations[0].get("organization_id", ""))

    def get_org_id_by_name(self, name: str) -> str | None:
        """Look up an org ID by name (case-insensitive partial match)."""
        name_lower = name.lower().strip()
        for org in self.zoho_organizations:
            if org.get("name", "").lower().strip() == name_lower:
                return str(org["organization_id"])
        # Partial match fallback
        for org in self.zoho_organizations:
            if name_lower in org.get("name", "").lower():
                return str(org["organization_id"])
        return None

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

    async def _fetch_zoho_organizations(self) -> None:
        """Call ZohoBooks_list_organizations to fetch all available orgs."""
        org_tool = self.registry.get_tool("ZohoBooks_list_organizations")
        if not org_tool:
            logger.warning("ZohoBooks_list_organizations not found — cannot auto-detect orgs")
            return

        try:
            logger.info("Auto-fetching Zoho organizations…")
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
                return

            data = json.loads(text)
            orgs = data.get("organizations", [])
            if not orgs:
                logger.warning("No organizations found in Zoho Books response")
                return

            self.zoho_organizations = [
                {
                    "name": o.get("name", ""),
                    "organization_id": o.get("organization_id", ""),
                    "is_default_org": o.get("is_default_org", False),
                }
                for o in orgs
            ]
            org_names = [o["name"] for o in self.zoho_organizations]
            logger.info("Found %d Zoho org(s): %s", len(self.zoho_organizations), org_names)

        except Exception:
            logger.warning("Failed to auto-fetch Zoho organizations", exc_info=True)
