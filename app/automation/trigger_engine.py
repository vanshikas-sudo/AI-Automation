"""
Trigger Engine — Evaluates rule conditions against fetched data.

For POLLING rules:
  1. Fetch data from MCP tool (e.g. ZohoBooks_list_invoices)
  2. Evaluate each condition against every data item
  3. Return items that match ALL conditions

For SCHEDULE rules:
  - No conditions to evaluate — just fire the actions directly.

Supported operators:
  gt, gte, lt, lte, eq, neq, contains, not_contains, in, between

Computed fields:
  days_overdue = (today - due_date).days
  days_since_created = (today - created_time).days
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from app.automation.models import Condition, EventRule, TriggerType

logger = logging.getLogger(__name__)

# Date fields that can be used to compute "days since" values
_DATE_FIELDS = {"due_date", "date", "created_time", "last_modified_time", "expiry_date"}


def evaluate_conditions(
    data_items: list[dict[str, Any]],
    conditions: list[Condition],
) -> list[dict[str, Any]]:
    """
    Filter data_items to only those matching ALL conditions.

    Args:
        data_items: List of dicts from MCP tool response (e.g. invoices).
        conditions: List of Condition objects from the rule.

    Returns:
        List of items matching all conditions.
    """
    if not conditions:
        return data_items

    matched: list[dict[str, Any]] = []
    for item in data_items:
        if _item_matches_all(item, conditions):
            matched.append(item)

    logger.info(
        "Trigger evaluation: %d/%d items matched %d condition(s)",
        len(matched), len(data_items), len(conditions),
    )
    return matched


def _item_matches_all(item: dict[str, Any], conditions: list[Condition]) -> bool:
    """Check if a single item matches ALL conditions."""
    for cond in conditions:
        value = _resolve_field(item, cond.field)
        if not _evaluate_operator(value, cond.operator, cond.value):
            return False
    return True


def _resolve_field(item: dict[str, Any], field: str) -> Any:
    """
    Resolve a field path from an item dict.

    Supports:
      - Simple: "total" → item["total"]
      - Nested: "contact.email" → item["contact"]["email"]
      - Computed: "days_overdue" → (today - due_date).days
    """
    # Computed fields
    if field == "days_overdue":
        return _compute_days_since(item, "due_date")
    if field == "days_since_created":
        return _compute_days_since(item, "created_time")
    if field.startswith("days_since_"):
        date_field = field[len("days_since_"):]
        return _compute_days_since(item, date_field)

    # Nested field access: "contact.email"
    parts = field.split(".")
    current: Any = item
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _compute_days_since(item: dict[str, Any], date_field: str) -> int | None:
    """Compute days between today and a date field value."""
    raw = item.get(date_field)
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            d = dt.date()
        elif isinstance(raw, datetime):
            d = raw.date()
        elif isinstance(raw, date):
            d = raw
        else:
            return None
        return (date.today() - d).days
    except (ValueError, TypeError):
        return None


def _evaluate_operator(actual: Any, operator: str, expected: Any) -> bool:
    """Evaluate a single condition operator."""
    if actual is None:
        return False

    try:
        # Numeric coercion for comparison operators
        if operator in ("gt", "gte", "lt", "lte", "between"):
            actual = float(actual)
            if operator == "between":
                if isinstance(expected, (list, tuple)) and len(expected) == 2:
                    return float(expected[0]) <= actual <= float(expected[1])
                return False
            expected = float(expected)

        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "lt":
            return actual < expected
        if operator == "lte":
            return actual <= expected
        if operator == "eq":
            return str(actual).lower() == str(expected).lower()
        if operator == "neq":
            return str(actual).lower() != str(expected).lower()
        if operator == "contains":
            return str(expected).lower() in str(actual).lower()
        if operator == "not_contains":
            return str(expected).lower() not in str(actual).lower()
        if operator == "in":
            if isinstance(expected, (list, tuple)):
                return actual in expected
            return str(actual) in str(expected)

        logger.warning("Unknown operator: %s", operator)
        return False

    except (ValueError, TypeError) as e:
        logger.debug("Condition eval error: %s %s %s — %s", actual, operator, expected, e)
        return False


def parse_mcp_response(raw_response: str) -> list[dict[str, Any]]:
    """
    Parse an MCP tool response into a list of data items.

    MCP tools return JSON strings. The data could be:
      - A list of objects directly
      - An object with a list under a known key (invoices, contacts, etc.)
    """
    try:
        data = json.loads(raw_response) if isinstance(raw_response, str) else raw_response
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse MCP response as JSON: %s", repr(raw_response[:200]))
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        # Look for the first key that contains a list of dicts
        for key in ("invoices", "contacts", "items", "bills", "estimates",
                     "salesorders", "purchaseorders", "expenses", "journals",
                     "creditnotes", "vendorcredits", "projects", "data", "records"):
            if key in data and isinstance(data[key], list):
                return data[key]
        # Fallback: if the dict itself looks like a single record, wrap it
        if "id" in data or "invoice_id" in data or "contact_id" in data:
            return [data]

    return []
