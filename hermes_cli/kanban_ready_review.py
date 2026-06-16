"""Ready-review affordances for MIX Kanban cards.

This module is intentionally policy-light: it gives humans one shared view of
whether a card has enough evidence to be moved to ``ready`` and records explicit
approval provenance in the existing append-only event/comment surfaces. The core
transition guard can consume the same event kind without a schema migration.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional

from hermes_cli import kanban_db as kb

APPROVAL_EVENT_KIND = "ready_review_approved"
REQUIRED_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "acceptance_criteria",
        "acceptance criteria",
        (r"acceptance\s+criteria", r"\bacceptance\b", r"\baccept\b"),
    ),
    (
        "verification",
        "verification plan/result",
        (r"verification", r"verify", r"tested", r"tests?\s+(run|pass|passed)"),
    ),
    (
        "source_provenance",
        "source/provenance",
        (r"source", r"provenance", r"citation", r"artifact"),
    ),
)

_AGENT_APPROVER_NAMES = {"agent", "assistant", "default", "rolly", "hermes", "bot", "worker"}
_GENERIC_APPROVER_NAMES = {"human", "reviewer"}


@dataclass(frozen=True)
class ReadyReviewStatus:
    task_id: str
    is_mix_card: bool
    approved: bool
    approver: Optional[str]
    approved_at: Optional[int]
    approval_event_id: Optional[int]
    requirements: dict[str, bool]
    missing: list[str]
    eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_mix_card(task: kb.Task) -> bool:
    """Best-effort marker for the MIX-specific ready gate.

    The Rolly board does not have first-class product/project metadata yet, so
    tenant='mix' is the canonical signal and title/body mentions are a practical
    bridge for existing cards.
    """
    haystack = " ".join(
        part for part in [task.tenant, task.title, task.body] if part
    ).lower()
    return bool(re.search(r"\bmix\b", haystack))


def _metadata_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _review_corpus(
    task: kb.Task,
    comments: Iterable[kb.Comment],
    runs: Iterable[kb.Run],
) -> str:
    parts: list[str] = [task.title or "", task.body or "", task.result or ""]
    parts.extend(c.body or "" for c in comments)
    for run in runs:
        parts.append(run.summary or "")
        parts.append(_metadata_text(run.metadata))
    return "\n".join(p for p in parts if p).lower()


def latest_approval(events: Iterable[kb.Event]) -> tuple[Optional[str], Optional[int], Optional[int]]:
    latest: Optional[kb.Event] = None
    for event in events:
        if event.kind == APPROVAL_EVENT_KIND:
            latest = event
    if latest is None:
        return None, None, None
    payload = latest.payload or {}
    approver = payload.get("approver") or payload.get("reviewer")
    return (str(approver) if approver else None, latest.created_at, latest.id)


def compute_ready_review_status(
    task: kb.Task,
    comments: Iterable[kb.Comment],
    events: Iterable[kb.Event],
    runs: Iterable[kb.Run],
) -> ReadyReviewStatus:
    corpus = _review_corpus(task, comments, runs)
    requirements: dict[str, bool] = {}
    missing: list[str] = []
    for key, label, patterns in REQUIRED_FIELDS:
        present = any(re.search(pattern, corpus, flags=re.IGNORECASE) for pattern in patterns)
        requirements[key] = present
        if not present:
            missing.append(label)

    approver, approved_at, event_id = latest_approval(events)
    approver_key = approver.strip().lower() if approver else ""
    approved = bool(
        approver_key
        and approver_key not in _AGENT_APPROVER_NAMES
        and approver_key not in _GENERIC_APPROVER_NAMES
    )
    if not approved:
        missing.append("explicit human approval")

    return ReadyReviewStatus(
        task_id=task.id,
        is_mix_card=is_mix_card(task),
        approved=approved,
        approver=approver,
        approved_at=approved_at,
        approval_event_id=event_id,
        requirements=requirements,
        missing=missing,
        eligible=not missing,
    )


def status_for_task(conn: sqlite3.Connection, task_id: str) -> ReadyReviewStatus:
    task = kb.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"no such task: {task_id}")
    return compute_ready_review_status(
        task,
        kb.list_comments(conn, task_id),
        kb.list_events(conn, task_id),
        kb.list_runs(conn, task_id),
    )


def record_ready_review_approval(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    approver: str,
    note: Optional[str] = None,
    author: str = "dashboard",
) -> ReadyReviewStatus:
    task = kb.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"no such task: {task_id}")
    approver = (approver or "").strip()
    if not approver:
        raise ValueError("approver is required")
    if approver.lower() in _AGENT_APPROVER_NAMES or approver.lower() in _GENERIC_APPROVER_NAMES:
        raise ValueError("approver must be a specific human reviewer name, not an agent/profile/generic label")

    before = compute_ready_review_status(
        task,
        kb.list_comments(conn, task_id),
        kb.list_events(conn, task_id),
        kb.list_runs(conn, task_id),
    )
    payload = {
        "approver": approver,
        "approved_at": int(time.time()),
        "requirements": before.requirements,
        "missing_before_approval": before.missing,
    }
    comment = f"READY REVIEW APPROVED by {approver}"
    if note:
        comment += f": {note.strip()}"

    with kb.write_txn(conn):
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (task_id, APPROVAL_EVENT_KIND, json.dumps(payload, ensure_ascii=False), payload["approved_at"]),
        )
        conn.execute(
            "INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?, ?, ?, ?)",
            (task_id, author, comment, payload["approved_at"]),
        )

    return status_for_task(conn, task_id)
