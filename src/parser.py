"""
Hermes Multiplex Plugin — Prefix Parser
========================================

Detects routing prefixes in incoming messages and extracts structured
routing information.

Supported patterns:

- ``/multix switch <agent>`` → command: switch
- ``/multix spawn <agent>`` → command: spawn
- ``/multix list [agent]`` → command: list
- ``/multix kill <agent>-<session_id>`` → command: kill
- ``/multix config [get|set ...]`` → command: config
- ``/multix <agent> <msg>`` → command: route (direct agent routing)
- ``@agentname <msg>`` → command: route, agent extracted
- ``@agentname-sessionid <msg>`` → command: route, agent + session_id
- ``@sessionid <msg>`` → command: route, session_id
- bare message → command: route, no agent/session
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Command constants
# ---------------------------------------------------------------------------

COMMAND_ROUTE = "route"       # Normal message routing to a session
COMMAND_SWITCH = "switch"     # Change default agent for this chat
COMMAND_SPAWN = "spawn"       # Create a new session for an agent
COMMAND_LIST = "list"         # List active sessions
COMMAND_KILL = "kill"         # Terminate a specific session
COMMAND_CONFIG = "config"     # Get/set configuration


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParseResult:
    """Structured result of parsing an incoming message.

    Attributes:
        command: One of the COMMAND_* constants.
        agent: Agent name (e.g. "analyst", "coder") or None if not specified.
        message: The user message content with prefix stripped.
        session_id: Specific session ID (4-char hex) or None.
        raw: Original unmodified text.
    """

    command: str
    agent: Optional[str] = None
    message: str = ""
    session_id: Optional[str] = None
    raw: str = ""


# ---------------------------------------------------------------------------
# Recognised management subcommands under /multix
# ---------------------------------------------------------------------------

_MULTIX_SUBCOMMANDS = frozenset({COMMAND_SWITCH, COMMAND_SPAWN, COMMAND_LIST,
                                  COMMAND_KILL, COMMAND_CONFIG})

# Agent names are alphanumeric lowercase, may include underscore
_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_AT_MENTION_RE = re.compile(r"@([a-z][a-z0-9_]*)(?:-([a-f0-9]{4}))?\b")
_SESSION_ID_STANDALONE_RE = re.compile(r"(?:^|\s)@([a-f0-9]{4})\b")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_message(text: str) -> ParseResult:
    """Parse an incoming message for routing prefixes.

    Returns a :class:`ParseResult` with the extracted command, agent,
    session_id, and cleaned message text.

    Args:
        text: Raw incoming message text.

    Returns:
        ParseResult — always non-None.
    """
    stripped = text.strip()
    if not stripped:
        return ParseResult(command=COMMAND_ROUTE, message="", raw=text)

    # --- /multix command parsing ---------------------------------------------
    if stripped.startswith("/multix"):
        return _parse_multix(stripped)

    # --- @agentname or @sessionid mention parsing ----------------------------
    mention_match = _AT_MENTION_RE.search(stripped)
    if mention_match:
        agent_part = mention_match.group(1)
        session_part = mention_match.group(2)

        # Check if agent_part is a known agent name or looks like a session id
        if _AGENT_NAME_RE.match(agent_part):
            # Remove the @mention from the message
            cleaned = _strip_mention(stripped, mention_match)
            return ParseResult(
                command=COMMAND_ROUTE,
                agent=agent_part,
                session_id=session_part,
                message=cleaned,
                raw=text,
            )

    # Check for bare @sessionid (e.g. "@4f2a continue")
    sid_match = _SESSION_ID_STANDALONE_RE.search(stripped)
    if sid_match:
        sid = sid_match.group(1)
        cleaned = _strip_mention(stripped, sid_match)
        return ParseResult(
            command=COMMAND_ROUTE,
            session_id=sid,
            message=cleaned,
            raw=text,
        )

    # --- Fallthrough: bare message, no prefix --------------------------------
    return ParseResult(command=COMMAND_ROUTE, message=stripped, raw=text)


def _strip_mention(text: str, match: re.Match) -> str:
    """Remove the @mention span from *text*, collapsing whitespace."""
    start, end = match.start(), match.end()
    before = text[:start]
    after = text[end:]
    # Collapse double spaces
    result = (before + after).strip()
    result = re.sub(r"  +", " ", result)
    return result


def _parse_multix(text: str) -> ParseResult:
    """Parse /multix ... command."""
    # Remove leading /multix
    rest = re.sub(r"^/multix\s*", "", text, count=1).strip()

    if not rest:
        # Bare "/multix" — treat as plain message
        return ParseResult(command=COMMAND_ROUTE, message=text, raw=text)

    parts = rest.split()

    # /multix switch <agent>
    if parts[0] in _MULTIX_SUBCOMMANDS:
        sub = parts[0]
        if sub == COMMAND_LIST:
            # /multix list [agent]
            agent = parts[1] if len(parts) > 1 else None
            return ParseResult(command=COMMAND_LIST, agent=agent, raw=text)

        elif sub == COMMAND_SWITCH:
            # /multix switch <agent>
            agent = parts[1] if len(parts) > 1 else None
            return ParseResult(command=COMMAND_SWITCH, agent=agent, raw=text)

        elif sub == COMMAND_SPAWN:
            # /multix spawn <agent>
            agent = parts[1] if len(parts) > 1 else None
            return ParseResult(command=COMMAND_SPAWN, agent=agent, raw=text)

        elif sub == COMMAND_KILL:
            # /multix kill <agent>-<session_id>
            if len(parts) > 1:
                target = parts[1]
                # e.g. "analyst-4f2a" → agent="analyst", session_id="4f2a"
                if "-" in target:
                    agent_part, sid = target.rsplit("-", 1)
                    return ParseResult(
                        command=COMMAND_KILL,
                        agent=agent_part,
                        session_id=sid,
                        raw=text,
                    )
                # Just a session ID or agent name?
                if _SESSION_ID_STANDALONE_RE.match("@" + target):
                    return ParseResult(
                        command=COMMAND_KILL,
                        session_id=target,
                        raw=text,
                    )
                return ParseResult(command=COMMAND_KILL, agent=target, raw=text)
            return ParseResult(command=COMMAND_KILL, raw=text)

        elif sub == COMMAND_CONFIG:
            # /multix config [get|set key [value]]
            msg = " ".join(parts[1:]) if len(parts) > 1 else ""
            return ParseResult(command=COMMAND_CONFIG, message=msg, raw=text)

    # /multix <agent> <message> — direct route
    agent = parts[0]
    msg = " ".join(parts[1:]) if len(parts) > 1 else ""
    return ParseResult(command=COMMAND_ROUTE, agent=agent, message=msg, raw=text)
