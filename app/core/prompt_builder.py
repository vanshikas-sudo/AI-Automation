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
    zoho_organizations: list[dict] | None = None,
    custom_base: str | None = None,
) -> str:
    """
    Build a minimal, intent-scoped system prompt.

    Args:
        intent:              The classified user intent.
        zoho_org_id:         Zoho organization ID (when already selected).
        zoho_organizations:  All available orgs [{name, organization_id}, ...].
        custom_base:         Optional override for the base identity line.

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
    if intent in (Intent.ZOHO_CRUD, Intent.REPORT):
        if zoho_org_id:
            # Org already selected — inject it directly
            parts.append(
                f"Zoho organization_id: {zoho_org_id}. "
                "IMPORTANT: All ZohoBooks tools expect parameters nested inside a wrapper object. "
                "For list tools, wrap parameters inside \"query_params\". "
                "For get tools, use \"path_params\" for resource IDs and \"query_params\" for organization_id. "
                f"Example: {{\"query_params\": {{\"organization_id\": \"{zoho_org_id}\"}}}}"
            )
        elif zoho_organizations and len(zoho_organizations) > 1:
            # Multiple orgs available — instruct LLM to ask user
            org_list = "\n".join(
                f"  - {org['name']}"
                for org in zoho_organizations
            )
            parts.append(
                "IMPORTANT: Multiple Zoho organizations are available. "
                "Before performing ANY Zoho operation, you MUST ask the user which organization "
                "they want to work with. Present the following organization names and ask them to choose:\n"
                f"{org_list}\n\n"
                "Once the user tells you the organization name, use it for the request. "
                "Do NOT proceed with any Zoho tool call until the user has selected an organization."
            )
        else:
            # No orgs detected yet — instruct agent to fetch them first
            parts.append(
                "IMPORTANT: The Zoho organization has not been determined yet. "
                "Before performing ANY Zoho operation, you MUST first call the "
                "ZohoBooks_list_organizations tool to discover available organizations. "
                "Then present the organization names to the user and ask them to choose one. "
                "Do NOT proceed with any other tool call until the user has selected an organization."
            )

    return "\n\n".join(parts)
