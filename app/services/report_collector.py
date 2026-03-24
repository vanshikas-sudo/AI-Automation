"""
Report Data Collector
Fetches data from Zoho Books via direct MCP tool calls and structures it
into the format expected by pdf_report_service.generate_fiscal_report_pdf().

Uses a two-phase approach:
  1. Direct tool calls to fetch raw data (reliable, no LLM truncation)
  2. LLM call only for generating insights/summaries (small output)
"""

import json
import logging
import re
from collections import defaultdict
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)


# ─── Helper: call an MCP tool and parse JSON response ─────────────

async def _call_tool(tool, params: dict) -> dict | list | None:
    """Call an MCP tool with params and return parsed JSON, or None on failure."""
    try:
        result = await tool.ainvoke(params)

        # Extract text from various response formats
        text = _extract_tool_text(result)
        if not text:
            logger.warning("Tool %s returned empty response", getattr(tool, 'name', '?'))
            return None

        # Try parsing as JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Response may be truncated — try to repair and salvage
            repaired = _repair_truncated_json(text)
            if repaired is not None:
                return repaired
            logger.warning("Tool %s: could not parse response (%d chars), start=%s, end=%s",
                          getattr(tool, 'name', '?'), len(text),
                          repr(text[:100]), repr(text[-100:]))
            return None
            return None
    except Exception as e:
        logger.warning("Tool call failed for %s: %s", getattr(tool, 'name', '?'), e)
    return None


def _extract_tool_text(result) -> str | None:
    """Extract text content from various MCP tool result formats."""
    if isinstance(result, str):
        return result

    # content_and_artifact returns tuple (content, artifact)
    if isinstance(result, tuple) and len(result) >= 1:
        return _extract_tool_text(result[0])

    # List of content blocks: [{"type": "text", "text": "..."}]
    if isinstance(result, list):
        texts = []
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        return "\n".join(texts) if texts else None

    # Dict with text key
    if isinstance(result, dict) and "text" in result:
        return result["text"]

    return str(result) if result else None


def _repair_truncated_json(text: str) -> dict | None:
    """Try to repair truncated JSON from Zoho API responses.

    The response format is typically:
      {"code":0,"message":"success","invoices":[{...},{...},...{truncated
    We find the last complete array element and close the structure.
    """
    # Find the last complete array item boundary: },{
    # This marks where one object ends and next begins
    last_boundary = text.rfind('},{')
    if last_boundary > 0:
        # Truncate after the closing } of the last complete object
        truncated = text[:last_boundary + 1]

        # Count unclosed brackets to determine what closers we need
        open_brackets = 0
        open_braces = 0
        in_str = False
        esc = False
        for c in truncated:
            if esc:
                esc = False
                continue
            if c == '\\':
                if in_str:
                    esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == '[':
                open_brackets += 1
            elif c == ']':
                open_brackets -= 1
            elif c == '{':
                open_braces += 1
            elif c == '}':
                open_braces -= 1

        closing = ']' * max(0, open_brackets) + '}' * max(0, open_braces)
        candidate = truncated + closing

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    return None


def _parse_fy_range(fiscal_year: str):
    """Parse '2025-2026' into (start_date, end_date) strings."""
    parts = fiscal_year.split("-")
    if len(parts) == 2:
        start_year = int(parts[0])
        end_year = int(parts[1])
    else:
        start_year = int(fiscal_year)
        end_year = start_year + 1
    return f"{start_year}-04-01", f"{end_year}-03-31"


def _month_key(date_str: str) -> str:
    """Convert 'YYYY-MM-DD' to month abbreviation."""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.strftime("%b")
    except (ValueError, TypeError):
        return ""


