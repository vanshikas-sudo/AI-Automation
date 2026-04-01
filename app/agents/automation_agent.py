"""
Automation Agent — Handles automation-related WhatsApp messages.

Supports:
  - "list my automations"           → shows all rules
  - "pause automation <name>"       → pauses a rule
  - "resume automation <name>"      → resumes a rule
  - "delete automation <name>"      → deletes a rule
  - "trigger automation <name>"     → manually fires a rule
  - "every day at 9 PM send me a sales summary on WhatsApp"  → creates a rule via LLM
  - "when invoices are overdue by 30 days, send email"        → creates a polling rule via LLM
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from datetime import datetime

from cron_descriptor import get_description

from app.automation.models import (
    ActionConfig,
    Condition,
    EventRule,
    RuleStatus,
    TriggerConfig,
    TriggerType,
)
from app.config import get_settings
from app.core.session_manager import SessionManager

logger = logging.getLogger(__name__)

# ── Pending automation state (org selection flow) ─────────
_pending_automation: dict[str, str] = {}   # phone → original automation text


def set_pending_automation(phone: str, text: str) -> None:
    _pending_automation[phone] = text


def get_pending_automation(phone: str) -> str | None:
    return _pending_automation.get(phone)


def clear_pending_automation(phone: str) -> None:
    _pending_automation.pop(phone, None)


# ── Pending confirmation state (delete / create) ─────────
# phone → {"action": "delete"|"create", "rule_id": ..., "rule_name": ..., "rule": ...}
_pending_confirmation: dict[str, dict] = {}

_YES_RE = re.compile(r"^\s*(yes|y|yeah|yep|yup|confirm|sure|ok|go\s*ahead|do\s*it|proceed)\s*[!.]*\s*$", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*(no|n|nah|nope|cancel|stop|nevermind|never\s*mind|abort)\s*[!.]*\s*$", re.IGNORECASE)


def get_pending_confirmation(phone: str) -> dict | None:
    return _pending_confirmation.get(phone)


def clear_pending_confirmation(phone: str) -> None:
    _pending_confirmation.pop(phone, None)


# Sub-command patterns — allow optional words between verb and "automation/rule"
_LIST_RE = re.compile(r"\b(list|show|my)\b.*\b(automations?|rules?)\b", re.IGNORECASE)
_PAUSE_RE = re.compile(r"\b(pause|stop|disable)\b.*?\b(automations?|rules?)?\b\s*(.+)?", re.IGNORECASE)
_RESUME_RE = re.compile(r"\b(resume|start|enable|activate)\b.*?\b(automations?|rules?)?\b\s*(.+)?", re.IGNORECASE)
_DELETE_RE = re.compile(r"\b(delete|remove)\b.*?\b(automations?|rules?)?\b\s*(.+)?", re.IGNORECASE)
_TRIGGER_RE = re.compile(r"\b(trigger|run|test|fire)\b.*?\b(automations?|rules?)?\b\s*(.+)?", re.IGNORECASE)
_RESCHEDULE_RE = re.compile(
    r"\b(reschedule|change|update|edit|modify|set)\b.*?"
    r"\b(schedule|time|cron|timing)\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b(\d{1,2})\s*[:.]\s*(\d{2})\s*(am|pm|AM|PM)?\b"
    r"|\b(\d{1,2})\s*(am|pm|AM|PM)\b",
    re.IGNORECASE,
)


async def handle_automation_message(text: str, from_number: str, app_state) -> str:
    """
    Route an automation intent to the right sub-handler.
    Returns the reply text to send back via WhatsApp.
    """
    store = getattr(app_state, "rule_store", None)
    if not store:
        return "Automation engine is not available right now. Please try again later."

    # ── Handle pending confirmation (yes/no reply) ────────
    pending = _pending_confirmation.get(from_number)
    if pending:
        if _YES_RE.match(text.strip()):
            clear_pending_confirmation(from_number)
            return await _execute_confirmed_action(pending, store)
        elif _NO_RE.match(text.strip()):
            clear_pending_confirmation(from_number)
            return "Cancelled. No changes were made."
        else:
            if _classify_automation_command(text):
                clear_pending_confirmation(from_number)
            else:
                return f"Please reply *yes* to confirm or *no* to cancel."

    # ── Step 1: Try exact regex matching ──────────────────
    if _LIST_RE.search(text):
        return await _handle_list(store)

    m = _PAUSE_RE.search(text)
    if m and m.group(1).lower() in ("pause", "stop", "disable"):
        name_hint = _extract_rule_name(text, m.group(1))
        return await _handle_toggle(store, name_hint, pause=True)

    m = _RESUME_RE.search(text)
    if m and m.group(1).lower() in ("resume", "start", "enable", "activate"):
        name_hint = _extract_rule_name(text, m.group(1))
        return await _handle_toggle(store, name_hint, pause=False)

    m = _DELETE_RE.search(text)
    if m and m.group(1).lower() in ("delete", "remove"):
        name_hint = _extract_rule_name(text, m.group(1))
        return await _handle_delete(store, name_hint, from_number)

    m = _TRIGGER_RE.search(text)
    if m and m.group(1).lower() in ("trigger", "run", "test", "fire"):
        name_hint = _extract_rule_name(text, m.group(1))
        return await _handle_trigger(store, name_hint)

    # Reschedule
    if _RESCHEDULE_RE.search(text):
        return await _handle_reschedule(text, store)

    # ── Step 2: Fuzzy command detection ───────────────────
    # This catches ALL misspellings — verb, action word, or both
    cmd = _classify_automation_command(text)
    if cmd:
        action = cmd["action"]
        name_hint = cmd["name_hint"]

        if action == "list":
            return await _handle_list(store)
        elif action == "pause":
            return await _handle_toggle(store, name_hint, pause=True)
        elif action == "resume":
            return await _handle_toggle(store, name_hint, pause=False)
        elif action == "delete":
            return await _handle_delete(store, name_hint, from_number)
        elif action == "trigger":
            return await _handle_trigger(store, name_hint)
        else:
            # Detected as a management command but unclear which
            return (
                "I couldn't understand that command. Try one of these:\n\n"
                "• *list my automations* — view all your rules\n"
                "• *pause automation <name>* — pause a rule\n"
                "• *resume automation <name>* — resume a rule\n"
                "• *delete automation <name>* — remove a rule\n"
                "• *trigger automation <name>* — run a rule now"
            )

    # ── Step 3: Creation — only if nothing above matched ──
    return await _handle_create(text, from_number, store, app_state)


# ── Helpers ───────────────────────────────────────────────

# Words to strip when extracting the rule name from a command
_STRIP_WORDS = {"automation", "automations", "rule", "rules", "my", "the", "all", "named", "called"}


def _extract_rule_name(text: str, verb: str) -> str:
    """Extract the rule name from a command by stripping the verb and noise words."""
    # Remove everything up to and including the verb
    lower = text.lower()
    idx = lower.find(verb.lower())
    if idx >= 0:
        remainder = text[idx + len(verb):].strip()
    else:
        remainder = text

    # Strip noise words
    words = [w for w in remainder.split() if w.lower() not in _STRIP_WORDS]
    return " ".join(words).strip()


# Known sub-command verbs grouped by action
_COMMAND_VERBS = {
    "list": ["list", "show", "my"],
    "pause": ["pause", "stop", "disable"],
    "resume": ["resume", "start", "enable", "activate"],
    "delete": ["delete", "remove"],
    "trigger": ["trigger", "run", "test", "fire"],
}
_ALL_VERBS = [v for group in _COMMAND_VERBS.values() for v in group]
_ACTION_WORDS = ["automations", "automation", "rules", "rule"]
_NOISE_WORDS = {"my", "the", "all", "named", "called", "please", "can", "you", "me", "a", "an"}


def _classify_automation_command(text: str) -> dict | None:
    """
    Unified command classifier. Detects whether text is a management command
    (list/pause/resume/delete/trigger) by checking for BOTH exact AND fuzzy
    matches of verb + action word.

    Returns {"action": "list"|"pause"|..., "name_hint": "..."} or None.
    """
    words = text.lower().split()
    if not words:
        return None

    found_verb = None       # The resolved exact verb
    found_action = None     # The action group name
    has_action_word = False  # Found "automation"/"rule" (exact or fuzzy)
    verb_was_fuzzy = False   # Was the verb a fuzzy match (not exact)?
    name_words = []

    for i, word in enumerate(words):
        # Check for exact verb match
        if not found_verb and word in _ALL_VERBS:
            found_verb = word
            verb_was_fuzzy = False
            continue

        # Check for fuzzy verb match (high cutoff to avoid false positives)
        if not found_verb:
            matches = difflib.get_close_matches(word, _ALL_VERBS, n=1, cutoff=0.75)
            if matches:
                found_verb = matches[0]
                verb_was_fuzzy = True
                continue

        # Check for exact action word
        if word in _ACTION_WORDS:
            has_action_word = True
            continue

        # Check for fuzzy action word (e.g. "automatin", "automtions", "ruless")
        action_matches = difflib.get_close_matches(word, _ACTION_WORDS, n=1, cutoff=0.55)
        if action_matches:
            has_action_word = True
            continue

        # Skip noise words
        if word in _NOISE_WORDS:
            continue

        # Everything else is part of the rule name
        name_words.append(word)

    # Must have a verb to be a management command.
    # Then: either has an action word (automation/rule, exact or fuzzy),
    # OR the verb itself was misspelled (fuzzy) and there are remaining words
    # that could be a rule name (intent router already decided this is automation).
    if not found_verb:
        return None

    if not has_action_word:
        if not (verb_was_fuzzy and name_words):
            return None

    # Map verb to action group
    for action, verbs in _COMMAND_VERBS.items():
        if found_verb in verbs:
            found_action = action
            break

    if not found_action:
        return None

    return {
        "action": found_action,
        "name_hint": " ".join(name_words).strip(),
    }


def _humanize_cron(cron_expr: str) -> str:
    """Convert a cron expression to human-readable text."""
    try:
        return get_description(cron_expr)
    except Exception:
        return cron_expr


# ── Sub-handlers ─────────────────────────────────────────


async def _handle_list(store) -> str:
    rules = await store.list_rules()
    if not rules:
        return "You don't have any automations yet.\n\nTry: *\"every day at 9 PM send me a sales summary on WhatsApp\"*"

    lines = ["*Your Automations:*\n"]
    tz_label = get_settings().timezone
    for i, r in enumerate(rules, 1):
        status_icon = "🟢" if r.status == RuleStatus.ACTIVE else "⏸️"
        schedule = _humanize_cron(r.trigger.schedule) if r.trigger.schedule else "manual"
        triggered = f"(triggered {r.trigger_count}x)" if r.trigger_count else "(never triggered)"
        lines.append(f"{i}. {status_icon} *{r.name}*\n   Schedule: {schedule} ({tz_label}) {triggered}")
    lines.append(f"\n_Total: {len(rules)} rule(s)_")
    lines.append("\nSay *pause/resume/delete/trigger/reschedule automation <name>* to manage.")
    return "\n".join(lines)


async def _handle_toggle(store, name_hint: str, pause: bool) -> str:
    rule = await _find_rule_by_name(store, name_hint)
    if isinstance(rule, str):
        return rule  # Error message

    action = "paused" if pause else "resumed"
    if pause and rule.status == RuleStatus.PAUSED:
        return f"*{rule.name}* is already paused."
    if not pause and rule.status == RuleStatus.ACTIVE:
        return f"*{rule.name}* is already active."

    await store.toggle_rule(rule.id)
    return f"✅ *{rule.name}* has been {action}."


async def _handle_delete(store, name_hint: str, from_number: str = "") -> str:
    # Support "delete all automations"
    if name_hint.lower().strip() in ("all", "all automations", "all rules", "everything"):
        rules = await store.list_rules()
        if not rules:
            return "No automations to delete."
        if from_number:
            _pending_confirmation[from_number] = {
                "action": "delete_all",
                "count": len(rules),
            }
            names = "\n".join(f"  • {r.name}" for r in rules)
            return f"⚠️ Are you sure you want to delete *all {len(rules)}* automation(s)?\n{names}\n\nReply *yes* to confirm or *no* to cancel."
        # No from_number — direct execution (shouldn't happen in normal flow)
        for r in rules:
            await store.delete_rule(r.id)
        return f"🗑️ All *{len(rules)}* automation(s) have been deleted."

    rule = await _find_rule_by_name(store, name_hint)
    if isinstance(rule, str):
        return rule

    if from_number:
        schedule_desc = _humanize_cron(rule.trigger.schedule) if rule.trigger.schedule else "manual"
        _pending_confirmation[from_number] = {
            "action": "delete",
            "rule_id": rule.id,
            "rule_name": rule.name,
        }
        return (
            f"⚠️ Are you sure you want to delete *{rule.name}*?\n"
            f"   Schedule: {schedule_desc}\n\n"
            f"Reply *yes* to confirm or *no* to cancel."
        )

    await store.delete_rule(rule.id)
    return f"🗑️ *{rule.name}* has been deleted."


async def _handle_trigger(store, name_hint: str) -> str:
    rule = await _find_rule_by_name(store, name_hint)
    if isinstance(rule, str):
        return rule

    try:
        from app.worker.tasks import evaluate_single_rule
        evaluate_single_rule.delay(rule.id)
        return f"⚡ *{rule.name}* has been triggered. The worker will process it now."
    except Exception as e:
        return f"Could not trigger the rule (queue unavailable): {e}"


async def _handle_reschedule(text: str, store) -> str:
    """Parse a reschedule command: extract rule name + new time, update the cron."""
    # Extract time from text
    new_time = _parse_time_from_text(text)
    if not new_time:
        return (
            "Please specify the new time. Example:\n"
            "• *change schedule of Daily Sales Summary to 6:30 PM*\n"
            "• *reschedule Unpaid Bills Summary to 9 AM*"
        )

    hour, minute = new_time

    # Extract rule name — strip time-related words and command words
    time_strip = {"to", "at", "for", "schedule", "time", "cron", "timing",
                  "change", "update", "edit", "modify", "set", "reschedule",
                  "am", "pm", "of"}
    words = text.split()
    name_words = []
    for w in words:
        wl = w.lower().rstrip(".,!?")
        if wl in time_strip or wl in _STRIP_WORDS:
            continue
        if re.match(r"^\d{1,2}[:.]?\d{0,2}$", wl):
            continue
        name_words.append(w)
    name_hint = " ".join(name_words).strip()

    rule = await _find_rule_by_name(store, name_hint)
    if isinstance(rule, str):
        return rule

    old_cron = rule.trigger.schedule
    rule.trigger.schedule = f"{minute} {hour} * * *"
    await store.update_rule(rule)

    old_desc = _humanize_cron(old_cron) if old_cron else "manual"
    new_desc = _humanize_cron(rule.trigger.schedule)
    tz_label = get_settings().timezone
    return (
        f"✅ *{rule.name}* rescheduled!\n\n"
        f"⏰ Old: {old_desc} ({tz_label})\n"
        f"⏰ New: {new_desc} ({tz_label})"
    )


def _parse_time_from_text(text: str) -> tuple[int, int] | None:
    """Extract hour and minute from text like '6:30 PM', '9 AM', '21:00'."""
    m = _TIME_RE.search(text)
    if not m:
        return None

    if m.group(1) is not None:
        # Matched HH:MM format (with optional AM/PM)
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = (m.group(3) or "").lower()
    elif m.group(4) is not None:
        # Matched H AM/PM format
        hour = int(m.group(4))
        minute = 0
        ampm = (m.group(5) or "").lower()
    else:
        return None

    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return (hour, minute)
    return None


async def _handle_create(text: str, from_number: str, store, app_state) -> str:
    """
    Parse natural language into an automation rule using the LLM.
    Dynamically resolves the user's Zoho org_id from their session or MCP manager.
    """
    model = getattr(app_state, "llm_model", None)
    if not model:
        return "LLM is not configured. Cannot create automations from natural language."

    # ── Resolve org_id dynamically ──────────────────────────
    org_id = ""
    mcp_mgr = getattr(app_state, "mcp_manager", None)

    # Priority 1: User's session-selected org
    user_org = SessionManager.get_org(from_number)
    if user_org:
        org_id = user_org.get("org_id", "")

    # Priority 2: MCP manager's auto-detected org (single-org case)
    if not org_id and mcp_mgr and getattr(mcp_mgr, "zoho_org_id", None):
        org_id = mcp_mgr.zoho_org_id

    # Priority 3: If multiple orgs and none selected, ask user to pick one first
    if not org_id and mcp_mgr and len(getattr(mcp_mgr, "zoho_organizations", [])) > 1:
        org_names = [org["name"] for org in mcp_mgr.zoho_organizations]
        org_list = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(org_names))
        set_pending_automation(from_number, text)
        return (
            f"Before creating an automation, please select an organization first.\n"
            f"Send any message with the org name to select it:\n{org_list}"
        )

    if not org_id:
        logger.warning("No org_id resolved for automation creation by %s", from_number)

    prompt = f"""You are an automation rule parser. Convert the user's request into a JSON automation rule.

