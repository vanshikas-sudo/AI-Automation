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

import difflib
import re
from enum import Enum


class Intent(str, Enum):
    ZOHO_CRUD = "zoho_crud"
    REPORT = "report"
    AUTOMATION = "automation"
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
    (Intent.AUTOMATION, re.compile(
        r"\b(list\s*(my\s*)?automations?|my\s*automations?|show\s*(my\s*)?automations?"
        r"|create\s*automations?|new\s*automations?|add\s*automations?|set\s*up\s*automations?"
        r"|pause\s*automations?|resume\s*automations?|delete\s*automations?|remove\s*automations?"
        r"|trigger\s*automations?|run\s*automations?|test\s*automations?"
        r"|reschedule\s*automations?|change\s*(schedule|time)\s*(of\s*)?(automation|rule)?"
        r"|update\s*(schedule|time)\s*(of\s*)?(automation|rule)?"
        r"|trigger\s+(daily|weekly|monthly|overdue|sales|invoice|alert|summary|report)\b"
        r"|run\s+(daily|weekly|monthly|overdue|sales|invoice|alert|summary|report)\b"
        r"|when\s*.+\s*(send|notify|alert|email|remind|generate)"
        r"|every\s*(day|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|evening|night)"
        r"|schedule\s*(a\s*)?report|daily\s*report\s*at|weekly\s*report\s*at|monthly\s*report"
        r"|set\s*up\s*(a\s*)?(daily|weekly|monthly)\s*(sales|invoice|expense|summary|report)"
        r"|daily\s*sales\s*summary|send\s*me\s*(a\s*)?(daily|weekly|monthly)"
        r"|remind\s*me\s*(every|daily|weekly|when)"
        r"|automate|automations?|scheduled?\s*task|recurring\s*task)\b",
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

# Automation command verbs — if misspelled, should route to AUTOMATION not ZOHO_CRUD
_AUTOMATION_COMMAND_VERBS = [
    "trigger", "pause", "resume", "delete", "remove",
    "disable", "enable", "activate", "fire",
]

# Broader automation keywords for fuzzy fallback
_AUTOMATION_KEYWORDS = [
    "automation", "automations", "automate",
    "trigger", "list", "pause", "resume", "delete", "remove",
    "schedule", "recurring", "rule", "rules",
]


def classify(text: str) -> Intent:
    """Classify a user message into an intent. Zero LLM cost."""
    matched_intent = None

    for intent, pattern in _PATTERNS:
        if pattern.search(text):
            matched_intent = intent
            break

    # If matched ZOHO_CRUD or CHAT, check if the message actually contains
    # a misspelled automation command verb — those should go to AUTOMATION.
    if matched_intent in (Intent.ZOHO_CRUD, None):
        if _fuzzy_automation_command(text):
            return Intent.AUTOMATION

    if matched_intent:
        return matched_intent

    # Final fuzzy fallback for broader automation keywords
    if _fuzzy_automation_match(text):
        return Intent.AUTOMATION

    return Intent.CHAT


def _fuzzy_automation_command(text: str) -> bool:
    """
    Check if the FIRST meaningful word is a misspelled automation command verb.
    Only checks the first word so "list unpaid bills" doesn't get hijacked
    from ZOHO_CRUD, but "trgger unpaid bills summary" does route to AUTOMATION.
    """
    words = text.lower().split()
    if not words:
        return False
    first_word = words[0]
    if len(first_word) < 3:
        return False
    # Check if first word is close to a command verb but not an exact match
    matches = difflib.get_close_matches(
        first_word, _AUTOMATION_COMMAND_VERBS, n=1, cutoff=0.75
    )
    return bool(matches and first_word != matches[0])


def _fuzzy_automation_match(text: str) -> bool:
    """Check if any word in text is a likely misspelling of an automation keyword."""
    words = text.lower().split()
    for word in words:
        if len(word) < 3:
            continue
        matches = difflib.get_close_matches(
            word, _AUTOMATION_KEYWORDS, n=1, cutoff=0.65
        )
        if matches and word != matches[0]:
            return True
    return False


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
