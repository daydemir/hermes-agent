from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    return home


@pytest.fixture
def conn(kanban_home):
    with kb.connect() as c:
        yield c


COMPLETE_MIX_BODY = """
Goal: prepare the MIX lead card.

Context/provenance:
- Source: /Users/rolly/rolly-brain/wiki/mix/leads.md

Acceptance:
- Lead row contains contact, basis, and next action.

Verification:
- Check the L2 note and kanban card source links.

Human approval:
- Approved by Deniz.
""".strip()


INCOMPLETE_MIX_BODY = """
Goal: prepare the MIX lead card.

Acceptance:
- Lead row exists.
""".strip()


def _ready_guard_failure(err: str | None) -> str:
    assert err is not None
    assert "MIX ready guard" in err
    return err


def _status(conn, task_id: str) -> str:
    task = kb.get_task(conn, task_id)
    assert task is not None
    return task.status


def _ready_guard_blocked_events(conn, task_id: str):
    return conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'ready_guard_blocked' ORDER BY id",
        (task_id,),
    ).fetchall()


def test_recompute_ready_does_not_repeat_passive_ready_guard_events(conn):
    tid = kb.create_task(
        conn,
        title="MIX passive ready guard",
        body="Goal: decide whether this MIX lead is worth pursuing.",
        tenant="mix",
        triage=True,
    )
    assert kb.specify_triage_task(conn, tid)
    assert _status(conn, tid) == "todo"

    assert kb.recompute_ready(conn) == 0
    assert kb.recompute_ready(conn) == 0

    events = _ready_guard_blocked_events(conn, tid)
    reasons = [event["payload"] for event in events]
    assert len(events) <= 1, reasons
    assert _status(conn, tid) == "todo"


def test_explicit_promote_attempt_records_ready_guard_blocked_event(conn):
    tid = kb.create_task(
        conn,
        title="MIX explicit ready guard",
        body="Goal: decide whether this MIX lead is worth pursuing.",
        tenant="mix",
        triage=True,
    )
    assert kb.specify_triage_task(conn, tid)
    assert _status(conn, tid) == "todo"

    before_events = _ready_guard_blocked_events(conn, tid)

    ok, err = kb.promote_task(conn, tid, actor="tester")

    assert ok is False
    _ready_guard_failure(err)
    events = _ready_guard_blocked_events(conn, tid)
    assert len(events) == len(before_events) + 1
    assert "missing acceptance criteria" in events[-1]["payload"]
    assert _status(conn, tid) == "todo"


def test_promote_refuses_mix_card_missing_ready_fields(conn):
    tid = kb.create_task(
        conn,
        title="MIX lead outreach",
        body=INCOMPLETE_MIX_BODY,
        tenant="mix",
        triage=True,
    )
    assert kb.specify_triage_task(conn, tid)
    assert _status(conn, tid) == "todo"

    ok, err = kb.promote_task(conn, tid, actor="tester")

    assert ok is False
    message = _ready_guard_failure(err)
    assert "missing verification" in message
    assert "missing source/provenance" in message
    assert "missing human approval" in message
    assert _status(conn, tid) == "todo"


def test_promote_allows_complete_human_approved_mix_card(conn):
    tid = kb.create_task(
        conn,
        title="MIX lead outreach",
        body=COMPLETE_MIX_BODY,
        tenant="mix",
        triage=True,
    )
    assert kb.specify_triage_task(conn, tid)
    assert _status(conn, tid) == "ready"


def test_recompute_ready_keeps_incomplete_mix_card_in_todo(conn):
    tid = kb.create_task(
        conn,
        title="MIX competitive research",
        body=INCOMPLETE_MIX_BODY,
        tenant="mix",
        triage=True,
    )

    assert kb.specify_triage_task(conn, tid)
    assert _status(conn, tid) == "todo"
    assert kb.recompute_ready(conn) == 0
    assert _status(conn, tid) == "todo"


def test_claim_demotes_direct_sql_ready_mix_card_missing_approval(conn):
    tid = kb.create_task(
        conn,
        title="MIX direct SQL bypass",
        body=COMPLETE_MIX_BODY.replace("Approved by Deniz", "Approval pending"),
        tenant="mix",
        triage=True,
    )
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))

    claimed = kb.claim_task(conn, tid, claimer="test:1")

    assert claimed is None
    assert _status(conn, tid) == "todo"
    event = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'claim_rejected' ORDER BY id DESC LIMIT 1",
        (tid,),
    ).fetchone()
    assert event is not None
    assert "missing human approval" in event["payload"]


@pytest.mark.parametrize("placeholder", ["human", "reviewer"])
def test_generic_approval_placeholders_do_not_satisfy_ready_gate(conn, placeholder):
    tid = kb.create_task(
        conn,
        title="MIX generic approval placeholder",
        body=COMPLETE_MIX_BODY.replace("Approved by Deniz", f"Approved by {placeholder}"),
        tenant="mix",
        triage=True,
    )

    assert kb.specify_triage_task(conn, tid)
    assert _status(conn, tid) == "todo"
    ok, err = kb.promote_task(conn, tid, actor="tester")

    assert ok is False
    assert "missing human approval" in _ready_guard_failure(err)


def test_non_mix_card_ready_flow_is_unchanged(conn):
    tid = kb.create_task(
        conn,
        title="Generic lead outreach",
        body="Acceptance:\n- exists",
        triage=True,
    )

    assert kb.specify_triage_task(conn, tid)
    assert _status(conn, tid) == "ready"
