"""
Report Data Collector
Fetches data from Zoho Books via the LangGraph agent and structures it
into the format expected by pdf_report_service.generate_fiscal_report_pdf().

The agent is instructed via a detailed prompt to gather all financial data
and return a structured JSON. Customer/vendor names and emails are stripped.
"""

import json
import logging
import re

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

# ─── The mega-prompt that instructs the agent on what to fetch ────

_REPORT_PROMPT = """
You are a financial data analyst. I need you to gather data from Zoho Books for fiscal year {fiscal_year} and return it as a JSON object. 

IMPORTANT PRIVACY RULES:
- Do NOT include any customer names, vendor names, or email addresses in any data.
- Replace customer/vendor identifiers with generic labels like "Customer A", "Customer B" etc.
- Only include invoice numbers, bill numbers, dates, amounts, and statuses.

Please gather the following data by calling the Zoho Books tools and compile it into a single JSON response. If some data is unavailable, use empty arrays/objects for those sections.

Return ONLY a valid JSON object with this exact structure (no markdown, no extra text):

{{
  "fiscal_year": "{fiscal_year}",
  "organization_name": "<org name from ZohoBooks_get_organization>",
  "total_sales": <total invoiced amount for the fiscal year>,
  "gross_profit": <total sales minus total cost of goods/bills>,
  "total_expenses": <total expenses + bills amount>,
  "net_income": <gross profit minus total expenses>,
  
  "sales_summary": {{
    "description": "<brief 2-3 sentence description of overall sales performance>"
  }},
  
  "monthly_sales": [
    {{"month": "Apr", "amount": 0}},
    {{"month": "May", "amount": 0}},
    {{"month": "Jun", "amount": 0}},
    {{"month": "Jul", "amount": 0}},
    {{"month": "Aug", "amount": 0}},
    {{"month": "Sep", "amount": 0}},
    {{"month": "Oct", "amount": 0}},
    {{"month": "Nov", "amount": 0}},
    {{"month": "Dec", "amount": 0}},
    {{"month": "Jan", "amount": 0}},
    {{"month": "Feb", "amount": 0}},
    {{"month": "Mar", "amount": 0}}
  ],
  
  "sales_breakdown": [
    {{"category": "<category>", "amount": 0, "percentage": 0, "invoice_count": 0}}
  ],
  
  "top_item": {{
    "name": "<item name>",
    "revenue": 0,
    "quantity_sold": 0,
    "margin": 0
  }},
  
  "gross_profit_details": {{
    "total_revenue": 0,
    "cost_of_goods": 0,
    "gross_profit": 0,
    "margin_pct": 0
  }},
  
  "monthly_gross_profit": [
    {{"month": "Apr", "revenue": 0, "cost": 0}}
  ],
  
  "top_5_items": [
    {{"name": "<item>", "revenue": 0, "quantity": 0, "margin": 0}}
  ],
  
  "least_5_items": [
    {{"name": "<item>", "revenue": 0, "quantity": 0, "margin": 0}}
  ],
  
  "accounts_receivable": {{
    "total_outstanding": 0,
    "current": 0,
    "overdue": 0,
    "aging": [
      {{"period": "Current", "amount": 0}},
      {{"period": "1-30 days", "amount": 0}},
      {{"period": "31-60 days", "amount": 0}},
      {{"period": "61-90 days", "amount": 0}},
      {{"period": "90+ days", "amount": 0}}
    ],
    "details": [
      {{"invoice_number": "", "date": "", "due_date": "", "amount": 0, "balance": 0, "status": ""}}
    ]
  }},
  
  "accounts_payable": {{
    "total_outstanding": 0,
    "current": 0,
    "overdue": 0,
    "details": [
      {{"bill_number": "", "date": "", "due_date": "", "amount": 0, "balance": 0, "status": ""}}
    ]
  }},
  
  "regional_data": [
    {{"region": "<city/state/country>", "amount": 0, "percentage": 0, "growth": "+X%"}}
  ],
  
  "expense_breakdown": [
    {{"category": "<expense category>", "amount": 0, "percentage": 0, "trend": "Up/Down/Stable"}}
  ],
  
  "journal_report": {{
    "summary": "<brief description of journal activity>",
    "total_entries": 0,
    "total_debit": 0,
    "total_credit": 0,
    "entries": [
      {{"date": "", "journal_number": "", "account": "", "debit": 0, "credit": 0, "notes": ""}}
    ],
    "monthly_totals": [
      {{"month": "Apr", "debit": 0, "credit": 0}}
    ]
  }},
  
  "strategic_insights": [
    "<insight 1 based on the data>",
    "<insight 2>",
    "<insight 3>",
    "<insight 4>",
    "<insight 5>"
  ],
  
  "recommendations": [
    {{"title": "<short title>", "description": "<detailed recommendation>", "priority": "High"}},
    {{"title": "<short title>", "description": "<detailed recommendation>", "priority": "Medium"}},
    {{"title": "<short title>", "description": "<detailed recommendation>", "priority": "Low"}}
  ]
}}

Steps to gather data:
1. Call ZohoBooks_get_organization to get the org name
2. Call ZohoBooks_list_invoices to get all invoices for the fiscal year (use date filters if possible, paginate with per_page=200)
3. Call ZohoBooks_list_items to get item catalog with prices/costs
4. Call ZohoBooks_list_bills to get all bills/payable data
5. Call ZohoBooks_list_expenses to get all expenses
6. Call ZohoBooks_list_estimates for any estimates
7. Call ZohoBooks_list_sales_orders for sales order data
8. Call ZohoBooks_list_purchase_orders for purchase order data
9. Call ZohoBooks_list_credit_notes for credit data

From the data collected:
- Calculate monthly sales from invoice dates
- Determine top/bottom items by revenue from invoice line items
- Calculate gross profit as revenue minus cost of goods
- Categorize expenses by type
- Determine regional data from invoice billing addresses/states
- Build aging analysis from invoice due dates and balances
- Create journal entries summary from any available transaction data
- Generate strategic insights based on patterns in the data

REMEMBER: Strip ALL customer names, vendor names, and email addresses from the output.
Return ONLY the JSON object, nothing else.
"""


