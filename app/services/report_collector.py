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
    tool_name = getattr(tool, 'name', '?')
    try:
        result = await tool.ainvoke(params)
        logger.info("[TOOL] %s → response type=%s", tool_name, type(result).__name__)

        # Extract text from various response formats
        text = _extract_tool_text(result)
        if not text:
            logger.warning("[TOOL] %s returned empty response (raw=%s)", tool_name, repr(result)[:200])
            return None

        logger.debug("[TOOL] %s → %d chars, starts=%s", tool_name, len(text), repr(text[:150]))

        # Try parsing as JSON
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                logger.info("[TOOL] %s → parsed OK, keys=%s", tool_name, list(parsed.keys()))
            else:
                logger.info("[TOOL] %s → parsed OK, type=%s len=%d", tool_name, type(parsed).__name__, len(parsed) if isinstance(parsed, list) else 0)
            return parsed
        except json.JSONDecodeError:
            # Response may be truncated — try to repair and salvage
            repaired = _repair_truncated_json(text)
            if repaired is not None:
                logger.info("[TOOL] %s → repaired truncated JSON OK", tool_name)
                return repaired
            logger.warning("[TOOL] %s: JSON parse failed (%d chars), start=%s, end=%s",
                          tool_name, len(text), repr(text[:100]), repr(text[-100:]))
            return None
    except Exception as e:
        logger.warning("[TOOL] %s call failed: %s", tool_name, e, exc_info=True)
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
        if texts:
            return "\n".join(texts)
        # If it's a plain data list (not content blocks), serialize it
        try:
            return json.dumps(result)
        except (TypeError, ValueError):
            return None

    # Dict with text key (single content block)
    if isinstance(result, dict):
        if "text" in result and "type" in result:
            return result["text"]
        # It's already structured data — serialize so json.loads can re-parse
        try:
            return json.dumps(result)
        except (TypeError, ValueError):
            return None

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


def _build_monthly_costs(bills: list, expenses: list, fy_start: str, fy_end: str) -> dict[str, float]:
    """Build monthly costs from bills + expenses within the fiscal year."""
    fy_months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep",
                 "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    monthly = {m: 0.0 for m in fy_months}

    for b in bills:
        date_str = b.get("date", "")
        if not date_str or date_str < fy_start or date_str > fy_end:
            continue
        month = _month_key(date_str)
        if month in monthly:
            monthly[month] += float(b.get("total", 0))

    for e in expenses:
        date_str = e.get("date", "")
        if not date_str or date_str < fy_start or date_str > fy_end:
            continue
        month = _month_key(date_str)
        if month in monthly:
            monthly[month] += float(e.get("total", e.get("amount", 0)))

    return monthly


def _build_expense_breakdown(bills: list, expenses: list) -> list[dict]:
    """Build expense breakdown by category from bills and expenses."""
    categories: dict[str, float] = defaultdict(float)

    for b in bills:
        # Use vendor_name as category since bills don't have expense categories
        cat = b.get("vendor_name", "") or b.get("reference_number", "Other Bills")
        if not cat:
            cat = "Other Bills"
        categories[cat] += float(b.get("total", 0))

    for e in expenses:
        cat = (e.get("category_name", "") or e.get("account_name", "")
               or e.get("description", "Other Expenses"))
        if not cat:
            cat = "Other Expenses"
        categories[cat] += float(e.get("total", e.get("amount", 0)))

    if not categories:
        return []

    total = sum(categories.values())
    breakdown = []
    for cat, amount in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        pct = (amount / total * 100) if total > 0 else 0
        breakdown.append({
            "category": cat[:30],  # Truncate long names
            "amount": amount,
            "percentage": round(pct, 1),
            "trend": "—",
        })

    return breakdown[:10]  # Top 10 categories


