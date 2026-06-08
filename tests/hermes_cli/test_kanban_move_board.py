"""Tests for ``kanban_db.move_task_to_board`` — cross-board task moves.

Boards are independent SQLite DBs (one ``kanban.db`` per board), so a move
is a real cross-file row migration: ``tasks`` / ``task_comments`` /
``task_events`` / ``task_acceptance_criteria`` are carried to the target
(id preserved), ``task_attachments`` are carried with their on-disk blobs
(rows + files follow the card), and the board-local ``task_links`` /
``task_runs`` / ``kanban_notify_subs`` are dropped from the source.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# Ensure the worktree (not a stale global clone) is first on sys.path.
_WORKTREE = Path(__file__).resolve().parents[2]
if str(_WORKTREE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE))

from hermes_cli import kanban_db as kb


@pytest.fixture
def two_boards(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with two named boards: ``board-a`` and ``board-b``."""
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for var in (
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_WORKSPACES_ROOT",
        "HERMES_KANBAN_HOME",
        "HERMES_KANBAN_BOARD",
        "HERMES_KANBAN_ATTACHMENTS_ROOT",
    ):
        monkeypatch.delenv(var, raising=False)
    try:
        import hermes_constants
        hermes_constants._cached_default_hermes_root = None  # type: ignore[attr-defined]
    except Exception:
        pass
    kb._INITIALIZED_PATHS.clear()
    kb.create_board("board-a")
    kb.create_board("board-b")
    return home


def _seed_task_with_history(board: str) -> str:
    """Create one task on ``board`` with a comment, an event, and an
    acceptance criterion. Returns the task id."""
    conn = kb.connect(board=board)
    try:
        tid = kb.create_task(conn, title="ship the thing", assignee="dev")
        kb.add_comment(conn, tid, author="alice", body="looks good")
        now = int(time.time())
        with kb.write_txn(conn):
            conn.execute(
                "INSERT INTO task_events (task_id, kind, payload, created_at) "
                "VALUES (?, 'custom_marker', ?, ?)",
                (tid, '{"k": "v"}', now),
            )
            conn.execute(
                "INSERT INTO task_acceptance_criteria "
                "(id, task_id, position, text, verifier, passed, created_at, updated_at) "
                "VALUES (?, ?, 0, 'must compile', 'Rolly', 0, ?, ?)",
                (f"ac_{tid}", tid, now, now),
            )
    finally:
        conn.close()
    return tid


def test_move_carries_task_comment_event_and_id(two_boards):
    tid = _seed_task_with_history("board-a")

    result = kb.move_task_to_board(tid, "board-b", board="board-a")
    assert result == {"task_id": tid, "from_board": "board-a", "to_board": "board-b"}

    # Gone from the source.
    conn_a = kb.connect(board="board-a")
    try:
        assert kb.get_task(conn_a, tid) is None
    finally:
        conn_a.close()

    # Present on the target with id preserved + its comment + event +
    # acceptance criterion carried over.
    conn_b = kb.connect(board="board-b")
    try:
        task = kb.get_task(conn_b, tid)
        assert task is not None
        assert task.id == tid
        assert task.title == "ship the thing"

        comments = kb.list_comments(conn_b, tid)
        bodies = [c.body for c in comments]
        assert "looks good" in bodies

        events = kb.list_events(conn_b, tid)
        kinds = [e.kind for e in events]
        assert "custom_marker" in kinds

        ac_rows = conn_b.execute(
            "SELECT id, text FROM task_acceptance_criteria WHERE task_id = ?",
            (tid,),
        ).fetchall()
        assert len(ac_rows) == 1
        assert ac_rows[0]["id"] == f"ac_{tid}"
        assert ac_rows[0]["text"] == "must compile"
    finally:
        conn_b.close()


def test_move_drops_links_runs_and_notify_subs(two_boards):
    """Board-local rows (links to sibling cards, runs, notify subs) must be
    deleted from the source and NOT carried. (Attachments are carried — see
    ``test_move_carries_attachments_rows_and_blobs``.)"""
    conn_a = kb.connect(board="board-a")
    try:
        sibling = kb.create_task(conn_a, title="sibling parent", assignee="dev")
        tid = kb.create_task(conn_a, title="moves away", assignee="dev")
        # Link the moving task to a sibling card on board A.
        kb.link_tasks(conn_a, sibling, tid)
        now = int(time.time())
        with kb.write_txn(conn_a):
            conn_a.execute(
                "INSERT INTO task_runs (task_id, status, started_at) "
                "VALUES (?, 'done', ?)",
                (tid, now),
            )
            conn_a.execute(
                "INSERT INTO kanban_notify_subs "
                "(task_id, platform, chat_id, thread_id, created_at) "
                "VALUES (?, 'slack', 'C1', '', ?)",
                (tid, now),
            )
    finally:
        conn_a.close()

    kb.move_task_to_board(tid, "board-b", board="board-a")

    # Source: all board-local rows for the moved task are gone.
    conn_a = kb.connect(board="board-a")
    try:
        assert conn_a.execute(
            "SELECT COUNT(*) FROM task_links WHERE parent_id = ? OR child_id = ?",
            (tid, tid),
        ).fetchone()[0] == 0
        assert conn_a.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_id = ?", (tid,)
        ).fetchone()[0] == 0
        assert conn_a.execute(
            "SELECT COUNT(*) FROM kanban_notify_subs WHERE task_id = ?", (tid,)
        ).fetchone()[0] == 0
        # The sibling card itself stays on board A.
        assert kb.get_task(conn_a, sibling) is not None
    finally:
        conn_a.close()

    # Target: NONE of the dropped tables were carried.
    conn_b = kb.connect(board="board-b")
    try:
        assert kb.get_task(conn_b, tid) is not None
        assert conn_b.execute(
            "SELECT COUNT(*) FROM task_links WHERE parent_id = ? OR child_id = ?",
            (tid, tid),
        ).fetchone()[0] == 0
        assert conn_b.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_id = ?", (tid,)
        ).fetchone()[0] == 0
        assert conn_b.execute(
            "SELECT COUNT(*) FROM kanban_notify_subs WHERE task_id = ?", (tid,)
        ).fetchone()[0] == 0
    finally:
        conn_b.close()


