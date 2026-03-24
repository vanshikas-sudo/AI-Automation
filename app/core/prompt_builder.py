"""
Prompt Builder — Assembles system prompts per-intent.

Inspired by better-chatbot's prompts.ts:
  The system prompt is NOT one big blob. It's assembled from sections
  based on the intent, so each agent gets only what it needs.

Sections:
  - base_identity:  1-sentence identity
  - agent_section:  intent-specific instructions
  - org_context:    Zoho org ID injection
  - format_rules:   WhatsApp readability constraints
"""

from app.core.intent_router import Intent


def build_prompt(
    intent: Intent,
    zoho_org_id: str | None = None,
    custom_base: str | None = None,
) -> str:
    """
    Build a minimal, intent-scoped system prompt.

    Args:
        intent:      The classified user intent.
        zoho_org_id: Zoho organization ID (injected so the LLM doesn't waste
                     a tool call fetching it every time).
        custom_base: Optional override for the base identity line.

    Returns:
        Assembled system prompt string.
    """
    parts: list[str] = []

    # ── Base identity ────────────────────────────────────────
    if custom_base:
        parts.append(custom_base)
    else:
        parts.append(
            "You are a concise WhatsApp assistant with Zoho integration."
        )

    # ── Format rules (skip word limit for REPORT — needs full JSON) ──
    if intent == Intent.REPORT:
        parts.append(
            "Return complete, well-formed JSON with no truncation. "
            "Do not limit output length."
        )
    else:
        parts.append(
            "Format responses for mobile readability. "
            "Keep replies under 300 words. Use short paragraphs."
        )

    # ── Intent-specific instructions ─────────────────────────
    if intent == Intent.ZOHO_CRUD:
        parts.append(
            "Use the available Zoho tools to fulfill the user's request. "
            "When creating or updating records, confirm the details before submitting. "
            "Return results in a clean, readable format."
        )
    elif intent == Intent.REPORT:
        parts.append(
            "You are a financial data analyst. "
            "Collect data from Zoho Books tools and return it as structured JSON. "
            "Do NOT include customer names, vendor names, or email addresses."
        )
    elif intent == Intent.CHAT:
        parts.append(
            "Answer the user's question naturally. "
            "If they ask what you can do, mention: Zoho invoice/contact/item management, "
            "CRM record operations, and fiscal report generation."
        )
    # Intent.CLEAR is handled before reaching the prompt builder

    # ── Zoho org ID injection ────────────────────────────────
    if zoho_org_id and intent in (Intent.ZOHO_CRUD, Intent.REPORT):
        parts.append(
            f"Zoho organization_id: {zoho_org_id}. "
            "IMPORTANT: All ZohoBooks tools expect parameters nested inside a wrapper object. "
            "For list tools, wrap parameters inside \"query_params\". "
            "For get tools, use \"path_params\" for resource IDs and \"query_params\" for organization_id. "
            f"Example: {{\"query_params\": {{\"organization_id\": \"{zoho_org_id}\"}}}}"
        )

    return "\n\n".join(parts)