def _build_journal_report(journals: list, fy_start: str, fy_end: str) -> dict:
    """Build journal report data from journal entries."""
    if not journals:
        return {
            "summary": "No journal entries found for this period.",
            "total_entries": 0, "total_debit": 0, "total_credit": 0,
            "entries": [], "monthly_totals": [],
        }

    total_debit = 0.0
    total_credit = 0.0
    entries = []
    fy_months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep",
                 "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    monthly_debits = {m: 0.0 for m in fy_months}
    monthly_credits = {m: 0.0 for m in fy_months}

    for j in journals:
        j_date = j.get("journal_date", j.get("date", ""))
        j_number = j.get("journal_number", j.get("entry_number", ""))
        j_debit = float(j.get("total", j.get("debit_total", 0)))
        j_credit = float(j.get("total", j.get("credit_total", 0)))
        j_notes = j.get("notes", j.get("reference_number", ""))

        total_debit += j_debit
        total_credit += j_credit

        month = _month_key(j_date)
        if month in monthly_debits:
            monthly_debits[month] += j_debit
            monthly_credits[month] += j_credit

        if len(entries) < 20:
            entries.append({
                "date": j_date,
                "journal_number": j_number,
                "account": j.get("account_name", j_notes[:30] if j_notes else ""),
                "debit": j_debit,
                "credit": j_credit,
                "notes": (j_notes or "")[:40],
            })

    monthly_totals = [{"month": m, "debit": monthly_debits[m], "credit": monthly_credits[m]}
                      for m in fy_months]

    return {
        "summary": f"{len(journals)} journal entries recorded in this period.",
        "total_entries": len(journals),
        "total_debit": total_debit,
        "total_credit": total_credit,
        "entries": entries,
        "monthly_totals": monthly_totals,
    }


