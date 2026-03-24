"""Quick end-to-end report data collection test."""
import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

from app.mcp.manager import MCPManager
from app.core.intent_router import Intent
from app.core.prompt_builder import build_prompt
from app.services.report_collector import collect_report_data


async def test():
    # 1. Setup MCP + LLM
    mgr = MCPManager()
    await mgr.initialize()
    print(f"Connected: {mgr.is_connected}, Org: {mgr.zoho_org_id}, Tools: {mgr.registry.tool_count}")

    # 2. Build agent
    from app.config import get_settings
    from app.providers.llm_factory import create_chat_model
    from langgraph.prebuilt import create_react_agent

    settings = get_settings()
    model = create_chat_model(settings)
    tools = mgr.registry.get_for_intent(Intent.REPORT)
    prompt = build_prompt(Intent.REPORT, zoho_org_id=mgr.zoho_org_id)
    agent = create_react_agent(model, tools, prompt=prompt)

    print(f"\nReport tools: {len(tools)}")
    print(f"System prompt:\n{prompt}\n")

    # 3. Collect data via collector (with tool_registry for direct calls)
    print("Collecting report data for FY 2025-2026...")
    from app.services.report_collector import collect_report_data

    data = await collect_report_data(
        agent, "2025-2026",
        org_id=mgr.zoho_org_id or "",
        tool_registry=mgr.registry,
    )

    print(f"\n=== RESULT ===")
    print(f"total_sales: {data.get('total_sales')}")
    print(f"gross_profit: {data.get('gross_profit')}")
    print(f"total_expenses: {data.get('total_expenses')}")
    print(f"net_income: {data.get('net_income')}")
    print(f"sales_summary: {data.get('sales_summary')}")
    print(f"monthly_sales count: {len(data.get('monthly_sales', []))}")
    print(f"top_5_items count: {len(data.get('top_5_items', []))}")
    print(f"invoices in AR: {len(data.get('accounts_receivable', {}).get('details', []))}")
    print(f"bills in AP: {len(data.get('accounts_payable', {}).get('details', []))}")
    print(f"insights: {len(data.get('strategic_insights', []))}")

    is_fallback = data.get('sales_summary', {}).get('description', '') == 'Data could not be retrieved. Please try again.'
    print(f"\nIs fallback data? {is_fallback}")

    await mgr.close()


asyncio.run(test())
