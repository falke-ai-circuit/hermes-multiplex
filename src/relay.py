"""
Hermes Multiplex Plugin — Response Relay
=========================================

Formats agent responses with session-aware prefixes before delivery to the
chat platform. Handles three response modes:

1. **Direct route** — User sent `@analyst`, response gets `[analyst-4f2a]` prefix
2. **Delegation chain** — Conductor spawned subagent, prefix shows chain
3. **Conductor summary** — When subagent finishes, conductor relays summary

The relay does NOT modify the underlying message content — it only injects
the prefix for display clarity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from tracker import SessionTracker
    from config import Config  # noqa

import logging

logger = logging.getLogger(__name__)


def format_response(
    agent_name: str,
    session_id: str,
    message: str,
    prefix_format: str = "[{agent}-{session_id}]",
    delegation_parent: Optional[str] = None,
) -> str:
    """Format an agent response with session-aware prefix.

    Args:
        agent_name: The agent's display name (e.g. "analyst", "coder").
        session_id: The session's 8-char hex ID.
        message: The agent's raw response text.
        prefix_format: Template for the prefix. Use ``{agent}`` and
                       ``{session_id}`` placeholders.
        delegation_parent: If this agent was spawned by another, prepend
                           the parent chain. E.g. ``main-d9e3→``.

    Returns:
        Prefixed message string ready for relay to the platform.
    """
    prefix = prefix_format.format(agent=agent_name, session_id=session_id)

    if delegation_parent:
        prefix = f"{delegation_parent}→{prefix}"

    # Normalise: ensure prefix is on its own line if message starts with text
    if message.lstrip().startswith(prefix):
        # Already prefixed — don't double-up
        return message

    # Insert prefix at the start of the message
    return f"{prefix} {message}"


def format_conductor_summary(
    agent_name: str,
    session_id: str,
    summary: str,
    prefix_format: str = "[{agent}-{session_id}]",
) -> str:
    """Format a summary relay when a subagent completes and the conductor
    announces the result to the chat.

    The conductor's own prefix is NOT prepended — this message appears
    inline in the conductor's stream.
    """
    prefix = prefix_format.format(agent=agent_name, session_id=session_id)
    return f"🔔 **{agent_name}** ({prefix}) finished:\n\n{summary}"


def extract_agent_from_prefix(text: str) -> Optional[str]:
    """Given a prefixed response like ``[analyst-4f2a] Root cause found``,
    extract the agent name. Returns None if no prefix detected.

    This is used by the conductor to determine which agent session produced
    a response when routing replies back to the user.
    """
    import re
    match = re.match(r"^\[([a-z_]+)-[a-f0-9]{8}\]", text)
    if match:
        return match.group(1)
    return None