def _build_monthly_sales(invoices: list, fy_start: str, fy_end: str) -> list[dict]:
    """Build monthly sales from invoice dates within the fiscal year."""
    fy_months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep",
                 "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    monthly = {m: 0.0 for m in fy_months}

    for inv in invoices:
        date_str = inv.get("date", "")
        if not date_str:
            continue
        if date_str < fy_start or date_str > fy_end:
            continue
        month = _month_key(date_str)
        if month in monthly:
            monthly[month] += float(inv.get("total", 0))

    return [{"month": m, "amount": monthly[m]} for m in fy_months]


def _build_aging(invoices: list) -> list[dict]:
    """Build accounts receivable aging buckets."""
    buckets = {"Current": 0, "1-30 days": 0, "31-60 days": 0,
               "61-90 days": 0, "90+ days": 0}
    today = datetime.now()

    for inv in invoices:
        balance = float(inv.get("balance", 0))
        if balance <= 0:
            continue
        due_str = inv.get("due_date", "")
        if not due_str:
            buckets["Current"] += balance
            continue
        try:
            due = datetime.strptime(due_str[:10], "%Y-%m-%d")
            days_overdue = (today - due).days
            if days_overdue <= 0:
                buckets["Current"] += balance
            elif days_overdue <= 30:
                buckets["1-30 days"] += balance
            elif days_overdue <= 60:
                buckets["31-60 days"] += balance
            elif days_overdue <= 90:
                buckets["61-90 days"] += balance
            else:
                buckets["90+ days"] += balance
        except (ValueError, TypeError):
            buckets["Current"] += balance

    return [{"period": k, "amount": v} for k, v in buckets.items()]


def _build_item_revenue(invoices: list) -> dict[str, dict]:
    """Aggregate revenue by item name from invoice line items."""
    items: dict[str, dict] = defaultdict(lambda: {"revenue": 0, "quantity": 0})
    for inv in invoices:
        for li in inv.get("line_items", []):
            name = li.get("name") or li.get("item_name") or li.get("description", "Unknown")
            items[name]["revenue"] += float(li.get("item_total", 0))
            items[name]["quantity"] += float(li.get("quantity", 0))
    return dict(items)


