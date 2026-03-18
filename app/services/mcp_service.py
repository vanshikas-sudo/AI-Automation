import asyncio
import json
import logging

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.services.llm_provider import create_chat_model

logger = logging.getLogger(__name__)

# Transport types to attempt in order for Zoho MCP
_TRANSPORTS = ("streamable_http", "sse")
_MAX_RETRIES = 3
_RETRY_DELAY = 2  # seconds (doubles each retry)

# ─── Tool filtering ──────────────────────────────────────────────
# The Zoho MCP server exposes ~248 tools whose definitions exceed
# Claude's 200 000-token context window.  We whitelist only the
# exact tools we need.
_ALLOWED_TOOLS = {
    # Zoho Books — invoices
    "ZohoBooks_list_invoices",
    "ZohoBooks_get_invoice",
    "ZohoBooks_create_invoice",
    "ZohoBooks_update_invoice",
    # Zoho Books — contacts
    "ZohoBooks_list_contacts",
    "ZohoBooks_get_contact",
    "ZohoBooks_create_contact",
    # Zoho Books — items
    "ZohoBooks_list_items",
    "ZohoBooks_get_item",
    "ZohoBooks_create_item",
    # Zoho Books — bills / expenses
    "ZohoBooks_list_bills",
    "ZohoBooks_get_bill",
    "ZohoBooks_create_bill",
    "ZohoBooks_list_expenses",
    "ZohoBooks_get_expense",
    "ZohoBooks_create_expense",
    # Zoho Books — estimates / sales / purchase orders
    "ZohoBooks_list_estimates",
    "ZohoBooks_get_estimate",
    "ZohoBooks_create_estimate",
    "ZohoBooks_list_sales_orders",
    "ZohoBooks_get_sales_order",
    "ZohoBooks_list_purchase_orders",
    "ZohoBooks_get_purchase_order",
    # Zoho Books — payments & credit notes
    "ZohoBooks_create_customer_payment",
    "ZohoBooks_get_customer_payment",
    "ZohoBooks_list_credit_notes",
    "ZohoBooks_get_credit_note",
    # Zoho Books — org
    "ZohoBooks_get_organization",
    "ZohoBooks_list_organizations",
    # Zoho CRM — records
    "ZohoCRM_Get_Records",
    "ZohoCRM_Get_Record",
    "ZohoCRM_Create_Records",
    "ZohoCRM_Update_Record",
    "ZohoCRM_Delete_Record",
    "ZohoCRM_Search_Records",
    # Zoho CRM — metadata
    "ZohoCRM_Get_Modules",
    "ZohoCRM_Get_Fields",
}


def _filter_tools(tools: list) -> list:
    """Keep only explicitly whitelisted tools."""
    return [t for t in tools if t.name in _ALLOWED_TOOLS]


class MCPService:
    """Manages MCP server connections and the LangGraph ReAct agent."""

    def __init__(self):
        self.client: MultiServerMCPClient | None = None
        self.agent = None
        self._model = None
        self._connected = False
        self._zoho_org_id: str | None = None

    async def initialize(self) -> None:
        """Connect to MCP servers, fetch tools, and build the agent."""
        settings = get_settings()

        self._model = create_chat_model(settings)
        logger.info(
            "LLM initialized: provider=%s, model=%s",
            settings.llm_provider.value,
            settings.resolved_model,
        )

        tools = await self._connect_mcp(settings.mcp_zoho_url)

        # Auto-fetch Zoho org ID if not manually set
        org_id = settings.zoho_org_id
        if not org_id:
            org_id = await self._fetch_zoho_org_id(tools)
        self._zoho_org_id = org_id

        self.agent = create_react_agent(
            self._model, tools, prompt=self._build_prompt(settings),
        )

    def _build_prompt(self, settings) -> str:
        """Build system prompt, injecting Zoho org ID if available."""
        prompt = settings.llm_system_prompt
        if self._zoho_org_id:
            prompt += (
                f"\n\nZoho organization_id: {self._zoho_org_id}. "
                "Always pass this as the organization_id parameter when calling any ZohoBooks tool."
            )
        return prompt

    async def _fetch_zoho_org_id(self, tools: list) -> str | None:
        """Call ZohoBooks_list_organizations via MCP to auto-detect the org ID."""
        org_tool = next((t for t in tools if t.name == "ZohoBooks_list_organizations"), None)
        if not org_tool:
            logger.warning("ZohoBooks_list_organizations tool not found — cannot auto-detect org ID")
            return None

        try:
            logger.info("Auto-fetching Zoho organization ID…")
            result = await org_tool.ainvoke({})

            # Result is a list of content blocks: [{"type": "text", "text": "{...}"}]
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

            # Prefer the default org, otherwise take the first
            org = next((o for o in orgs if o.get("is_default_org")), orgs[0])
            org_id = str(org.get("organization_id", ""))
            org_name = org.get("name", "unknown")
            logger.info("Auto-detected Zoho org: %s (ID: %s)", org_name, org_id)
            return org_id

        except Exception:
            logger.warning("Failed to auto-fetch Zoho org ID", exc_info=True)

        return None

    async def _connect_mcp(self, url: str) -> list:
        """Try connecting to MCP with each transport, with retries."""
        for transport in _TRANSPORTS:
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    client = MultiServerMCPClient(
                        {
                            "zoho": {
                                "transport": transport,
                                "url": url,
                            },
                        }
                    )
                    tools = await client.get_tools()
                    all_count = len(tools)
                    tools = _filter_tools(tools)
                    self.client = client
                    self._connected = True
                    logger.info(
                        "Connected to Zoho MCP (%s) — kept %d/%d tools: %s",
                        transport, len(tools), all_count,
                        [t.name for t in tools],
                    )
                    return tools
                except Exception:
                    delay = _RETRY_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "MCP connect attempt %d/%d (%s) failed, retrying in %ds…",
                        attempt, _MAX_RETRIES, transport, delay,
                        exc_info=(attempt == _MAX_RETRIES),
                    )
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(delay)

            logger.info("Transport '%s' exhausted, trying next…", transport)

        logger.error(
            "Could not connect to any MCP server — agent will run without tools. "
            "Verify MCP_ZOHO_URL is correct and the server is reachable."
        )
        return []

    async def reconnect(self) -> None:
        """Re-connect to MCP and rebuild the agent with fresh tools."""
        await self.close()
        settings = get_settings()
        tools = await self._connect_mcp(settings.mcp_zoho_url)

        org_id = settings.zoho_org_id
        if not org_id:
            org_id = await self._fetch_zoho_org_id(tools)
        self._zoho_org_id = org_id

        self.agent = create_react_agent(
            self._model, tools, prompt=self._build_prompt(settings)
        )

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        """Cleanly shut down MCP client connections."""
        if self.client:
            self.client = None
            self._connected = False

    def get_agent(self):
        return self.agent
