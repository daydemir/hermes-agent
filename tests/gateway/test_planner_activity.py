from types import SimpleNamespace

import asyncio
import pytest

from gateway.planner_activity import (
    ActivityState,
    LOADING_MESSAGES,
    PLANNER_SKILL,
    PlannerActivityRegistry,
    PlannerOwner,
    apply_planner_directive,
    planner_activity,
)
from gateway.platforms.base import Platform
from tools.process_registry import ProcessRegistry, ProcessSession


def event(
    *, platform=Platform.SLACK, chat_type="group", channel="C123", thread="123.456",
    skills=None, internal=False, is_bot=False, user_id="U123",
):
    return SimpleNamespace(
        source=SimpleNamespace(
            platform=platform, chat_type=chat_type, chat_id=channel, thread_id=thread,
            is_bot=is_bot, user_id=user_id,
        ),
        auto_skill=skills if skills is not None else [PLANNER_SKILL],
        internal=internal,
    )


@pytest.mark.parametrize(
    "candidate",
    [
        event(platform=Platform.TELEGRAM),
        event(chat_type="dm"),
        event(thread=None),
        event(thread="top-level"),
        event(thread="123"),
        event(channel=""),
        event(skills=[]),
        event(skills=["another-skill"]),
        event(internal=True),
        event(is_bot=True),
        event(user_id=None),
    ],
)
def test_planner_classification_fails_closed(candidate):
    assert PlannerActivityRegistry().acquire(candidate, "S") is None


def test_planner_classification_and_payload():
    owner = PlannerActivityRegistry().acquire(event(), "S")
    assert owner == PlannerOwner("S", "C123", "123.456", 1)
    assert 2 <= len(LOADING_MESSAGES) <= 10
    assert all(message.strip() for message in LOADING_MESSAGES)
    forbidden = ("phase", "worker", "task", "%", "eta", "complete")
    assert not any(word in message.lower() for word in forbidden for message in LOADING_MESSAGES)


def test_wait_rejects_internal_completion_and_human_resumes_same_generation():
    registry = PlannerActivityRegistry()
    owner = registry.acquire(event(), "S")
    assert owner
    registry._states[owner] = ActivityState.WAITING
    assert registry.acquire(event(internal=True), "S") is None
    assert registry.acquire(event(), "S") == owner
    assert registry.is_active(owner)


def test_terminal_only_new_human_generation_supersedes():
    registry = PlannerActivityRegistry()
    owner = registry.acquire(event(), "S")
    assert owner
    registry._states[owner] = ActivityState.TERMINAL
    assert registry.acquire(event(internal=True), "S") is None
    assert registry.acquire(event(), "S") == PlannerOwner("S", "C123", "123.456", 2)


@pytest.mark.asyncio
async def test_release_keeps_generation_tombstone_and_rejects_late_internal_event():
    registry = PlannerActivityRegistry()
    first = registry.acquire(event(), "S")
    assert first
    await registry.release(first)
    second = registry.acquire(event(), "S")
    assert second == PlannerOwner("S", "C123", "123.456", 2)
    assert registry.acquire(event(internal=True), "S") is None
    stale = event(internal=True)
    stale.metadata = {"planner_generation": 1}
    assert registry.acquire(stale, "S") is None


def test_accepted_directive_without_runtime_reports_success():
    planner_activity.__init__()
    assert planner_activity.acquire(event(), "S")
    assert apply_planner_directive(
        kind="wait_for_human", session_key="S", channel="C123",
        thread="123.456", generation="1",
    )


def test_concurrent_threads_are_isolated():
    registry = PlannerActivityRegistry()
    first = registry.acquire(event(thread="123.456"), "S1")
    second = registry.acquire(event(thread="789.012"), "S2")
    assert first and second
    registry._states[first] = ActivityState.WAITING
    assert not registry.is_active(first)
    assert registry.is_active(second)


@pytest.mark.parametrize(
    "field,value",
    [
        ("notify_on_complete", False),
        ("exited", True),
        ("session_key", "other"),
        ("watcher_platform", "telegram"),
        ("watcher_chat_id", "other"),
        ("watcher_thread_id", "other"),
        ("planner_generation", 2),
    ],
)
def test_exact_background_watcher_predicate(field, value):
    registry = ProcessRegistry()
    owner = PlannerOwner("S", "C123", "123.456", 1)
    session = ProcessSession(
        id="p", command="x", notify_on_complete=True, session_key="S",
        watcher_platform="slack", watcher_chat_id="C123", watcher_thread_id="123.456",
        planner_generation=1,
    )
    setattr(session, field, value)
    registry._running[session.id] = session
    assert not registry.has_planner_watcher(owner)
    if field == "notify_on_complete":
        assert registry.has_active_for_session("S")


def test_background_owner_includes_wait_terminal_override(monkeypatch):
    registry = ProcessRegistry()
    owner = PlannerOwner("S", "C123", "123.456", 1)
    registry._running["p"] = ProcessSession(
        id="p", command="x", notify_on_complete=True, session_key="S",
        watcher_platform="slack", watcher_chat_id="C123", watcher_thread_id="123.456",
        planner_generation=1,
    )
    monkeypatch.setattr(planner_activity, "is_active", lambda candidate: candidate == owner)
    assert registry.has_planner_owner(owner)
    monkeypatch.setattr(planner_activity, "is_active", lambda candidate: False)
    assert not registry.has_planner_owner(owner)


@pytest.mark.asyncio
async def test_real_boundary_invalidates_before_bounded_clear():
    class Adapter:
        def __init__(self):
            self.cleared = asyncio.Event()

        async def _stop_typing_refresh(self, chat_id, task, **kwargs):
            task.cancel()
            self.cleared.set()

    planner_activity.__init__()
    owner = planner_activity.acquire(event(), "S")
    assert owner
    adapter = Adapter()
    task = asyncio.create_task(asyncio.Event().wait())
    assert planner_activity.bind_runtime(
        owner, adapter, task, {"thread_id": "123.456", "planner_activity": True}
    )
    applied = await asyncio.to_thread(
        apply_planner_directive,
        kind="wait_for_human",
        session_key="S",
        channel="C123",
        thread="123.456",
        generation="1",
    )
    assert applied
    assert adapter.cleared.is_set()
    assert not planner_activity.is_active(owner)
    assert not apply_planner_directive(
        kind="terminal", session_key="S", channel="C123", thread="123.456", generation="1"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"kind": "bad"},
        {"session_key": "other"},
        {"channel": "C999"},
        {"thread": "789.012"},
        {"generation": "2"},
        {"generation": "bad"},
    ],
)
def test_boundary_rejects_malformed_stale_and_cross_session(kwargs):
    values = {
        "kind": "wait_for_human", "session_key": "S", "channel": "C123",
        "thread": "123.456", "generation": "1",
    }
    values.update(kwargs)
    assert not apply_planner_directive(**values)