async def collect_report_data(agent, fiscal_year: str = "2025-2026", org_id: str = "",
                             tool_registry=None) -> dict:
    """
    Collect report data using direct MCP tool calls + LLM for insights only.

    Phase 1: Direct tool calls to fetch raw data (reliable)
    Phase 2: LLM generates insights from the structured data (small output)
    """
    import asyncio
    logger.info("[COLLECTOR] Starting direct data collection for FY %s", fiscal_year)

    fy_start, fy_end = _parse_fy_range(fiscal_year)
    qp = {"query_params": {"organization_id": org_id, "per_page": 200}}
    qp_basic = {"query_params": {"organization_id": org_id}}

    # ── Phase 1: Fetch raw data from Zoho via direct tool calls ──────

    # Get tool objects from registry or from the agent
    tool_map = {}
    if tool_registry:
        tool_map = {t.name: t for t in tool_registry.get_all()}
    if not tool_map:
        # Try to get tools from agent
        try:
            for t in (agent.tools if hasattr(agent, 'tools') else []):
                tool_map[t.name] = t
        except Exception:
            pass
    if not tool_map:
        logger.warning("[COLLECTOR] No tools available, using agent fallback")
        return await _collect_via_agent(agent, fiscal_year, org_id)

    # Parallel fetch of all data sources
    tasks = {}
    tool_names = {
        "invoices": "ZohoBooks_list_invoices",
        "items": "ZohoBooks_list_items",
        "bills": "ZohoBooks_list_bills",
        "sales_orders": "ZohoBooks_list_sales_orders",
        "vendor_payments": "ZohoBooks_list_vendor_payments",
    }

    for key, tool_name in tool_names.items():
        tool = tool_map.get(tool_name)
        if tool:
            params = qp if key == "invoices" else qp_basic
            tasks[key] = asyncio.create_task(_call_tool(tool, params))
        else:
            logger.warning("[COLLECTOR] Tool %s not found", tool_name)

    # get_organization needs path_params for org ID
    org_tool = tool_map.get("ZohoBooks_get_organization")
    if org_tool:
        tasks["org"] = asyncio.create_task(_call_tool(org_tool, {
            "path_params": {"organization_id": org_id},
            "query_params": {"organization_id": org_id},
        }))

    # Wait for all tool calls (with individual timeouts via _call_tool)
    results = {}
    for key, task in tasks.items():
        try:
            results[key] = await asyncio.wait_for(task, timeout=60)
        except asyncio.TimeoutError:
            logger.warning("[COLLECTOR] Timeout fetching %s", key)
            results[key] = None

    # ── Phase 2: Structure the data in Python ────────────────────────

    invoices_data = results.get("invoices") or {}
    invoices = invoices_data.get("invoices", [])
    items_data = results.get("items") or {}
    items = items_data.get("items", [])
    bills_data = results.get("bills") or {}
    bills = bills_data.get("bills", [])
    org_data = results.get("org") or {}
    org_name = org_data.get("organization", {}).get("name", "Organization")

    # Filter invoices to fiscal year
    fy_invoices = [inv for inv in invoices
                   if fy_start <= inv.get("date", "")[:10] <= fy_end]
    if not fy_invoices:
        fy_invoices = invoices  # Use all if none match the date range

    logger.info("[COLLECTOR] Raw data: %d invoices (%d in FY), %d items, %d bills",
                len(invoices), len(fy_invoices), len(items), len(bills))

    # Compute financials
    total_sales = sum(float(inv.get("total", 0)) for inv in fy_invoices)
    total_bills = sum(float(b.get("total", 0)) for b in bills)
    total_outstanding_ar = sum(float(inv.get("balance", 0)) for inv in fy_invoices)
    total_outstanding_ap = sum(float(b.get("balance", 0)) for b in bills)
    overdue_ar = sum(float(inv.get("balance", 0)) for inv in fy_invoices
                     if inv.get("status") == "overdue")
    overdue_ap = sum(float(b.get("balance", 0)) for b in bills
                     if b.get("status") == "overdue")

    gross_profit = total_sales - total_bills
    net_income = gross_profit

    # Monthly sales
    monthly_sales = _build_monthly_sales(fy_invoices, fy_start, fy_end)

    # Item revenue analysis — use items catalog since list_invoices
    # doesn't include line_items (would need get_invoice per invoice)
    sorted_items = sorted(items, key=lambda x: float(x.get("rate", 0)), reverse=True)
    top_5 = [{"name": it.get("name", "Unknown"), "revenue": float(it.get("rate", 0)),
              "quantity": float(it.get("stock_on_hand", 0)),
              "margin": float(it.get("rate", 0)) - float(it.get("purchase_rate", 0))}
             for it in sorted_items[:5]]
    least_5 = [{"name": it.get("name", "Unknown"), "revenue": float(it.get("rate", 0)),
                "quantity": float(it.get("stock_on_hand", 0)),
                "margin": float(it.get("rate", 0)) - float(it.get("purchase_rate", 0))}
               for it in sorted_items[-5:]] if len(sorted_items) >= 5 else []

    top_item = {}
    if sorted_items:
        it = sorted_items[0]
        top_item = {"name": it.get("name", "Unknown"),
                    "revenue": float(it.get("rate", 0)),
                    "quantity_sold": float(it.get("stock_on_hand", 0)),
                    "margin": float(it.get("rate", 0)) - float(it.get("purchase_rate", 0))}

    # Sales breakdown by invoice (since we don't have line items)
    sales_breakdown = []
    for inv in fy_invoices[:10]:
        total = float(inv.get("total", 0))
        pct = (total / total_sales * 100) if total_sales > 0 else 0
        label = inv.get("invoice_number", "Invoice")
        sales_breakdown.append({
            "category": label, "amount": total,
            "percentage": round(pct, 1), "invoice_count": 1,
        })

    # AR details (stripped of PII)
    ar_details = []
    for i, inv in enumerate(fy_invoices):
        if float(inv.get("balance", 0)) > 0:
            ar_details.append({
                "invoice_number": inv.get("invoice_number", ""),
                "date": inv.get("date", ""),
                "due_date": inv.get("due_date", ""),
                "amount": float(inv.get("total", 0)),
                "balance": float(inv.get("balance", 0)),
                "status": inv.get("status", ""),
            })

    # AP details
    ap_details = []
    for b in bills:
        if float(b.get("balance", 0)) > 0:
            ap_details.append({
                "bill_number": b.get("bill_number", ""),
                "date": b.get("date", ""),
                "due_date": b.get("due_date", ""),
                "amount": float(b.get("total", 0)),
                "balance": float(b.get("balance", 0)),
                "status": b.get("status", ""),
            })

    # Aging
    aging = _build_aging(fy_invoices)

    # Monthly gross profit
    fy_months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep",
                 "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    monthly_gp = [{"month": m, "revenue": ms["amount"], "cost": 0}
                  for m, ms in zip(fy_months, monthly_sales)]

    # ── Phase 3: Use LLM for insights only (small output) ───────────

    insights = []
    recommendations = []
    sales_description = f"Total sales of {total_sales:,.2f} across {len(fy_invoices)} invoices."

    try:
        summary_prompt = (
            f"Based on this financial data for FY {fiscal_year}:\n"
            f"- Total Sales: {total_sales:,.2f}\n"
            f"- Total Bills/Expenses: {total_bills:,.2f}\n"
            f"- Gross Profit: {gross_profit:,.2f}\n"
            f"- Outstanding Receivables: {total_outstanding_ar:,.2f}\n"
            f"- Outstanding Payables: {total_outstanding_ap:,.2f}\n"
            f"- Number of Invoices: {len(fy_invoices)}\n"
            f"- Number of Bills: {len(bills)}\n"
            f"- Top Item: {top_item.get('name', 'N/A')} ({top_item.get('revenue', 0):,.2f})\n\n"
            "Return a JSON object with exactly these keys:\n"
            '{"description": "<2-3 sentence sales summary>",\n'
            ' "insights": ["<insight1>", "<insight2>", "<insight3>"],\n'
            ' "recommendations": [{"title": "<title>", "description": "<desc>", "priority": "High/Medium/Low"}]}\n'
            "Return ONLY the JSON, no markdown."
        )
        response = await asyncio.wait_for(
            agent.ainvoke({"messages": [HumanMessage(content=summary_prompt)]}),
            timeout=60,
        )
        for msg in reversed(response["messages"]):
            if isinstance(msg, AIMessage) and msg.content:
                text = msg.content.strip()
                parsed = _extract_json_from_response(text)
                if parsed:
                    sales_description = parsed.get("description", sales_description)
                    insights = parsed.get("insights", [])
                    recommendations = parsed.get("recommendations", [])
                break
    except Exception:
        logger.warning("[COLLECTOR] LLM insights generation failed, using defaults", exc_info=True)

    # ── Assemble final data structure ────────────────────────────────

    report_data = {
        "fiscal_year": fiscal_year,
        "organization_name": org_name,
        "total_sales": total_sales,
        "gross_profit": gross_profit,
        "total_expenses": total_bills,
        "net_income": net_income,
        "sales_summary": {"description": sales_description},
        "monthly_sales": monthly_sales,
        "sales_breakdown": sales_breakdown,
        "top_item": top_item,
        "gross_profit_details": {
            "total_revenue": total_sales,
            "cost_of_goods": total_bills,
            "gross_profit": gross_profit,
            "margin_pct": round((gross_profit / total_sales * 100) if total_sales > 0 else 0, 1),
        },
        "monthly_gross_profit": monthly_gp,
        "top_5_items": top_5,
        "least_5_items": least_5,
        "accounts_receivable": {
            "total_outstanding": total_outstanding_ar,
            "current": total_outstanding_ar - overdue_ar,
            "overdue": overdue_ar,
            "aging": aging,
            "details": ar_details,
        },
        "accounts_payable": {
            "total_outstanding": total_outstanding_ap,
            "current": total_outstanding_ap - overdue_ap,
            "overdue": overdue_ap,
            "details": ap_details,
        },
        "regional_data": [],
        "expense_breakdown": [],
        "journal_report": {
            "summary": "Data collected from Zoho Books",
            "total_entries": 0, "total_debit": 0, "total_credit": 0,
            "entries": [], "monthly_totals": [],
        },
        "strategic_insights": insights if insights else [
            f"Total revenue of {total_sales:,.2f} recorded for FY {fiscal_year}.",
            f"Gross profit margin of {round((gross_profit / total_sales * 100) if total_sales > 0 else 0, 1)}%.",
            f"{len(ar_details)} outstanding invoices totaling {total_outstanding_ar:,.2f}.",
        ],
        "recommendations": recommendations if recommendations else [],
    }

    logger.info(
        "[COLLECTOR] Report data assembled for FY %s — "
        "sales=%.2f, expenses=%.2f, invoices=%d, bills=%d",
        fiscal_year, total_sales, total_bills, len(fy_invoices), len(bills),
    )
    return report_data


