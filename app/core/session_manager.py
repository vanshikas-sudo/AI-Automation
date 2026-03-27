"""
Session Manager — Per-phone-number conversation history.

Design (inspired by better-chatbot's thread management):
  - Sliding window: keeps last N message pairs (configurable)
  - Session TTL: auto-clears after inactivity
  - Stores as simple dicts {role, content}
  - Extracts final AI reply from agent response (strips tool call noise)
"""

import logging
import time
from collections import defaultdict

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

# Defaults — overridden by config at startup
MAX_HISTORY = 10          # message pairs (user+assistant = 2 messages each)
SESSION_TTL = 30 * 60     # 30 minutes of inactivity


class SessionManager:
    """Manages per-user conversation sessions in-memory."""

    _sessions: dict[str, list[dict[str, str]]] = defaultdict(list)
    _last_activity: dict[str, float] = {}
    _selected_org: dict[str, dict[str, str]] = {}  # phone → {org_id, org_name}

    @classmethod
    def configure(cls, max_history: int = 10, session_ttl_minutes: int = 30) -> None:
        """Update defaults from config. Call once at startup."""
        global MAX_HISTORY, SESSION_TTL
        MAX_HISTORY = max_history
        SESSION_TTL = session_ttl_minutes * 60

    # ── Write ────────────────────────────────────────────────

    @classmethod
    def add_user_message(cls, phone: str, text: str) -> None:
        cls._touch(phone)
        cls._sessions[phone].append({"role": "user", "content": text})
        cls._trim(phone)

    @classmethod
    def add_assistant_message(cls, phone: str, text: str) -> None:
        cls._touch(phone)
        cls._sessions[phone].append({"role": "assistant", "content": text})
        cls._trim(phone)

    # ── Read ─────────────────────────────────────────────────

    @classmethod
    def get_langchain_messages(cls, phone: str) -> list:
        """Convert session history to LangChain message objects."""
        cls._expire_if_stale(phone)
        messages = []
        for msg in cls._sessions.get(phone, []):
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))
        return messages

    @classmethod
    def get_history(cls, phone: str) -> list[dict[str, str]]:
        """Raw history for debugging."""
        return list(cls._sessions.get(phone, []))

    # ── Control ──────────────────────────────────────────────

    @classmethod
    def clear(cls, phone: str) -> None:
        """Explicitly clear a user's session."""
        cls._sessions.pop(phone, None)
        cls._last_activity.pop(phone, None)
        cls._selected_org.pop(phone, None)

    # ── Organization selection ───────────────────────────────

    @classmethod
    def set_org(cls, phone: str, org_id: str, org_name: str) -> None:
        cls._selected_org[phone] = {"org_id": org_id, "org_name": org_name}

    @classmethod
    def get_org(cls, phone: str) -> dict[str, str] | None:
        return cls._selected_org.get(phone)

    @classmethod
    def has_org(cls, phone: str) -> bool:
        return phone in cls._selected_org

    # ── Response extraction ──────────────────────────────────

    @staticmethod
    def extract_reply(response: dict) -> str:
        """
        Extract the final AI text reply from a LangGraph agent response.
        Handles both string and multi-part content, skipping tool calls.
        """
        for msg in reversed(response.get("messages", [])):
            if not isinstance(msg, AIMessage) or not msg.content:
                continue
            # Multi-part content (list of dicts with type/text)
            if isinstance(msg.content, list):
                text_parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in msg.content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                if text_parts:
                    return "\n".join(text_parts).strip()
            # Simple string content
            elif isinstance(msg.content, str):
                return msg.content.strip()
        return "I couldn't generate a response. Please try again."

    # ── Internal helpers ─────────────────────────────────────

    @classmethod
    def _touch(cls, phone: str) -> None:
        cls._last_activity[phone] = time.monotonic()

    @classmethod
    def _trim(cls, phone: str) -> None:
        """Sliding window — keep last MAX_HISTORY messages."""
        session = cls._sessions[phone]
        if len(session) > MAX_HISTORY * 2:
            session[:] = session[-(MAX_HISTORY * 2):]

    @classmethod
    def _expire_if_stale(cls, phone: str) -> None:
        """Auto-clear if no activity for SESSION_TTL seconds."""
        last = cls._last_activity.get(phone)
        if last and (time.monotonic() - last) > SESSION_TTL:
            logger.info("Session expired for %s (idle > %d min)", phone, SESSION_TTL // 60)
            cls.clear(phone)  # also clears org selection