async def collect_report_data(agent, fiscal_year: str = "2025-2026", org_id: str = "",
                             tool_registry=None) -> dict:
    """
    Collect report data using direct MCP tool calls + LLM for insights only.

    Phase 1: Direct tool calls to fetch raw data (reliable)
    Phase 2: LLM generates insights from the structured data (small output)
    """
    import asyncio
    logger.info("[COLLECTOR] Starting direct data collection for FY %s (org=%s)", fiscal_year, org_id)

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

    logger.info("[COLLECTOR] Available tools (%d): %s", len(tool_map), list(tool_map.keys()))

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
        "expenses": "ZohoBooks_list_expenses",
        "journals": "ZohoBooks_list_journals",
    }

    for key, tool_name in tool_names.items():
        tool = tool_map.get(tool_name)
        if tool:
            params = qp if key == "invoices" else qp_basic
            tasks[key] = asyncio.create_task(_call_tool(tool, params))
        else:
            logger.warning("[COLLECTOR] Tool %s not found in tool_map", tool_name)

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

    # Log what we got back
    for key, val in results.items():
        if val is None:
            logger.warning("[COLLECTOR] %s → None (failed)", key)
        elif isinstance(val, dict):
            logger.info("[COLLECTOR] %s → dict keys=%s", key, list(val.keys()))
        else:
            logger.info("[COLLECTOR] %s → %s", key, type(val).__name__)

    # ── Phase 2: Structure the data in Python ────────────────────────

    invoices_data = results.get("invoices") or {}
    invoices = invoices_data.get("invoices", [])
    items_data = results.get("items") or {}
    items = items_data.get("items", [])
    bills_data = results.get("bills") or {}
    bills = bills_data.get("bills", [])
    expenses_data = results.get("expenses") or {}
    expenses = expenses_data.get("expenses", [])
    journals_data = results.get("journals") or {}
    journals = journals_data.get("journals", [])
    org_data = results.get("org") or {}
    org_name = org_data.get("organization", {}).get("name", "Organization")

    # Filter invoices to fiscal year
    fy_invoices = [inv for inv in invoices
                   if fy_start <= inv.get("date", "")[:10] <= fy_end]
    if not fy_invoices:
        fy_invoices = invoices  # Use all if none match the date range

    # Filter bills to fiscal year
    fy_bills = [b for b in bills
                if fy_start <= b.get("date", "")[:10] <= fy_end]
    if not fy_bills:
        fy_bills = bills

    # Filter expenses to fiscal year
    fy_expenses = [e for e in expenses
                   if fy_start <= e.get("date", "")[:10] <= fy_end]
    if not fy_expenses:
        fy_expenses = expenses

    # Filter journals to fiscal year
    fy_journals = [j for j in journals
                   if fy_start <= j.get("journal_date", j.get("date", ""))[:10] <= fy_end]
    if not fy_journals:
        fy_journals = journals

    logger.info("[COLLECTOR] Raw data: %d invoices (%d in FY), %d items, %d bills (%d in FY), "
                "%d expenses (%d in FY), %d journals (%d in FY)",
                len(invoices), len(fy_invoices), len(items),
                len(bills), len(fy_bills),
                len(expenses), len(fy_expenses),
                len(journals), len(fy_journals))

    # Compute financials
    total_sales = sum(float(inv.get("total", 0)) for inv in fy_invoices)
    total_bills = sum(float(b.get("total", 0)) for b in fy_bills)
    total_expense_amount = sum(float(e.get("total", e.get("amount", 0))) for e in fy_expenses)
    total_all_expenses = total_bills + total_expense_amount
    total_outstanding_ar = sum(float(inv.get("balance", 0)) for inv in fy_invoices)
    total_outstanding_ap = sum(float(b.get("balance", 0)) for b in fy_bills)
    overdue_ar = sum(float(inv.get("balance", 0)) for inv in fy_invoices
                     if inv.get("status") == "overdue")
    overdue_ap = sum(float(b.get("balance", 0)) for b in fy_bills
                     if b.get("status") == "overdue")

    gross_profit = total_sales - total_all_expenses
    net_income = gross_profit

    logger.info("[COLLECTOR] Financials: sales=%.2f, bills=%.2f, expenses=%.2f, "
                "gross_profit=%.2f, AR=%.2f, AP=%.2f",
                total_sales, total_bills, total_expense_amount,
                gross_profit, total_outstanding_ar, total_outstanding_ap)

    # Monthly sales
    monthly_sales = _build_monthly_sales(fy_invoices, fy_start, fy_end)

    # Monthly costs (from bills + expenses)
    monthly_costs = _build_monthly_costs(fy_bills, fy_expenses, fy_start, fy_end)

    # Item revenue analysis — use items catalog since list_invoices
    # doesn't include line_items (would need get_invoice per invoice)
    sorted_items = sorted(items, key=lambda x: float(x.get("rate", 0) or 0), reverse=True)
    top_5 = [{"name": it.get("name", "Unknown"), "revenue": float(it.get("rate", 0) or 0),
              "quantity": float(it.get("stock_on_hand", 0) or 0),
              "margin": float(it.get("rate", 0) or 0) - float(it.get("purchase_rate", 0) or 0)}
             for it in sorted_items[:5]]
    least_5 = [{"name": it.get("name", "Unknown"), "revenue": float(it.get("rate", 0) or 0),
                "quantity": float(it.get("stock_on_hand", 0) or 0),
                "margin": float(it.get("rate", 0) or 0) - float(it.get("purchase_rate", 0) or 0)}
               for it in sorted_items[-5:]] if len(sorted_items) >= 5 else []

    top_item = {}
    if sorted_items:
        it = sorted_items[0]
        top_item = {"name": it.get("name", "Unknown"),
                    "revenue": float(it.get("rate", 0) or 0),
                    "quantity_sold": float(it.get("stock_on_hand", 0) or 0),
                    "margin": float(it.get("rate", 0) or 0) - float(it.get("purchase_rate", 0) or 0)}

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

    # Monthly gross profit (revenue from sales, cost from bills+expenses)
    fy_months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep",
                 "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    monthly_gp = [{"month": m, "revenue": ms["amount"],
                   "cost": monthly_costs.get(m, 0.0)}
                  for m, ms in zip(fy_months, monthly_sales)]

    # Expense breakdown from bills + expenses
    expense_breakdown = _build_expense_breakdown(fy_bills, fy_expenses)

    # Journal report
    journal_report = _build_journal_report(fy_journals, fy_start, fy_end)

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
        "total_expenses": total_all_expenses,
        "net_income": net_income,
        "sales_summary": {"description": sales_description},
        "monthly_sales": monthly_sales,
        "sales_breakdown": sales_breakdown,
        "top_item": top_item,
        "gross_profit_details": {
            "total_revenue": total_sales,
            "cost_of_goods": total_all_expenses,
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
        "expense_breakdown": expense_breakdown,
        "journal_report": journal_report,
        "strategic_insights": insights if insights else [
            f"Total revenue of {total_sales:,.2f} recorded for FY {fiscal_year}.",
            f"Gross profit margin of {round((gross_profit / total_sales * 100) if total_sales > 0 else 0, 1)}%.",
            f"{len(ar_details)} outstanding invoices totaling {total_outstanding_ar:,.2f}.",
        ],
        "recommendations": recommendations if recommendations else [],
    }

    logger.info(
        "[COLLECTOR] Report data assembled for FY %s — "
        "sales=%.2f, expenses=%.2f, invoices=%d, bills=%d, expenses_items=%d, journals=%d",
        fiscal_year, total_sales, total_all_expenses,
        len(fy_invoices), len(fy_bills), len(fy_expenses), len(fy_journals),
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
