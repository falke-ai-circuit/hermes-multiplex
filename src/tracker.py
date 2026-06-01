"""
Hermes Multiplex Plugin — Session Tracker
===========================================

Per-chat session registry with ACTIVE/IDLE/ARCHIVED states, persistent
to ``~/.hermes/profiles/conductor/plugins/multiplex/state/sessions.json``.

Session keys are 8-character hex strings. Each session tracks:

- **agent** — the agent profile name (e.g. "analyst", "coder")
- **state** — one of ACTIVE, IDLE, ARCHIVED
- **created** — Unix timestamp of creation
- **last_active** — Unix timestamp of last activity

Public API:

- ``create_session(chat_id, agent)`` → session_id
- ``get_active(chat_id)`` → session_id or None
- ``set_active(chat_id, session_id)`` — also resets idle timer
- ``list_sessions(chat_id, agent_filter=None)`` → list of session dicts
- ``mark_idle(chat_id, session_id)`` → bool (True if state changed)
- ``archive_session(chat_id, session_id)`` → bool
- ``mark_idle_sessions(timeout_seconds)`` → count of sessions idled
- ``save()`` / ``load()`` — JSON persistence
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_ACTIVE = "ACTIVE"
STATE_IDLE = "IDLE"
STATE_ARCHIVED = "ARCHIVED"

DEFAULT_IDLE_TIMEOUT = 900  # seconds (15 minutes)


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------
#
# _registry: Dict[str, dict]
#   chat_id → {
#       "active_session": str | None,
#       "sessions": {
#           session_id → {
#               "agent": str,
#               "state": str,
#               "created": float,
#               "last_active": float,
#           }
#       }
#   }


class SessionTracker:
    """Per-chat session registry with persistence."""

    def __init__(self, state_dir: Optional[Path] = None):
        """Create a session tracker.

        Args:
            state_dir: Directory for sessions.json. If None, uses the default
                       multiplex state dir (``~/.hermes/profiles/conductor/
                       plugins/multiplex/state/``).
        """
        self._registry: Dict[str, dict] = {}
        if state_dir is None:
            try:
                from hermes_constants import get_hermes_home
                hermes_home = Path(get_hermes_home())
            except ImportError:
                hermes_home = Path.home() / ".hermes"
            state_dir = hermes_home / "profiles" / "conductor" / "plugins" / "multiplex" / "state"
        self.state_dir = Path(state_dir)
        self._state_file = self.state_dir / "sessions.json"

    # -----------------------------------------------------------------------
    # Session creation
    # -----------------------------------------------------------------------

    def create_session(self, chat_id: str, agent: str) -> str:
        """Create a new session for *agent* in *chat_id*.

        Returns the new 8-char hex session ID. Automatically sets it as the
        active session for the chat.
        """
        sid = secrets.token_hex(4)  # 8 hex chars
        now = time.time()

        if chat_id not in self._registry:
            self._registry[chat_id] = {"active_session": None, "sessions": {}}

        self._registry[chat_id]["sessions"][sid] = {
            "agent": agent,
            "state": STATE_ACTIVE,
            "created": now,
            "last_active": now,
        }
        self._registry[chat_id]["active_session"] = sid

        logger.debug("Created session %s for agent=%s in chat=%s", sid, agent, chat_id)
        return sid

    # -----------------------------------------------------------------------
    # Active session
    # -----------------------------------------------------------------------

    def get_active(self, chat_id: str) -> Optional[str]:
        """Return the active session ID for *chat_id*, or None."""
        chat = self._registry.get(chat_id)
        if chat is None:
            return None
        active = chat.get("active_session")
        if active is None:
            return None
        # Verify the session still exists
        if active not in chat["sessions"]:
            return None
        # Archived sessions cannot be active
        if chat["sessions"][active]["state"] == STATE_ARCHIVED:
            return None
        return active

    def set_active(self, chat_id: str, session_id: str) -> bool:
        """Set *session_id* as the active session for *chat_id*.

        Returns True on success. Also resets the session's ``last_active``
        timestamp and restores state to ACTIVE if it was IDLE.
        """
        chat = self._registry.get(chat_id)
        if chat is None:
            logger.debug("set_active: chat %s not found", chat_id)
            return False
        sessions = chat.get("sessions", {})
        if session_id not in sessions:
            logger.debug("set_active: session %s not found in chat %s", session_id, chat_id)
            return False
        if sessions[session_id]["state"] == STATE_ARCHIVED:
            logger.debug("set_active: session %s is ARCHIVED", session_id)
            return False

        chat["active_session"] = session_id
        sessions[session_id]["last_active"] = time.time()
        if sessions[session_id]["state"] == STATE_IDLE:
            sessions[session_id]["state"] = STATE_ACTIVE
        return True

    # -----------------------------------------------------------------------
    # List sessions
    # -----------------------------------------------------------------------

    def list_sessions(self, chat_id: str, agent_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """List sessions for *chat_id*, optionally filtered by agent.

        Returns a list of dicts with keys:
        ``session_id``, ``agent``, ``state``, ``created``, ``idle_seconds``.
        """
        chat = self._registry.get(chat_id)
        if chat is None:
            return []

        now = time.time()
        result = []
        for sid, sdata in chat["sessions"].items():
            if sdata["state"] == STATE_ARCHIVED:
                continue
            if agent_filter is not None and sdata["agent"] != agent_filter:
                continue
            result.append({
                "session_id": sid,
                "agent": sdata["agent"],
                "state": sdata["state"],
                "created": sdata["created"],
                "last_active": sdata.get("last_active", sdata["created"]),
                "idle_seconds": int(now - sdata.get("last_active", sdata["created"])),
            })

        # Sort by most recently active first
        result.sort(key=lambda s: s["last_active"], reverse=True)
        return result

    # -----------------------------------------------------------------------
    # State transitions
    # -----------------------------------------------------------------------

    def mark_idle(self, chat_id: str, session_id: str) -> bool:
        """Transition session to IDLE state. Returns True if state changed."""
        chat = self._registry.get(chat_id)
        if chat is None:
            return False
        session = chat["sessions"].get(session_id)
        if session is None:
            return False
        if session["state"] == STATE_ACTIVE:
            session["state"] = STATE_IDLE
            logger.debug("Marked session %s IDLE in chat %s", session_id, chat_id)
            return True
        return False

    def archive_session(self, chat_id: str, session_id: str) -> bool:
        """Archive a session permanently. Returns True on success.

        If the archived session was the active one, the next most-recently-active
        non-archived session becomes active (or None if none remain).
        """
        chat = self._registry.get(chat_id)
        if chat is None:
            return False
        session = chat["sessions"].get(session_id)
        if session is None:
            return False

        session["state"] = STATE_ARCHIVED
        logger.debug("Archived session %s in chat %s", session_id, chat_id)

        # If this was active, find new active
        if chat["active_session"] == session_id:
            # Pick the most-recently-active non-archived session
            candidates = [
                (sid, s["last_active"])
                for sid, s in chat["sessions"].items()
                if s["state"] != STATE_ARCHIVED
            ]
            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                chat["active_session"] = candidates[0][0]
            else:
                chat["active_session"] = None

        return True

    def mark_idle_sessions(self, timeout_seconds: int = DEFAULT_IDLE_TIMEOUT) -> int:
        """Scan all sessions and mark idle any that exceed *timeout_seconds*.

        Only ACTIVE sessions are considered. Returns the count of sessions
        transitioned to IDLE.
        """
        now = time.time()
        count = 0
        for chat_id, chat in self._registry.items():
            for sid, sdata in chat["sessions"].items():
                if sdata["state"] != STATE_ACTIVE:
                    continue
                last = sdata.get("last_active", sdata["created"])
                if now - last >= timeout_seconds:
                    sdata["state"] = STATE_IDLE
                    count += 1
                    logger.debug("Timeout: session %s in chat %s → IDLE", sid, chat_id)
        return count

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def save(self) -> None:
        """Persist the current registry to the state JSON file."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            data = json.dumps(self._registry, indent=2, ensure_ascii=False)
            with open(self._state_file, "w") as fh:
                fh.write(data)
            logger.debug("Saved %d chat(s) to %s", len(self._registry), self._state_file)
        except OSError as exc:
            logger.warning("Failed to save session state: %s", exc)

    def load(self) -> None:
        """Load the registry from the state JSON file.

        Safe to call even if the file does not exist — registry will be empty.
        """
        if not self._state_file.is_file():
            return
        try:
            with open(self._state_file, "r") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._registry = data
            logger.debug("Loaded %d chat(s) from %s", len(self._registry), self._state_file)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load session state: %s", exc)
