"""
Intent Router — Pre-LLM intent classification via regex.

Classifies user messages into intents BEFORE hitting the LLM,
so we only load relevant tools per request (biggest token saver).

Intents:
  CLEAR      → "clear history", "reset", "start over"
  REPORT     → "generate fiscal report", "annual report FY 2025"
  ZOHO_CRUD  → "create invoice", "list contacts", "update record"
  CHAT       → everything else ("hello", "what can you do?", "thanks")
"""

import re
from enum import Enum


class Intent(str, Enum):
    ZOHO_CRUD = "zoho_crud"
    REPORT = "report"
    CLEAR = "clear"
    CHAT = "chat"


# (intent, compiled_pattern) — checked in order, first match wins
_PATTERNS: list[tuple[Intent, re.Pattern]] = [
    (Intent.CLEAR, re.compile(
        r"\b(clear\s*(chat|session|history)|reset\s*(chat|conversation)|start\s*over|new\s*chat)\b",
        re.IGNORECASE,
    )),
    (Intent.REPORT, re.compile(
        r"\b(fiscal\s*(year)?\s*report|annual\s*report|year(ly)?\s*report|"
        r"generate\s*report|fy\s*report|financial\s*report|revenue\s*report)\b",
        re.IGNORECASE,
    )),
    (Intent.ZOHO_CRUD, re.compile(
        r"\b(invoice|invoices|bill\s*to|create\s*invoice|send\s*invoice|INV-"
        r"|contact|contacts|customer|customers|vendor|vendors|supplier|suppliers"
        r"|item|items|product|products|inventory|stock|sku|catalog"
        r"|bill|bills|expense|expenses|spending|expenditure|cost|costs"
        r"|estimate|estimates|quotation|quote"
        r"|sales?\s*order|SO-|purchase\s*order|PO-|procurement"
        r"|payment|receivable|payable|credit\s*note"
        r"|journal|ledger|debit|credit|accounting"
        r"|crm|lead|leads|deal|deals|pipeline|opportunity|module|modules"
        r"|zoho)\b",
        re.IGNORECASE,
    )),
]

# Fiscal year extraction
_FY_RANGE = re.compile(r"(20\d{2})\s*[-–/]\s*(20\d{2})")
_FY_SINGLE = re.compile(r"\b(20\d{2})\b")


def classify(text: str) -> Intent:
    """Classify a user message into an intent. Zero LLM cost."""
    for intent, pattern in _PATTERNS:
        if pattern.search(text):
            return intent
    return Intent.CHAT


def extract_fiscal_year(text: str) -> str:
    """Extract fiscal year from text. Returns default if not found."""
    m = _FY_RANGE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _FY_SINGLE.search(text)
    if m:
        year = int(m.group(1))
        return f"{year}-{year + 1}"
    return "2024-2025"
