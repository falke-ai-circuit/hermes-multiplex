"""
Tests for multiplex tracker — session state management.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tracker import (
    SessionTracker,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_ARCHIVED,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_state_dir():
    """Create a temporary state directory and return it."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def tracker(temp_state_dir):
    """Create a SessionTracker pointed at a temp dir."""
    return SessionTracker(state_dir=temp_state_dir)


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------

class TestCreateSession:
    """Test session creation."""

    def test_create_session_returns_id(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        assert sid is not None
        assert len(sid) == 8  # 8-char hex

    def test_create_session_unique_ids(self, tracker):
        s1 = tracker.create_session("chat_tg_123", "analyst")
        s2 = tracker.create_session("chat_tg_123", "coder")
        assert s1 != s2

    def test_create_session_starts_active(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        session = tracker._registry["chat_tg_123"]["sessions"][sid]
        assert session["state"] == STATE_ACTIVE

    def test_create_session_sets_created_time(self, tracker):
        before = time.time()
        sid = tracker.create_session("chat_tg_123", "analyst")
        after = time.time()
        session = tracker._registry["chat_tg_123"]["sessions"][sid]
        assert before <= session["created"] <= after

    def test_create_session_updates_last_active(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        session = tracker._registry["chat_tg_123"]["sessions"][sid]
        assert session["last_active"] is not None


# ---------------------------------------------------------------------------
# Active session management
# ---------------------------------------------------------------------------

class TestActiveSession:
    """Test get_active / set_active."""

    def test_get_active_returns_none_when_no_sessions(self, tracker):
        assert tracker.get_active("chat_empty") is None

    def test_get_active_returns_most_recently_created(self, tracker):
        s1 = tracker.create_session("chat_tg_123", "analyst")
        s2 = tracker.create_session("chat_tg_123", "coder")
        # Most recently created should be active
        assert tracker.get_active("chat_tg_123") == s2

    def test_set_active_changes_active(self, tracker):
        s1 = tracker.create_session("chat_tg_123", "analyst")
        s2 = tracker.create_session("chat_tg_123", "coder")
        # s2 is active by default (most recent); set to s1
        tracker.set_active("chat_tg_123", s1)
        assert tracker.get_active("chat_tg_123") == s1

    def test_set_active_updates_last_active(self, tracker):
        s1 = tracker.create_session("chat_tg_123", "analyst")
        s2 = tracker.create_session("chat_tg_123", "coder")
        tracker.set_active("chat_tg_123", s2)
        # Touch the session timestamp
        session = tracker._registry["chat_tg_123"]["sessions"][s2]
        assert session["last_active"] is not None


# ---------------------------------------------------------------------------
# List sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    """Test list_sessions."""

    def test_list_empty_chat(self, tracker):
        assert tracker.list_sessions("empty_chat") == []

    def test_list_returns_all_sessions(self, tracker):
        tracker.create_session("chat_tg_123", "analyst")
        tracker.create_session("chat_tg_123", "coder")
        sessions = tracker.list_sessions("chat_tg_123")
        assert len(sessions) == 2

    def test_list_includes_agent_and_state(self, tracker):
        sid = tracker.create_session("chat_tg_123", "researcher")
        sessions = tracker.list_sessions("chat_tg_123")
        s = sessions[0]
        assert s["session_id"] == sid
        assert s["agent"] == "researcher"
        assert s["state"] == STATE_ACTIVE

    def test_list_includes_idle_seconds(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        sessions = tracker.list_sessions("chat_tg_123")
        s = sessions[0]
        assert s["idle_seconds"] >= 0

    def test_list_respects_agent_filter(self, tracker):
        tracker.create_session("chat_tg_123", "analyst")
        tracker.create_session("chat_tg_123", "coder")
        sessions = tracker.list_sessions("chat_tg_123", agent_filter="coder")
        assert len(sessions) == 1
        assert sessions[0]["agent"] == "coder"


# ---------------------------------------------------------------------------
# Mark idle / state transitions
# ---------------------------------------------------------------------------

class TestMarkIdle:
    """Test idle state transitions."""

    def test_mark_idle_active_to_idle(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        result = tracker.mark_idle("chat_tg_123", sid)
        assert result is True
        assert tracker._registry["chat_tg_123"]["sessions"][sid]["state"] == STATE_IDLE

    def test_mark_idle_already_idle(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        tracker.mark_idle("chat_tg_123", sid)
        result = tracker.mark_idle("chat_tg_123", sid)
        assert result is False  # No state change

    def test_mark_idle_nonexistent_session(self, tracker):
        result = tracker.mark_idle("chat_tg_123", "deadbeef")
        assert result is False

    def test_resume_idle_session(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        tracker.mark_idle("chat_tg_123", sid)
        # Setting it as active again should restore ACTIVE state
        tracker.set_active("chat_tg_123", sid)
        assert tracker._registry["chat_tg_123"]["sessions"][sid]["state"] == STATE_ACTIVE


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

class TestArchive:
    """Test archive operations."""

    def test_archive_session(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        result = tracker.archive_session("chat_tg_123", sid)
        assert result is True
        assert tracker._registry["chat_tg_123"]["sessions"][sid]["state"] == STATE_ARCHIVED

    def test_archive_nonexistent(self, tracker):
        result = tracker.archive_session("chat_tg_123", "deadbeef")
        assert result is False

    def test_archive_switches_active(self, tracker):
        s1 = tracker.create_session("chat_tg_123", "analyst")
        s2 = tracker.create_session("chat_tg_123", "coder")
        # s2 is active. Archive s2 → s1 becomes active
        tracker.archive_session("chat_tg_123", s2)
        assert tracker.get_active("chat_tg_123") == s1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    """Test save/load of session state to JSON."""

    def test_save_creates_file(self, temp_state_dir):
        t = SessionTracker(state_dir=temp_state_dir)
        t.create_session("chat_tg_123", "analyst")
        t.save()
        state_file = temp_state_dir / "sessions.json"
        assert state_file.is_file()

    def test_load_restores_state(self, temp_state_dir):
        t1 = SessionTracker(state_dir=temp_state_dir)
        sid = t1.create_session("chat_tg_123", "analyst")
        t1.save()

        t2 = SessionTracker(state_dir=temp_state_dir)
        t2.load()
        assert t2.get_active("chat_tg_123") == sid
        sessions = t2.list_sessions("chat_tg_123")
        assert len(sessions) == 1
        assert sessions[0]["agent"] == "analyst"

    def test_load_missing_file_safe(self, temp_state_dir):
        t = SessionTracker(state_dir=temp_state_dir)
        # No file exists — should not raise
        t.load()
        assert t._registry == {}

    def test_roundtrip_multiple_chats(self, temp_state_dir):
        t1 = SessionTracker(state_dir=temp_state_dir)
        t1.create_session("chat_tg_A", "analyst")
        t1.create_session("chat_tg_A", "coder")
        t1.create_session("chat_tg_B", "researcher")
        t1.save()

        t2 = SessionTracker(state_dir=temp_state_dir)
        t2.load()
        assert len(t2.list_sessions("chat_tg_A")) == 2
        assert len(t2.list_sessions("chat_tg_B")) == 1


# ---------------------------------------------------------------------------
# Idle timeout scan
# ---------------------------------------------------------------------------

class TestIdleScan:
    """Test the mark_idle_sessions scan."""

    def test_scan_marks_idle_after_timeout(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        # Artificially age the session
        tracker._registry["chat_tg_123"]["sessions"][sid]["last_active"] = time.time() - 99999
        count = tracker.mark_idle_sessions(timeout_seconds=900)
        assert count > 0
        assert tracker._registry["chat_tg_123"]["sessions"][sid]["state"] == STATE_IDLE

    def test_scan_ignores_active_within_timeout(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        # Just now — should not be marked idle
        count = tracker.mark_idle_sessions(timeout_seconds=900)
        assert count == 0
        assert tracker._registry["chat_tg_123"]["sessions"][sid]["state"] == STATE_ACTIVE

    def test_scan_skips_archived(self, tracker):
        sid = tracker.create_session("chat_tg_123", "analyst")
        tracker.archive_session("chat_tg_123", sid)
        tracker._registry["chat_tg_123"]["sessions"][sid]["last_active"] = time.time() - 99999
        count = tracker.mark_idle_sessions(timeout_seconds=900)
        assert count == 0  # Archived sessions are never idled