User said: "{text}"
User's WhatsApp number: {from_number}

Return ONLY a valid JSON object with these fields:
{{
  "name": "Short descriptive name",
  "trigger": {{
    "type": "schedule" or "polling",
    "schedule": "cron expression",
    "data_source": "MCP tool name if polling type, null if schedule-only",
    "data_source_params": {{}}
  }},
  "conditions": [
    {{"field": "field_name", "operator": "gt/lt/eq/contains", "value": "comparison_value"}}
  ],
  "actions": [
    {{"type": "send_whatsapp/send_email/generate_report/call_mcp_tool", "params": {{}}}}
  ]
}}

Available MCP data tools (use the correct one based on what the user asks about):
  - ZohoBooks_list_invoices — for invoices, sales, revenue
  - ZohoBooks_list_bills — for bills, unpaid bills, vendor payments due
  - ZohoBooks_list_expenses — for expenses, spending
  - ZohoBooks_list_contacts — for customers, vendors, contacts
  - ZohoBooks_list_items — for products, inventory, items
  - ZohoBooks_list_sales_orders — for sales orders
  - ZohoBooks_list_journals — for journal entries
  - ZohoBooks_list_vendor_payments — for vendor payments
  - ZohoBooks_list_chart_of_accounts — for chart of accounts