def test_move_carries_attachments_rows_and_blobs(two_boards):
    """Attachments (metadata rows + on-disk blobs) follow the card to the
    target board: gone from the source, present + readable on the target."""
    conn_a = kb.connect(board="board-a")
    try:
        tid = kb.create_task(conn_a, title="has a file", assignee="dev")
    finally:
        conn_a.close()

    # Write a real blob under board-a's per-task attachments dir, then record
    # the metadata row the way the upload handler does.
    src_dir = kb.task_attachments_dir(tid, board="board-a")
    src_dir.mkdir(parents=True, exist_ok=True)
    blob = src_dir / "report.pdf"
    blob.write_bytes(b"%PDF-1.4 hello")
    conn_a = kb.connect(board="board-a")
    try:
        kb.add_attachment(
            conn_a,
            tid,
            filename="report.pdf",
            stored_path=str(blob.resolve()),
            content_type="application/pdf",
            size=blob.stat().st_size,
            uploaded_by="alice",
        )
    finally:
        conn_a.close()

    kb.move_task_to_board(tid, "board-b", board="board-a")

    # Source: row gone + blob no longer under board-a.
    conn_a = kb.connect(board="board-a")
    try:
        assert conn_a.execute(
            "SELECT COUNT(*) FROM task_attachments WHERE task_id = ?", (tid,)
        ).fetchone()[0] == 0
    finally:
        conn_a.close()
    assert not blob.exists()

    # Target: row carried (id reassigned, metadata intact) + blob present and
    # readable under board-b's attachments root.
    conn_b = kb.connect(board="board-b")
    try:
        atts = kb.list_attachments(conn_b, tid)
        assert len(atts) == 1
        att = atts[0]
        assert att.filename == "report.pdf"
        assert att.uploaded_by == "alice"
        assert att.content_type == "application/pdf"
        moved = Path(att.stored_path)
        assert moved.is_file()
        assert moved.read_bytes() == b"%PDF-1.4 hello"
        b_root = kb.attachments_root(board="board-b").resolve()
        assert str(moved.resolve()).startswith(str(b_root))
    finally:
        conn_b.close()


def test_move_carries_attachment_row_when_blob_missing(two_boards):
    """A missing blob on disk must not fail the move — the row still carries."""
    conn_a = kb.connect(board="board-a")
    try:
        tid = kb.create_task(conn_a, title="ghost file", assignee="dev")
        with kb.write_txn(conn_a):
            conn_a.execute(
                "INSERT INTO task_attachments "
                "(task_id, filename, stored_path, size, created_at) "
                "VALUES (?, 'gone.bin', '/nonexistent/gone.bin', 3, ?)",
                (tid, int(time.time())),
            )
    finally:
        conn_a.close()

    # Must not raise even though the blob file does not exist on disk.
    kb.move_task_to_board(tid, "board-b", board="board-a")

    conn_b = kb.connect(board="board-b")
    try:
        atts = kb.list_attachments(conn_b, tid)
        assert len(atts) == 1
        assert atts[0].filename == "gone.bin"
    finally:
        conn_b.close()


def test_move_nonexistent_task_raises(two_boards):
    with pytest.raises(ValueError, match="not found"):
        kb.move_task_to_board("t_doesnotexist", "board-b", board="board-a")


def test_move_to_nonexistent_board_raises(two_boards):
    tid = _seed_task_with_history("board-a")
    with pytest.raises(ValueError, match="does not exist"):
        kb.move_task_to_board(tid, "no-such-board", board="board-a")
    # Task stays put on the source.
    conn_a = kb.connect(board="board-a")
    try:
        assert kb.get_task(conn_a, tid) is not None
    finally:
        conn_a.close()


def test_move_to_same_board_raises(two_boards):
    tid = _seed_task_with_history("board-a")
    with pytest.raises(ValueError, match="same"):
        kb.move_task_to_board(tid, "board-a", board="board-a")


def test_move_id_collision_on_target_raises(two_boards):
    tid = _seed_task_with_history("board-a")
    # Plant a task with the SAME id on board B.
    conn_b = kb.connect(board="board-b")
    try:
        with kb.write_txn(conn_b):
            conn_b.execute(
                "INSERT INTO tasks (id, title, status, created_at) "
                "VALUES (?, 'collision', 'backlog', ?)",
                (tid, int(time.time())),
            )
    finally:
        conn_b.close()

    with pytest.raises(ValueError, match="already exists"):
        kb.move_task_to_board(tid, "board-b", board="board-a")

    # Source still has the original task (rolled back cleanly).
    conn_a = kb.connect(board="board-a")
    try:
        assert kb.get_task(conn_a, tid) is not None
    finally:
        conn_a.close()
