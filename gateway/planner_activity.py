"""Exact process-local lifecycle for Rolly planner Slack activity."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any

PLANNER_SKILL = "rolly-bootstrap:rolly-planner"
LOADING_MESSAGES = ("Working…", "Thinking…", "Still working…")
_CHANNEL = re.compile(r"^[A-Z][A-Z0-9]+$")
_THREAD = re.compile(r"^[0-9]+\.[0-9]+$")


class DirectiveKind(StrEnum):
    WAIT_FOR_HUMAN = "wait_for_human"
    TERMINAL = "terminal"


class ActivityState(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    WAITING = "waiting"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class PlannerOwner:
    session_key: str
    channel_id: str
    thread_id: str
    generation: int

    def __post_init__(self) -> None:
        if not self.session_key:
            raise ValueError("planner owner requires a session")
        if not _CHANNEL.fullmatch(self.channel_id):
            raise ValueError("planner owner requires a valid Slack channel")
        if not _THREAD.fullmatch(self.thread_id):
            raise ValueError("planner owner requires a valid Slack thread")
        if self.generation < 1:
            raise ValueError("planner generation must be positive")


@dataclass(slots=True)
class _Runtime:
    adapter: Any
    task: asyncio.Task
    metadata: dict[str, Any]


def _planner_route(event: Any, session_key: str) -> tuple[str, str, str] | None:
    source = getattr(event, "source", None)
    skills = getattr(event, "auto_skill", None) or []
    if isinstance(skills, str):
        skills = [skills]
    platform = getattr(
        getattr(source, "platform", None), "value", getattr(source, "platform", "")
    )
    channel = str(getattr(source, "chat_id", "") or "")
    thread = str(getattr(source, "thread_id", "") or "")
    if (
        str(platform).lower() != "slack"
        or getattr(source, "chat_type", None) != "group"
        or PLANNER_SKILL not in skills
        or not session_key
        or not _CHANNEL.fullmatch(channel)
        or not _THREAD.fullmatch(thread)
    ):
        return None
    return session_key, channel, thread


def is_planner_event(event: Any, session_key: str) -> bool:
    return _planner_route(event, session_key) is not None


class PlannerActivityRegistry:
    """One renewable lease per exact session/thread. Never persisted."""

    def __init__(self) -> None:
        self._owners: dict[tuple[str, str, str], PlannerOwner] = {}
        self._states: dict[PlannerOwner, ActivityState] = {}
        self._runtimes: dict[PlannerOwner, _Runtime] = {}
        self._lock = Lock()

    def acquire(self, event: Any, session_key: str) -> PlannerOwner | None:
        route = _planner_route(event, session_key)
        if route is None:
            return None
        source = getattr(event, "source", None)
        trusted_human = (
            not bool(getattr(event, "internal", False))
            and not bool(getattr(source, "is_bot", False))
            and bool(getattr(source, "user_id", None))
        )
        with self._lock:
            current = self._owners.get(route)
            if current is None:
                if not trusted_human:
                    return None
                owner = PlannerOwner(*route, 1)
                self._owners[route] = owner
                self._states[owner] = ActivityState.ACTIVE
                return owner
            state = self._states[current]
            if not trusted_human and int(
                (getattr(event, "metadata", None) or {}).get("planner_generation", 0)
                or 0
            ) != current.generation:
                return None
            if state is ActivityState.ACTIVE:
                return current
            if state is ActivityState.IDLE:
                if not trusted_human:
                    return None
                owner = PlannerOwner(*route, current.generation + 1)
                self._owners[route] = owner
                self._states[owner] = ActivityState.ACTIVE
                return owner
            if state is ActivityState.WAITING:
                if not trusted_human:
                    return None
                self._states[current] = ActivityState.ACTIVE
                return current
            if not trusted_human:
                return None
            owner = PlannerOwner(*route, current.generation + 1)
            self._owners[route] = owner
            self._states[owner] = ActivityState.ACTIVE
            return owner

    def current(
        self, session_key: str, channel_id: str, thread_id: str
    ) -> PlannerOwner | None:
        with self._lock:
            return self._owners.get((session_key, channel_id, thread_id))

    def is_active(self, owner: PlannerOwner) -> bool:
        with self._lock:
            return self._states.get(owner) is ActivityState.ACTIVE

    def has_runtime(self, owner: PlannerOwner) -> bool:
        with self._lock:
            runtime = self._runtimes.get(owner)
            return bool(runtime and not runtime.task.done())

    def bind_runtime(
        self, owner: PlannerOwner, adapter: Any, task: asyncio.Task, metadata: dict
    ) -> bool:
        with self._lock:
            if self._states.get(owner) is not ActivityState.ACTIVE:
                return False
            runtime = self._runtimes.get(owner)
            if runtime and not runtime.task.done():
                return False
            self._runtimes[owner] = _Runtime(adapter, task, metadata)
            return True

    def consume(
        self, kind: DirectiveKind | str, owner: PlannerOwner
    ) -> tuple[bool, _Runtime | None]:
        try:
            directive = DirectiveKind(kind)
        except (TypeError, ValueError):
            return False, None
        with self._lock:
            if self._owners.get(
                (owner.session_key, owner.channel_id, owner.thread_id)
            ) != owner or self._states.get(owner) is not ActivityState.ACTIVE:
                return False, None
            if directive is DirectiveKind.TERMINAL:
                from tools.process_registry import process_registry

                if process_registry.has_planner_watcher(owner):
                    return False, None
            self._states[owner] = (
                ActivityState.WAITING
                if directive is DirectiveKind.WAIT_FOR_HUMAN
                else ActivityState.TERMINAL
            )
            return True, self._runtimes.pop(owner, None)

    async def stop_runtime(self, owner: PlannerOwner, runtime: _Runtime | None) -> None:
        if runtime is None:
            return
        await runtime.adapter._stop_typing_refresh(
            owner.channel_id,
            runtime.task,
            metadata=runtime.metadata,
            stop_attempts=1,
        )

    async def release(self, owner: PlannerOwner) -> None:
        with self._lock:
            if self._states.get(owner) is not ActivityState.ACTIVE:
                return
            runtime = self._runtimes.pop(owner, None)
            route = (owner.session_key, owner.channel_id, owner.thread_id)
            if self._owners.get(route) == owner:
                self._states[owner] = ActivityState.IDLE
        await self.stop_runtime(owner, runtime)

    def route_active(self, channel_id: str, thread_id: str) -> bool:
        with self._lock:
            return any(
                owner.channel_id == channel_id
                and owner.thread_id == thread_id
                and state is ActivityState.ACTIVE
                for owner, state in self._states.items()
            )

    def route_known(self, channel_id: str, thread_id: str) -> bool:
        with self._lock:
            return any(
                owner.channel_id == channel_id and owner.thread_id == thread_id
                for owner in self._states
            )


planner_activity = PlannerActivityRegistry()


def apply_planner_directive(
    *, kind: str, session_key: str, channel: str, thread: str, generation: str
) -> bool:
    """Trusted synchronous plugin boundary. Invalidate before bounded clear."""
    try:
        owner = PlannerOwner(session_key, channel, thread, int(generation))
    except (TypeError, ValueError):
        return False
    accepted, runtime = planner_activity.consume(kind, owner)
    if not accepted:
        return False
    if runtime is None:
        return True
    try:
        asyncio.run_coroutine_threadsafe(
            planner_activity.stop_runtime(owner, runtime), runtime.task.get_loop()
        ).result(timeout=2)
    except Exception:
        # State is already invalid. Slack's 120-second expiry is cleanup.
        pass
    return True
