"""Tests for pending follow-up extraction in recursive _run_agent calls.

When pending_event is None (Path B: pending comes from interrupt_message),
accessing pending_event.channel_prompt previously raised AttributeError.
This verifies the fix: channel_prompt is captured inside the
`if pending_event is not None:` block and falls back to None otherwise.

Also verifies that internal control interrupt reasons like "Stop requested"
do not get recycled into the pending-user-message follow-up path.
"""

from types import SimpleNamespace

from gateway.run import _is_control_interrupt_message


def _extract_pending_event_metadata(pending_event):
    """Reproduce the fixed metadata capture from gateway/run.py.

    Mirrors the variable-capture pattern used before the recursive
    _run_agent call so we can test both paths without a full runner.
    """
    next_channel_prompt = None
    next_platform_message_id = None
    if pending_event is not None:
        next_channel_prompt = getattr(pending_event, "channel_prompt", None)
        message_id = getattr(pending_event, "message_id", None)
        next_platform_message_id = str(message_id) if message_id else None
    return next_channel_prompt, next_platform_message_id


def _extract_channel_prompt(pending_event):
    return _extract_pending_event_metadata(pending_event)[0]


def _extract_platform_message_id(pending_event):
    return _extract_pending_event_metadata(pending_event)[1]


def _extract_pending_text(interrupted, pending_event, interrupt_message):
    """Reproduce the fixed pending-text selection from gateway/run.py."""
    if interrupted and pending_event is None and interrupt_message:
        if _is_control_interrupt_message(interrupt_message):
            return None
        return interrupt_message
    return None


class TestPendingEventNoneChannelPrompt:
    """Guard against AttributeError when pending_event is None."""

    def test_none_pending_event_returns_none_channel_prompt(self):
        """Path B: pending_event is None — must not raise AttributeError."""
        result = _extract_channel_prompt(None)
        assert result is None

    def test_pending_event_with_channel_prompt_passes_through(self):
        """Path A: pending_event present — channel_prompt is forwarded."""
        event = SimpleNamespace(channel_prompt="You are a helpful bot.")
        result = _extract_channel_prompt(event)
        assert result == "You are a helpful bot."

    def test_pending_event_without_channel_prompt_returns_none(self):
        """Path A: pending_event present but has no channel_prompt attribute."""
        event = SimpleNamespace()
        result = _extract_channel_prompt(event)
        assert result is None

    def test_none_pending_event_returns_none_platform_message_id(self):
        """Path B: no pending_event means no platform id to forward."""
        assert _extract_platform_message_id(None) is None

    def test_pending_event_message_id_passes_through_as_platform_message_id(self):
        """Path A: queued gateway event id is forwarded for DB attribution."""
        event = SimpleNamespace(message_id="1712345678.123456")
        assert _extract_platform_message_id(event) == "1712345678.123456"

    def test_pending_event_without_message_id_returns_none_platform_message_id(self):
        """Path A: no event message id remains harmless."""
        assert _extract_platform_message_id(SimpleNamespace()) is None


class TestControlInterruptMessages:
    """Control interrupt reasons must not become follow-up user input."""

    def test_stop_requested_is_not_treated_as_pending_user_message(self):
        result = _extract_pending_text(True, None, "Stop requested")
        assert result is None

    def test_session_reset_requested_is_not_treated_as_pending_user_message(self):
        result = _extract_pending_text(True, None, "Session reset requested")
        assert result is None

    def test_real_user_interrupt_message_still_requeues(self):
        result = _extract_pending_text(True, None, "actually use postgres instead")
        assert result == "actually use postgres instead"
