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
        """Lazy reconnect if connection was dropped, and retry org fetch if needed."""
        settings = get_settings()
        new_tools = await self.client.ensure_connected(settings.mcp_zoho_url)
        if new_tools:
            self.registry.register(new_tools)

        # Retry org detection if it failed during startup
        if not self.zoho_organizations and not settings.zoho_org_id:
            logger.info("Retrying Zoho organization detection…")
            await self._fetch_zoho_organizations()
            if len(self.zoho_organizations) == 1:
                self.zoho_org_id = str(self.zoho_organizations[0].get("organization_id", ""))

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
            # Try with wrapper params first (Zoho MCP tools expect nested params),
            # fall back to empty dict if that fails
            result = None
            for params in ({"query_params": {}}, {}):
                try:
                    result = await org_tool.ainvoke(params)
                    if result:
                        break
                except Exception as e:
                    logger.debug("Org fetch with params %s failed: %s", params, e)

            if not result:
                logger.warning("Empty response from ZohoBooks_list_organizations")
                return

            # Extract text from various response formats
            text = self._extract_text(result)
            if not text:
                logger.warning(
                    "Could not extract text from org response (type=%s): %s",
                    type(result).__name__, str(result)[:500],
                )
                return

            # Parse JSON — the response may contain the JSON directly or
            # it may be wrapped in extra text; try to find the JSON object
            data = self._parse_json_response(text)
            if not data:
                logger.warning("Could not parse org response as JSON: %s", text[:500])
                return

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

    @staticmethod
    def _extract_text(result) -> str | None:
        """Extract text content from various MCP tool response formats."""
        # Direct string
        if isinstance(result, str):
            return result.strip() or None

        # List of content blocks: [{"type": "text", "text": "..."}]
        if isinstance(result, list):
            for block in result:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "").strip() or None
                # Some adapters return ContentBlock objects with .text attr
                if hasattr(block, "text"):
                    return str(block.text).strip() or None
            # If list of strings, join them
            if all(isinstance(b, str) for b in result):
                return "\n".join(result).strip() or None

        # LangChain message objects (ToolMessage, AIMessage, etc.)
        if hasattr(result, "content"):
            content = result.content
            if isinstance(content, str):
                return content.strip() or None
            # content can be a list of blocks
            if isinstance(content, list):
                return MCPManager._extract_text(content)

        # Object with .text attribute (ContentBlock, TextBlock, etc.)
        if hasattr(result, "text"):
            return str(result.text).strip() or None

        # Last resort: stringify
        s = str(result).strip()
        return s if s else None

    @staticmethod
    def _parse_json_response(text: str) -> dict | None:
        """Parse JSON from text, handling cases where JSON is embedded in other text."""
        # Direct parse
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # Try to find a JSON object in the text (e.g., "Here are the orgs: {...}")
        start = text.find("{")
        if start != -1:
            # Find matching closing brace
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break
        return None