def _extract_json_from_response(text: str) -> dict | None:
    """Extract JSON from the agent's response, handling markdown code blocks."""
    # Try direct parse first
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try extracting from markdown code block
    patterns = [
        r"```json\s*\n(.*?)\n\s*```",
        r"```\s*\n(.*?)\n\s*```",
        r"\{[\s\S]*\}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            candidate = match.group(1) if match.lastindex else match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return None


def _strip_pii(data: dict) -> dict:
    """Remove any customer/vendor names and emails that might have leaked through."""
    pii_keys = {"customer_name", "vendor_name", "contact_name", "email",
                "customer_email", "vendor_email", "contact_email", "name"}

    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if k.lower() in pii_keys and isinstance(v, str) and "@" in v:
                continue  # Skip email fields
            cleaned[k] = _strip_pii(v)
        return cleaned
    elif isinstance(data, list):
        return [_strip_pii(item) for item in data]
    return data


async def collect_report_data(agent, fiscal_year: str = "2025-2026") -> dict:
    """
    Use the LangGraph agent to collect all data from Zoho Books
    and return structured data for PDF generation.

    Args:
        agent: The LangGraph ReAct agent with MCP tools
        fiscal_year: Fiscal year string (e.g., "2025-2026")

    Returns:
        Dictionary of structured report data
    """
    prompt = _REPORT_PROMPT.format(fiscal_year=fiscal_year)

    try:
        import asyncio
        logger.info("[COLLECTOR] Invoking agent for FY %s …", fiscal_year)

        response = await asyncio.wait_for(
            agent.ainvoke({"messages": [HumanMessage(content=prompt)]}),
            timeout=240,  # 4-minute hard timeout on agent call
        )

        # Get the final AI message
        reply_text = ""
        for msg in reversed(response["messages"]):
            if isinstance(msg, AIMessage) and msg.content:
                reply_text = msg.content.strip()
                break

        if not reply_text:
            logger.error("[COLLECTOR] Agent returned empty response for report data")
            return _get_fallback_data(fiscal_year)

        logger.info("[COLLECTOR] Agent responded (%d chars), parsing JSON…", len(reply_text))
        data = _extract_json_from_response(reply_text)
        if not data:
            logger.error("[COLLECTOR] Could not parse JSON from agent response: %s",
                        reply_text[:500])
            return _get_fallback_data(fiscal_year)

        # Strip any PII that leaked through
        data = _strip_pii(data)
        data["fiscal_year"] = fiscal_year

        logger.info("[COLLECTOR] Report data collected successfully for FY %s", fiscal_year)
        return data

    except asyncio.TimeoutError:
        logger.error("[COLLECTOR] Agent call timed out for FY %s", fiscal_year)
        return _get_fallback_data(fiscal_year)
    except Exception as e:
        logger.exception("[COLLECTOR] Failed to collect report data: %s", e)
        return _get_fallback_data(fiscal_year)


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