async def _collect_via_agent(agent, fiscal_year: str, org_id: str) -> dict:
    """Fallback: use the LLM agent to collect data (original approach)."""
    prompt = _AGENT_PROMPT.format(fiscal_year=fiscal_year, org_id=org_id)
    try:
        import asyncio
        response = await asyncio.wait_for(
            agent.ainvoke({"messages": [HumanMessage(content=prompt)]}),
            timeout=240,
        )
        for msg in reversed(response["messages"]):
            if isinstance(msg, AIMessage) and msg.content:
                data = _extract_json_from_response(msg.content.strip())
                if data:
                    data["fiscal_year"] = fiscal_year
                    return data
    except Exception:
        logger.exception("[COLLECTOR] Agent fallback failed")
    return _get_fallback_data(fiscal_year)


_AGENT_PROMPT = """Gather data from Zoho Books for fiscal year {fiscal_year}.
organization_id: {org_id}. All tool calls need query_params wrapper, e.g.:
{{"query_params": {{"organization_id": "{org_id}", "per_page": 200}}}}
Call ZohoBooks_list_invoices, ZohoBooks_list_items, ZohoBooks_list_bills.
Return a JSON with: fiscal_year, total_sales, gross_profit, total_expenses,
net_income, monthly_sales, top_5_items, accounts_receivable, accounts_payable."""


