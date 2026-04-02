"""Tests for barge-in interrupt debouncing.

The debounce gate requires 2 interrupt signals within INTERRUPT_DEBOUNCE_WINDOW
(default 0.8s) to confirm a barge-in. A single signal is recorded but not acted
on — it may be a false positive from speaker bleed.
"""

import time
from unittest.mock import patch

from app.models import ConversationSession


class TestInterruptDebounce:
    """Tests for ConversationSession.check_interrupt_debounce()."""

    def test_first_signal_returns_false(self):
        """First interrupt signal should be recorded but not confirmed."""
        session = ConversationSession()
        assert session.check_interrupt_debounce("cancel_all") is False
        assert session.pending_interrupt_time > 0
        assert session.pending_interrupt_mode == "cancel_all"

    def test_two_signals_within_window_confirms(self):
        """Two signals within the debounce window should confirm barge-in."""
        session = ConversationSession()

        # First signal
        assert session.check_interrupt_debounce("cancel_all") is False

        # Second signal immediately after — within window
        assert session.check_interrupt_debounce("cancel_all") is True

        # State should be reset after confirmation
        assert session.pending_interrupt_time == 0.0
        assert session.pending_interrupt_mode is None

    def test_two_signals_outside_window_does_not_confirm(self):
        """Two signals separated by more than the window should not confirm."""
        session = ConversationSession()

        # First signal
        assert session.check_interrupt_debounce("cancel_all") is False
        first_time = session.pending_interrupt_time

        # Simulate time passing beyond the window
        session.pending_interrupt_time = (
            time.monotonic() - session.INTERRUPT_DEBOUNCE_WINDOW - 0.1
        )

        # Second signal — too late, becomes a new pending
        assert session.check_interrupt_debounce("cancel_all") is False
        # Should have recorded a new pending time
        assert session.pending_interrupt_time > first_time

    def test_third_signal_after_expired_window_starts_new_debounce(self):
        """After an expired window, the next pair of signals should work."""
        session = ConversationSession()

        # First signal
        assert session.check_interrupt_debounce("cancel_all") is False

        # Expire the window
        session.pending_interrupt_time = (
            time.monotonic() - session.INTERRUPT_DEBOUNCE_WINDOW - 0.1
        )

        # Second signal — too late, becomes new first signal
        assert session.check_interrupt_debounce("cancel_all") is False

        # Third signal — within window of the second, should confirm
        assert session.check_interrupt_debounce("cancel_all") is True

    def test_debounce_resets_after_confirmation(self):
        """After a confirmed barge-in, state should be clean for the next one."""
        session = ConversationSession()

        # Confirm first barge-in
        session.check_interrupt_debounce("cancel_all")
        session.check_interrupt_debounce("cancel_all")

        # State is clean
        assert session.pending_interrupt_time == 0.0

        # Next single signal should just be pending
        assert session.check_interrupt_debounce("cancel_all") is False
        assert session.pending_interrupt_time > 0

    def test_cross_source_signals_confirm(self):
        """iOS interrupt + Gemini VAD should cross-validate and confirm."""
        session = ConversationSession()

        # iOS sends cancel_all
        assert session.check_interrupt_debounce("cancel_all") is False

        # Gemini VAD also detects speech — same mode, within window
        assert session.check_interrupt_debounce("cancel_all") is True

    def test_default_debounce_window_is_800ms(self):
        """The default window should be 0.8 seconds."""
        session = ConversationSession()
        assert session.INTERRUPT_DEBOUNCE_WINDOW == 0.8
