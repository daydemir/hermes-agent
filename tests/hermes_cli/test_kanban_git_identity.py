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
    kb.init_db()
    return home


def test_create_task_persists_deniz_git_identity(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(
            conn,
            title="identity card",
            actor_slug="deniz",
            git_author={"name": "Deniz Aydemir", "email": "deniz@example.test"},
            git_account="deniz:github",
            committer_mode="actor",
            identity_source="test",
        )
        task = kb.get_task(conn, tid)
        identity = kb.resolve_task_git_identity(conn, tid)

    assert task is not None
    assert task.actor_slug == "deniz"
    assert task.git_author == {"name": "Deniz Aydemir", "email": "deniz@example.test"}
    assert task.git_account == "deniz:github"
    assert identity.as_dict() == {
        "status": "resolved",
        "actor_slug": "deniz",
        "git_author": {"name": "Deniz Aydemir", "email": "deniz@example.test"},
        "git_account": "deniz:github",
        "committer_mode": "actor",
        "identity_source": "test",
        "error": None,
    }


def test_create_task_persists_arman_reference_with_safe_author_default(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="arman card", actor_slug="arman")
        identity = kb.resolve_task_git_identity(conn, tid)

    assert identity.status == "resolved"
    assert identity.actor_slug == "arman"
    assert identity.git_author == {
        "name": "Arman",
        "email": "arman@users.noreply.rolly.local",
    }
    assert identity.committer_mode == "agent"


def test_legacy_task_without_identity_resolves_legacy_default(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="legacy card")
        identity = kb.resolve_task_git_identity(conn, tid)

    assert identity.status == "legacy_default"
    assert identity.actor_slug is None
    assert identity.git_author is None
    assert identity.error is None


def test_unknown_identity_is_rejected_at_create_time(kanban_home):
    with kb.connect() as conn:
        with pytest.raises(ValueError, match="actor_slug must be one of"):
            kb.create_task(conn, title="bad", actor_slug="someone")


def test_unknown_identity_on_legacy_row_resolves_error_state(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="legacy bad")
        conn.execute("UPDATE tasks SET actor_slug = ? WHERE id = ?", ("someone", tid))
        task = kb.get_task(conn, tid)
        assert task is not None
        identity = kb.resolve_task_git_identity(conn, task)

    assert identity.status == "error"
    assert identity.actor_slug == "someone"
    assert "unknown actor_slug" in (identity.error or "")


def test_git_account_must_be_owned_by_actor(kanban_home):
    with kb.connect() as conn:
        with pytest.raises(ValueError, match="git_account must be owned"):
            kb.create_task(
                conn,
                title="unsafe account",
                actor_slug="deniz",
                git_account="arman:github",
            )