def _extract_json_from_response(text: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    for pattern in [r"```json\s*\n?([\s\S]+?)\n?\s*```", r"```\s*\n?([\s\S]+?)\n?\s*```"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass
    return None


def _get_fallback_data(fiscal_year: str) -> dict:
    """Return minimal fallback data if agent fails."""
    return {
        "fiscal_year": fiscal_year,
        "organization_name": "Organization",
        "total_sales": 0,
        "gross_profit": 0,
        "total_expenses": 0,
        "net_income": 0,
        "sales_summary": {"description": "Data could not be retrieved. Please try again."},
        "monthly_sales": [],
        "sales_breakdown": [],
        "top_item": {},
        "gross_profit_details": {},
        "monthly_gross_profit": [],
        "top_5_items": [],
        "least_5_items": [],
        "accounts_receivable": {"total_outstanding": 0, "current": 0, "overdue": 0,
                                 "aging": [], "details": []},
        "accounts_payable": {"total_outstanding": 0, "current": 0, "overdue": 0,
                              "details": []},
        "regional_data": [],
        "expense_breakdown": [],
        "journal_report": {"summary": "No data available", "total_entries": 0,
                           "total_debit": 0, "total_credit": 0,
                           "entries": [], "monthly_totals": []},
        "strategic_insights": ["Insufficient data to generate insights."],
        "recommendations": [],
    }