Rules:
1. For summary/report requests (e.g. "sales summary", "unpaid bills summary", "expense report"):
   - action type = "generate_report"
   - params MUST include: "data_tool": "<correct MCP tool>", "title": "<descriptive title matching user request>", "send_to": "{from_number}", "tool_params": {{"query_params": {{}}}}
   - Pick the data_tool that matches the user's request. Examples:
     * "unpaid bills" / "bills summary" → data_tool = "ZohoBooks_list_bills"
     * "sales summary" / "invoice report" → data_tool = "ZohoBooks_list_invoices"
     * "expense report" → data_tool = "ZohoBooks_list_expenses"
   - For status filters, add them to query_params, e.g. "query_params": {{"status": "unpaid"}}
   - trigger type = "schedule"

2. For alert/notification requests (e.g. "overdue invoices alert", "notify when..."):
   - trigger type = "polling"
   - data_source = "<correct MCP tool>"
   - conditions = appropriate filter conditions
   - action type = "send_whatsapp"
   - action params: "to": "{from_number}", "aggregate": true

3. For generic "send me" / "notify me" → use send_whatsapp with to="{from_number}"
4. For "send email" → use send_email

5. Cron format: minute hour day_of_month month day_of_week
   ⚠️ CRITICAL TIMEZONE RULE: The cron schedule runs in {get_settings().timezone} timezone.
   DO NOT convert to UTC. Use the user's requested time DIRECTLY in the cron expression.
   Examples:
     - User says "4:30 PM" → cron = "30 16 * * *" (16:30 local time, NOT UTC)
     - User says "9 PM" → cron = "0 21 * * *" (21:00 local time)
     - User says "9 AM on Mondays" → cron = "0 9 * * 1"
     - User says "10 PM" → cron = "0 22 * * *"

