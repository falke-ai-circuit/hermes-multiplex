"""
Hermes Multiplex Plugin — Plugin Registration
==============================================

Entry point for the Hermes Agent plugin system. On gateway startup, Hermes
calls :func:`register` with a ``PluginContext``. The multiplex plugin hooks
into the message dispatch pipeline to intercept messages before profile
routing, detect ``/multix`` / ``@agentname`` / ``@sessionid`` prefixes, and
route messages to the appropriate agent session.

Architecture
------------

::

    Telegram Message
          │
          ▼
    ┌─────────────────────┐
    │  pre_gateway_dispatch│  ← multiplex hooks HERE (gateway/run.py:5804)
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │   Parse message      │  → parse_message() from parser.py
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │   Resolve session    │  → tracker.create_session / get_active
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │   Route to profile   │  → gateway routes to agent's profile
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │   Agent responds     │
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │   Relay with prefix  │  → relay.format_response()
    └─────────────────────┘
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state — initialised by register()
# ---------------------------------------------------------------------------

_tracker = None
_config_loaded = False
_plugin_dir: Optional[Path] = None

# Public API reference — for gateway hooks to access
tracker = None  # type: ignore  — will be SessionTracker after register()
config = None   # type: ignore  — reference to config module


# ---------------------------------------------------------------------------
# Plugin lifecycle — called by Hermes on gateway startup
# ---------------------------------------------------------------------------

def register(context: Any) -> None:
    """Called by Hermes Agent on gateway startup.

    Args:
        context: A ``PluginContext`` object providing access to:
            - ``context.config`` — the gateway's merged config
            - ``context.register_hook(name, callback)`` — gateway hook registration
            - ``context.register_command(name, handler)`` — slash command registration
            - ``context.plugin_dir`` — this plugin's directory
    """
    global _tracker, _config_loaded, _plugin_dir, tracker, config

    _plugin_dir = Path(context.plugin_dir) if hasattr(context, "plugin_dir") else Path(__file__).parent.parent
    logger.info("Multiplex plugin init — plugin_dir=%s", _plugin_dir)

    # --- Load configuration ------------------------------------------------
    from . import config as cfg
    config = cfg
    cfg.load_config(str(_plugin_dir / "config" / "config.yaml"))
    _config_loaded = True

    # --- Initialise session tracker ----------------------------------------
    from .tracker import SessionTracker
    _tracker = SessionTracker(state_dir=cfg.get_state_dir())
    _tracker.load()
    tracker = _tracker
    logger.debug("Session tracker initialised — %d chats loaded",
                 len(_tracker._registry))

    # --- Register gateway hooks --------------------------------------------
    # pre_gateway_dispatch: fires before the profile router — our injection point
    context.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)

    # post_response: fires after agent generates a response — prefix injection
    context.register_hook("post_response", _post_response)

    # --- Register slash commands -------------------------------------------
    context.register_command("/multix", _handle_multix)

    logger.info("Multiplex plugin registered — %d agents, %d platforms",
                len(cfg._agents),
                sum(1 for v in cfg._platforms.values() if v.get("enabled")))


# ---------------------------------------------------------------------------
# Gateway hook: pre_gateway_dispatch
# ---------------------------------------------------------------------------

def _pre_gateway_dispatch(message: Dict[str, Any]) -> Dict[str, Any]:
    """Intercept message BEFORE profile routing.

    Called by ``gateway/run.py:5804`` with the raw incoming message dict.

    Expected message keys:
        - ``text`` (str): Message content
        - ``platform`` (str): e.g. "telegram"
        - ``chat_id`` (str): Platform-specific chat identifier
        - ``user_id`` (str, optional): Sender identifier
        - ``profile`` (str, optional): Current target profile (unset when entering)

    Returns:
        Modified message dict with ``multiplex`` routing metadata injected.
        If ``return None``, the message passes through unchanged (no prefix
        detected — conductor default).
    """
    if not _config_loaded or _tracker is None:
        return message  # Plugin not ready — pass through

    text = (message.get("text") or "").strip()
    if not text:
        return message

    platform = message.get("platform", "unknown")
    chat_id = message.get("chat_id", "")

    # Respect platform config — skip if platform is disabled
    from . import config as cfg
    if not cfg.is_platform_enabled(platform):
        return message

    # --- Parse the message for multiplex prefixes -------------------------
    from .parser import (
        parse_message, COMMAND_ROUTE, COMMAND_SWITCH, COMMAND_SPAWN,
        COMMAND_LIST, COMMAND_KILL, COMMAND_CONFIG,
    )

    parsed = parse_message(text)

    # Bare message with no prefix → pass through to conductor (default)
    if parsed.command == COMMAND_ROUTE and not parsed.agent and not parsed.session_id:
        return message

    logger.debug("Multiplex: parsed=%s agent=%s sid=%s msg=%s...",
                 parsed.command, parsed.agent, parsed.session_id,
                 parsed.message[:50] if parsed.message else "")

    # --- Handle management commands ----------------------------------------
    if parsed.command != COMMAND_ROUTE:
        return _handle_management_command(parsed, chat_id, message)

    # --- Route to agent session --------------------------------------------
    agent = parsed.agent

    # Resolve agent from session_id if only session_id was provided
    if not agent and parsed.session_id:
        # Look up which agent owns this session
        sessions = _tracker.list_sessions(chat_id)
        for s in sessions:
            if s["session_id"] == parsed.session_id:
                agent = s["agent"]
                break
        if not agent:
            logger.debug("Session %s not found in chat %s", parsed.session_id, chat_id)
            return message

    # If we still don't have an agent, pass through
    if not agent:
        return message

    # Verify agent exists in config
    agent_cfg = cfg.get_agent_config(agent)
    if agent_cfg is None:
        logger.debug("Unknown agent '%s' — passing through", agent)
        return message

    # Auto-create session if needed
    sessions_for_agent = _tracker.list_sessions(chat_id, agent_filter=agent)
    if not sessions_for_agent and agent_cfg.get("auto_create", True):
        session_id = _tracker.create_session(chat_id, agent)
        _tracker.save()
        logger.info("Auto-created session %s for agent=%s in chat=%s",
                    session_id, agent, chat_id)
        active_session = session_id
    elif sessions_for_agent:
        # Use the most recently active session
        active_session = sessions_for_agent[0]["session_id"]
        _tracker.set_active(chat_id, active_session)
    else:
        # Agent exists but has no sessions and auto_create is disabled
        logger.debug("Agent %s has auto_create=False and no sessions", agent)
        return message

    # --- Inject routing metadata into message ------------------------------
    message["multiplex"] = {
        "routed": True,
        "agent": agent,
        "session_id": active_session,
        "profile": agent_cfg.get("profile", agent),
        "message": parsed.message or text,
        "original_prefix": parsed.agent or parsed.session_id,
    }

    # Override text to strip the prefix so the agent doesn't see it
    message["text"] = parsed.message or ""

    # Tell the profile router which profile to use
    message["profile"] = agent_cfg.get("profile", agent)

    logger.info("Routing → agent=%s session=%s profile=%s msg_len=%d",
                agent, active_session, message["profile"],
                len(message["text"]))

    return message


# ---------------------------------------------------------------------------
# Gateway hook: post_response
# ---------------------------------------------------------------------------

def _post_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """Intercept agent response AFTER generation, BEFORE delivery.

    Injects session-aware prefix into the response text so the user
    knows which agent produced the output.
    """
    multiplex_meta = response.get("multiplex")
    if not multiplex_meta or not multiplex_meta.get("routed"):
        # Not a multiplex-routed message — pass through
        return response

    from .relay import format_response

    agent = multiplex_meta.get("agent", "unknown")
    session_id = multiplex_meta.get("session_id", "00000000")
    prefix_format = config.get_setting("prefix_format", "[{agent}-{session_id}]")

    text = response.get("text", "") or response.get("content", "")
    if text:
        response["text"] = format_response(agent, session_id, text, prefix_format)

        # Log delegation chain if configured
        if config.get_setting("show_delegation_chain", True):
            parent = multiplex_meta.get("delegation_parent")
            if parent:
                response["text"] = format_response(
                    agent, session_id, response.get("text", ""),
                    prefix_format, delegation_parent=parent
                )

    return response


# ---------------------------------------------------------------------------
# Management command handler
# ---------------------------------------------------------------------------

def _handle_management_command(
    parsed: Any,
    chat_id: str,
    message: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Handle /multix management commands (switch, spawn, list, kill, config).

    These commands are handled by the conductor directly — they don't
    route to agent profiles. The response is injected into the message
    so the gateway can relay it immediately.
    """
    from .commands import (
        handle_switch, handle_spawn, handle_list, handle_kill, handle_config,
    )
    from .parser import (
        COMMAND_SWITCH, COMMAND_SPAWN, COMMAND_LIST, COMMAND_KILL, COMMAND_CONFIG,
    )

    auto_switch = config.get_setting("auto_switch_on_spawn", True)

    try:
        if parsed.command == COMMAND_SWITCH:
            response_text = handle_switch(parsed, chat_id, _tracker)
        elif parsed.command == COMMAND_SPAWN:
            response_text = handle_spawn(parsed, chat_id, _tracker, auto_create=auto_switch)
        elif parsed.command == COMMAND_LIST:
            response_text = handle_list(parsed, chat_id, _tracker)
        elif parsed.command == COMMAND_KILL:
            response_text = handle_kill(parsed, chat_id, _tracker)
        elif parsed.command == COMMAND_CONFIG:
            response_text = handle_config(parsed, chat_id)
        else:
            response_text = f"Unknown command: {parsed.command}"
    except Exception as exc:
        logger.exception("Management command failed: %s", parsed.command)
        response_text = f"**Error:** {exc}"

    # Inject response so gateway delivers it directly
    message["multiplex"] = {
        "routed": True,
        "agent": "conductor",
        "session_id": "mgmt",
        "profile": "conductor",
        "message": response_text,
        "is_management": True,
    }
    message["text"] = response_text
    message["profile"] = "conductor"  # Management commands run as conductor
    return message


# ---------------------------------------------------------------------------
# Slash command handler — /multix
# ---------------------------------------------------------------------------

def _handle_multix(args: str, context: Any) -> str:
    """Handler registered for the /multix slash command.

    This is a secondary entry point — most routing happens via the
    ``pre_gateway_dispatch`` hook. This handler exists so the command
    appears in Hermes' command registry.
    """
    return (
        "**Hermes Multiplex** — agent session router\n\n"
        "• `/multix <agent> <msg>` — route message to agent\n"
        "• `/multix switch <agent>` — set default agent\n"
        "• `/multix spawn <agent>` — create new session\n"
        "• `/multix list [agent]` — show active sessions\n"
        "• `/multix kill <agent>-<id>` — terminate session\n"
        "• `/multix config [get|set]` — settings\n\n"
        "Or just use `@agentname <msg>` — faster."
    )
