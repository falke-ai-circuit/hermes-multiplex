"""
Hermes Multiplex Plugin — Command Handlers
===========================================

Handles the five management commands dispatched by the prefix parser:

- ``/multix switch <agent>`` → activate an existing session for *agent*
- ``/multix spawn <agent>`` → create a new session for *agent*
- ``/multix list [agent]`` → list active sessions, optionally filtered
- ``/multix kill <agent>-<session_id>`` → terminate a session
- ``/multix config [get|set] [key] [value]`` → runtime configuration

All handlers take a :class:`ParseResult` from :mod:`parser` and return a
Markdown-formatted response string suitable for relay to the chat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import logging

if TYPE_CHECKING:
    from parser import ParseResult
    from tracker import SessionTracker

from parser import COMMAND_SWITCH, COMMAND_SPAWN, COMMAND_LIST, COMMAND_KILL, COMMAND_CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public handlers — each returns (response_text, target_agent | None)
# ---------------------------------------------------------------------------

def handle_switch(
    parsed: "ParseResult",
    chat_id: str,
    tracker: "SessionTracker",
) -> str:
    """Switch the active session for *chat_id* to *parsed.agent*.

    If the agent has no active sessions, returns a helpful message suggesting
    ``/multix spawn``. If multiple sessions exist, activates the most recently
    active one.
    """
    agent = parsed.agent
    if not agent:
        return "**`/multix switch`** requires an agent name.\n\nUsage: `/multix switch analyst`"

    # Find all sessions for this agent in this chat
    sessions = tracker.list_sessions(chat_id, agent_filter=agent)
    if not sessions:
        return (
            f"No active session found for **{agent}** in this chat.\n\n"
            f"Create one with: `/multix spawn {agent}`"
        )

    # Pick the most-recently-active session
    best = sessions[0]
    session_id = best["session_id"]

    ok = tracker.set_active(chat_id, session_id)
    if not ok:
        return f"Failed to activate session `{session_id}` for **{agent}**."

    tracker.save()
    return (
        f"Switched to **{agent}** session `{session_id}` "
        f"(idle {best['idle_seconds']}s). "
        f"All messages now route to {agent}."
    )


def handle_spawn(
    parsed: "ParseResult",
    chat_id: str,
    tracker: "SessionTracker",
    auto_create: bool = True,
) -> str:
    """Create a new session for *parsed.agent*.

    If *auto_create* is True (from config), the new session becomes active
    immediately.
    """
    agent = parsed.agent
    if not agent:
        return "**`/multix spawn`** requires an agent name.\n\nUsage: `/multix spawn architect`"

    sid = tracker.create_session(chat_id, agent)
    tracker.save()

    if auto_create:
        return (
            f"Spawned new **{agent}** session `{sid}`.\n"
            f"Session is now active. Use `/multix switch main` to return to conductor."
        )

    return f"Spawned new **{agent}** session `{sid}` (standby mode)."


def handle_list(
    parsed: "ParseResult",
    chat_id: str,
    tracker: "SessionTracker",
) -> str:
    """List active sessions for *chat_id*, optionally filtered by agent."""
    agent_filter = parsed.agent  # May be None
    sessions = tracker.list_sessions(chat_id, agent_filter=agent_filter)

    if not sessions:
        header = (
            f"No active sessions found for **{agent_filter}** in this chat."
            if agent_filter
            else "No active sessions in this chat."
        )
        return f"{header}\n\nSpawn one with `/multix spawn <agent>`."

    lines = []
    if agent_filter:
        lines.append(f"**{agent_filter} sessions:**")
    else:
        lines.append("**Active sessions:**")

    active_id = tracker.get_active(chat_id)
    for s in sessions:
        marker = " ← `active`" if s["session_id"] == active_id else ""
        stale = ""
        if s["idle_seconds"] > 3600:
            stale = f" ⚠️ idle {s['idle_seconds'] // 3600}h"
        elif s["idle_seconds"] > 300:
            stale = f" idle {s['idle_seconds'] // 60}m"

        lines.append(
            f"• **{s['agent']}** `{s['session_id']}` — "
            f"{s['state']}{stale}{marker}"
        )

    return "\n".join(lines)


def handle_kill(
    parsed: "ParseResult",
    chat_id: str,
    tracker: "SessionTracker",
) -> str:
    """Archive a session. The session's state is preserved but it will not
    appear in /multix list and cannot be activated."""
    session_id = parsed.session_id
    agent = parsed.agent

    if not session_id and not agent:
        return "**`/multix kill`** requires an agent name or session ID.\n\nUsage: `/multix kill analyst-4f2a`"

    # If only agent (no session_id), find and kill the most recent session
    if not session_id and agent:
        sessions = tracker.list_sessions(chat_id, agent_filter=agent)
        if not sessions:
            return f"No active sessions for **{agent}** to kill."
        session_id = sessions[0]["session_id"]

    ok = tracker.archive_session(chat_id, session_id)
    if not ok:
        return f"Session `{session_id}` not found or already archived."

    tracker.save()
    return f"Session `{session_id}` archived. Use `/multix spawn {agent or '<agent>'}` to create a new one."


def handle_config(
    parsed: "ParseResult",
    chat_id: str,
) -> str:
    """Get or set multiplex configuration.

    Parsed.message contains the rest:
      - ``get key`` → returns current value
      - ``set key value`` → sets and returns new value
      - empty → returns current config summary
    """
    from config import _settings, _agents, _platforms

    msg = parsed.message.strip()
    if not msg:
        # Summary
        lines = ["**Multiplex config:**"]
        lines.append(f"• default_agent: `{_settings.get('default_agent')}`")
        lines.append(f"• auto_switch_on_spawn: `{_settings.get('auto_switch_on_spawn')}`")
        lines.append(f"• session_idle_timeout: `{_settings.get('session_idle_timeout')}s`")
        lines.append(f"• show_delegation_chain: `{_settings.get('show_delegation_chain')}`")
        lines.append(f"• agents: {len(_agents)} configured")
        lines.append(f"• platforms: {', '.join(k for k,v in _platforms.items() if v.get('enabled'))}")
        return "\n".join(lines)

    parts = msg.split()
    if len(parts) < 2 or parts[0] not in ("get", "set"):
        return (
            "**`/multix config`** usage:\n"
            "• `/multix config` — show summary\n"
            "• `/multix config get <key>` — get a setting\n"
            "• `/multix config set <key> <value>` — set a setting"
        )

    action, key = parts[0], parts[1]

    if action == "get":
        val = _settings.get(key)
        if val is None:
            # Try agent config
            agent = _agents.get(key)
            if agent:
                return f"**{key}**: `{agent}`"
            return f"Setting `{key}` not found."
        return f"**{key}**: `{val}`"

    # set
    val = " ".join(parts[2:]) if len(parts) > 2 else ""
    # Interpret booleans/ints
    if val.lower() in ("true", "yes"):
        val = True
    elif val.lower() in ("false", "no"):
        val = False
    elif val.isdigit():
        val = int(val)

    _settings[key] = val
    # Note: persistence would need a save_config() — for now, runtime only
    return f"**{key}** set to `{val}` (runtime only — restart resets)"
