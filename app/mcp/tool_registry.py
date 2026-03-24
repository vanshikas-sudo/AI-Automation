"""
Tool Registry — Whitelist, cache, and scope MCP tools by intent.

Responsibilities:
  1. Filter raw MCP tools against the whitelist (~40 tools from ~248)
  2. Cache tool schemas in memory (no repeated MCP fetches)
  3. Provide scoped tool subsets per intent (biggest token saver)

Tool groups are organized by business domain so the intent router
can load only what's needed for each request.
"""

import logging
from app.core.intent_router import Intent

logger = logging.getLogger(__name__)

# ─── Whitelist: all tools we ever want, grouped by domain ────────

TOOL_GROUPS: dict[str, set[str]] = {
    "invoices": {
        "ZohoBooks_list_invoices",
        "ZohoBooks_get_invoice",
        "ZohoBooks_create_invoice",
        "ZohoBooks_list_invoice_comments",
        "ZohoBooks_list_project_invoices",
    },
    "contacts": {
        "ZohoBooks_list_contacts",
        "ZohoBooks_get_contact",
        "ZohoBooks_get_contact_address",
        "ZohoBooks_list_contact_persons",
        "ZohoBooks_get_contact_person",
    },
    "items": {
        "ZohoBooks_list_items",
        "ZohoBooks_get_item",
    },
    "bills": {
        "ZohoBooks_list_bills",
        "ZohoBooks_get_bill",
        "ZohoBooks_list_bill_payments",
    },
    "sales_orders": {
        "ZohoBooks_list_sales_orders",
    },
    "payments": {
        "ZohoBooks_get_customer_payment",
        "ZohoBooks_list_vendor_payments",
        "ZohoBooks_get_sales_receipt",
    },
    "accounting": {
        "ZohoBooks_list_chart_of_accounts",
        "ZohoBooks_get_chart_of_account",
        "ZohoBooks_list_chart_of_account_transactions",
        "ZohoBooks_get_opening_balance",
        "ZohoBooks_get_fixed_asset",
    },
    "organization": {
        "ZohoBooks_get_organization",
        "ZohoBooks_list_organizations",
        "ZohoBooks_list_locations",
    },
}

# Union of all whitelisted tool names
ALL_ALLOWED_TOOLS: set[str] = set()
for _tools in TOOL_GROUPS.values():
    ALL_ALLOWED_TOOLS.update(_tools)

# ─── Pre-built scoped sets per intent ────────────────────────────

# ZOHO_CRUD: everything (full CRUD across all domains)
_ZOHO_CRUD_TOOLS: set[str] = set(ALL_ALLOWED_TOOLS)

# REPORT: read-only data collection (no create/update/delete)
_REPORT_TOOLS: set[str] = set()
for group in ("invoices", "items", "bills", "sales_orders", "payments",
              "accounting", "organization"):
    _REPORT_TOOLS.update(TOOL_GROUPS[group])
# Exclude write tools from report
_REPORT_TOOLS -= {"ZohoBooks_create_invoice"}

# ─── Intent → tool name set mapping ─────────────────────────────
_INTENT_TOOL_MAP: dict[Intent, set[str]] = {
    Intent.ZOHO_CRUD: _ZOHO_CRUD_TOOLS,
    Intent.REPORT: _REPORT_TOOLS,
    Intent.CHAT: set(),      # no tools
    Intent.CLEAR: set(),     # no tools
}


class ToolRegistry:
    """Filters, caches, and serves MCP tool objects."""

    def __init__(self):
        self._all_tools: list = []          # filtered tool objects
        self._tool_map: dict[str, object] = {}  # name → tool object

    def register(self, raw_tools: list) -> None:
        """Filter raw MCP tools against the whitelist and cache them."""
        self._all_tools = [t for t in raw_tools if t.name in ALL_ALLOWED_TOOLS]
        self._tool_map = {t.name: t for t in self._all_tools}
        logger.info(
            "ToolRegistry: kept %d/%d tools",
            len(self._all_tools), len(raw_tools),
        )

    def get_all(self) -> list:
        """Return all whitelisted tools."""
        return list(self._all_tools)

    def get_by_names(self, names: set[str]) -> list:
        """Return tool objects whose names are in the given set."""
        return [self._tool_map[n] for n in names if n in self._tool_map]

    def get_for_intent(self, intent: Intent) -> list:
        """Return the scoped tool subset for the given intent."""
        names = _INTENT_TOOL_MAP.get(intent, set())
        tools = self.get_by_names(names)
        logger.debug(
            "Tools for intent %s: %d tools", intent.value, len(tools),
        )
        return tools

    def get_tool(self, name: str):
        """Get a single tool by name, or None."""
        return self._tool_map.get(name)

    @property
    def tool_count(self) -> int:
        return len(self._all_tools)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tool_map.keys())