6. Do NOT include organization_id anywhere in data_source_params or tool_params — it is injected automatically.

Return ONLY the JSON, no explanation."""

    try:
        from langchain_core.messages import HumanMessage
        response = await model.ainvoke([HumanMessage(content=prompt)])
        raw = response.content.strip()

        # Extract JSON from response (may have markdown code blocks)
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            return "I couldn't understand that as an automation rule. Try something like:\n• *every day at 9 PM send me a sales summary*\n• *when invoices overdue by 30 days, notify me on WhatsApp*"

        data = json.loads(json_match.group())

        # Build the rule
        trigger_data = data.get("trigger", {})
        trigger = TriggerConfig(
            type=TriggerType(trigger_data.get("type", "schedule")),
            schedule=trigger_data.get("schedule"),
            data_source=trigger_data.get("data_source"),
            data_source_params=trigger_data.get("data_source_params", {}),
        )

        conditions = [
            Condition(field=c["field"], operator=c["operator"], value=c["value"])
            for c in data.get("conditions", [])
        ]

        actions = [
            ActionConfig(type=a["type"], params=a.get("params", {}))
            for a in data.get("actions", [])
        ]

        rule = EventRule(
            name=data.get("name", "My Automation"),
            org_id=org_id,
            trigger=trigger,
            conditions=conditions,
            actions=actions,
        )

        # Build preview and ask for confirmation
        schedule_desc = _humanize_cron(rule.trigger.schedule) if rule.trigger.schedule else "manual"
        tz_label = get_settings().timezone
        cond_desc = ""
        if conditions:
            cond_desc = "\n📋 Conditions: " + ", ".join(
                f"{c.field} {c.operator} {c.value}" for c in conditions
            )
        action_desc = ", ".join(a.type for a in actions)

        # Store rule data for confirmation
        _pending_confirmation[from_number] = {
            "action": "create",
            "rule": rule.model_dump(),
        }
        logger.info("[AUTOMATION] Pending confirmation SET for %s — rule: %s", from_number, rule.name)

        return (
            f"📋 *Automation Preview:*\n\n"
            f"📌 *{rule.name}*\n"
            f"⏰ Schedule: {schedule_desc} ({tz_label})\n"
            f"{cond_desc}\n"
            f"🎯 Actions: {action_desc}\n\n"
            f"Reply *yes* to create or *no* to cancel."
        )

    except json.JSONDecodeError:
        return "I had trouble parsing the automation. Try rephrasing, e.g.:\n• *every day at 9 PM send me a sales summary on WhatsApp*"
    except Exception as e:
        logger.error("Automation creation failed: %s", e, exc_info=True)
        return f"Something went wrong creating the automation: {e}"


# ── Helpers ──────────────────────────────────────────────


def classify_text_as_automation_command(text: str) -> bool:
    """Check if text looks like an automation sub-command (not a yes/no reply)."""
    return bool(
        _LIST_RE.search(text)
        or _PAUSE_RE.search(text)
        or _RESUME_RE.search(text)
        or _DELETE_RE.search(text)
        or _TRIGGER_RE.search(text)
        or _classify_automation_command(text)
    )


async def _execute_confirmed_action(pending: dict, store) -> str:
    """Execute a previously confirmed delete or create action."""
    action = pending.get("action")

    if action == "delete":
        rule_id = pending["rule_id"]
        rule_name = pending["rule_name"]
        await store.delete_rule(rule_id)
        return f"🗑️ *{rule_name}* has been deleted."

    if action == "delete_all":
        rules = await store.list_rules()
        count = 0
        for r in rules:
            await store.delete_rule(r.id)
            count += 1
        return f"🗑️ All *{count}* automation(s) have been deleted."

    if action == "create":
        rule_data = pending["rule"]
        rule = EventRule(**rule_data)
        await store.save_rule(rule)
        return (
            f"✅ *Automation created!*\n\n"
            f"📌 *{rule.name}*\n\n"
            f"Say *list my automations* to see all rules."
        )

    return "Unknown action. No changes were made."


async def _find_rule_by_name(store, name_hint: str) -> EventRule | str:
    """Find a rule by name hint (partial, case-insensitive). Returns rule or error string."""
    if not name_hint:
        rules = await store.list_rules()
        if not rules:
            return "No automations found."
        if len(rules) == 1:
            return rules[0]
        names = "\n".join(f"  • {r.name}" for r in rules)
        return f"Which automation? Please specify the name:\n{names}"

    rules = await store.list_rules()
    # Strip noise words users commonly add
    _noise = {"automation", "automations", "rule", "rules", "my", "the", "named", "called"}
    cleaned_words = [w for w in name_hint.lower().split() if w not in _noise]
    hint_lower = " ".join(cleaned_words).strip()

    # Exact match (with and without noise words)
    for r in rules:
        if r.name.lower() == hint_lower or r.name.lower() == name_hint.lower().strip():
            return r

    # Partial match — check both directions (hint in name OR name in hint)
    matches = [r for r in rules if hint_lower in r.name.lower() or r.name.lower() in hint_lower]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = "\n".join(f"  • {r.name}" for r in matches)
        return f"Multiple automations match. Be more specific:\n{names}"

    # Fuzzy match — auto-resolve if confidence is high
    rule_names = [r.name.lower() for r in rules]
    close = difflib.get_close_matches(hint_lower, rule_names, n=1, cutoff=0.5)
    if close:
        matched_rule = next(r for r in rules if r.name.lower() == close[0])
        # High confidence (>=0.7) — just use it directly
        ratio = difflib.SequenceMatcher(None, hint_lower, close[0]).ratio()
        if ratio >= 0.7:
            return matched_rule
        return f"No exact match for \"{name_hint}\". Did you mean *{matched_rule.name}*?\n\nPlease re-send with the correct name."

    return f"No automation found matching \"{name_hint}\"."
