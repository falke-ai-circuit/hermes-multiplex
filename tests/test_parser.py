"""
Tests for multiplex parser — prefix detection and structured result extraction.
"""

import pytest

# Import the module under test.
# In the actual Hermes plugin environment this would be `from src.parser import ...`
# but for tests we import directly.
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from parser import (
    parse_message,
    ParseResult,
    COMMAND_SWITCH,
    COMMAND_SPAWN,
    COMMAND_LIST,
    COMMAND_KILL,
    COMMAND_CONFIG,
    COMMAND_ROUTE,
)


# ---------------------------------------------------------------------------
# /multix commands
# ---------------------------------------------------------------------------

class TestMultixCommands:
    """Parse /multix <command> <args> pattern."""

    def test_multix_switch_agent(self):
        result = parse_message("/multix switch analyst")
        assert result.command == COMMAND_SWITCH
        assert result.agent == "analyst"
        assert result.message == ""
        assert result.session_id is None

    def test_multix_switch_main(self):
        result = parse_message("/multix switch main")
        assert result.command == COMMAND_SWITCH
        assert result.agent == "main"
        assert result.session_id is None

    def test_multix_spawn_agent(self):
        result = parse_message("/multix spawn researcher")
        assert result.command == COMMAND_SPAWN
        assert result.agent == "researcher"
        assert result.session_id is None

    def test_multix_list(self):
        result = parse_message("/multix list")
        assert result.command == COMMAND_LIST
        assert result.agent is None
        assert result.session_id is None

    def test_multix_list_agent(self):
        result = parse_message("/multix list analyst")
        assert result.command == COMMAND_LIST
        assert result.agent == "analyst"

    def test_multix_kill_session(self):
        result = parse_message("/multix kill analyst-4f2a")
        assert result.command == COMMAND_KILL
        assert result.agent == "analyst"
        assert result.session_id == "4f2a"

    def test_multix_config(self):
        result = parse_message("/multix config")
        assert result.command == COMMAND_CONFIG
        assert result.agent is None

    def test_multix_config_get(self):
        result = parse_message("/multix config get default_agent")
        assert result.command == COMMAND_CONFIG
        # config action and key are stuffed into message
        assert "get" in result.message
        assert "default_agent" in result.message

    def test_multix_direct_route(self):
        """Bare `/multix analyst investigate this` → route to analyst."""
        result = parse_message("/multix analyst investigate this")
        assert result.command == COMMAND_ROUTE
        assert result.agent == "analyst"
        assert result.message == "investigate this"
        assert result.session_id is None

    def test_multix_extra_whitespace(self):
        result = parse_message("  /multix   switch   coder  ")
        assert result.command == COMMAND_SWITCH
        assert result.agent == "coder"


# ---------------------------------------------------------------------------
# @agentname mentions
# ---------------------------------------------------------------------------

class TestAgentMentions:
    """Parse @agentname message patterns."""

    def test_at_agent_with_message(self):
        result = parse_message("@analyst investigate gateway OOM")
        assert result.command == COMMAND_ROUTE
        assert result.agent == "analyst"
        assert result.message == "investigate gateway OOM"
        assert result.session_id is None

    def test_at_agent_only(self):
        result = parse_message("@coder")
        assert result.command == COMMAND_ROUTE
        assert result.agent == "coder"
        assert result.message == ""

    def test_at_agent_with_dash(self):
        result = parse_message("@analyst-4f2a what was that confidence?")
        assert result.command == COMMAND_ROUTE
        assert result.agent == "analyst"
        assert result.session_id == "4f2a"
        assert result.message == "what was that confidence?"

    def test_at_sessionid_route(self):
        result = parse_message("@4f2a continue the analysis")
        assert result.command == COMMAND_ROUTE
        assert result.agent is None
        assert result.session_id == "4f2a"
        assert result.message == "continue the analysis"

    def test_at_agent_mid_message(self):
        """@mention in the middle of message — still detected."""
        result = parse_message("hey @researcher find alternatives")
        assert result.command == COMMAND_ROUTE
        assert result.agent == "researcher"
        assert result.message == "hey find alternatives"

    def test_at_unknown_agent(self):
        """Unknown agent name — should still be parsed as a route attempt."""
        result = parse_message("@unknown_agent do something")
        assert result.command == COMMAND_ROUTE
        assert result.agent == "unknown_agent"
        assert result.message == "do something"


# ---------------------------------------------------------------------------
# No-prefix / fallback
# ---------------------------------------------------------------------------

class TestNoPrefix:
    """Messages without any routing prefix."""

    def test_plain_message(self):
        result = parse_message("hello world")
        assert result.command == COMMAND_ROUTE
        assert result.agent is None
        assert result.session_id is None
        assert result.message == "hello world"

    def test_empty_message(self):
        result = parse_message("")
        assert result.command == COMMAND_ROUTE
        assert result.agent is None
        assert result.message == ""

    def test_only_at_symbol(self):
        result = parse_message("@")
        assert result.command == COMMAND_ROUTE
        assert result.agent is None
        assert result.session_id is None
        assert result.message == "@"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Corner cases and malformed input."""

    def test_multix_with_no_args(self):
        result = parse_message("/multix")
        assert result.command == COMMAND_ROUTE  # falls through — treated as plain
        assert result.agent is None

    def test_multix_unknown_subcommand(self):
        result = parse_message("/multix foobar xyz")
        # Treated as bare /multix agent route attempt
        assert result.command == COMMAND_ROUTE
        assert result.agent == "foobar"
        assert result.message == "xyz"

    def test_at_with_trailing_whitespace(self):
        result = parse_message("@analyst   ")
        assert result.command == COMMAND_ROUTE
        assert result.agent == "analyst"
        assert result.message == ""

    def test_multiple_mentions_first_wins(self):
        """First @mention determines routing."""
        result = parse_message("@analyst review @coder fix")
        assert result.command == COMMAND_ROUTE
        assert result.agent == "analyst"
        assert "review" in result.message
