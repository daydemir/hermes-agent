"""Tests for per-card git identity (hermes_cli.kanban_git_identity).

Covers the registry read (``rolly-users.json`` → ``GitIdentity`` map), the
fail-closed resolver (named actor / default actor / unknown actor / actor
with no git block), the env rendering, and the end-to-end persistence +
spawn-time injection so a deniz-actor card commits as Deniz Aydemir.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_git_identity as kgi


# rolly-users.json fixture content: deniz has a complete git block, arman
# does not (mirrors the real registry while Arman's card is pending).
_USERS = {
    "users": [
        {
            "slug": "deniz",
            "display_name": "Deniz",
            "role": "admin",
            "git": {
                "name": "Deniz Aydemir",
                "email": "deniz@aydemir.us",
                "github": "daydemir",
            },
            "accounts": [],
        },
        {
            "slug": "arman",
            "display_name": "Arman",
            "role": "admin",
            "accounts": [],
        },
    ]
}


@pytest.fixture
def home_with_users(tmp_path, monkeypatch):
    """Isolated HERMES_HOME carrying a kanban DB + a rolly-users.json."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_KANBAN_DEFAULT_ACTOR", raising=False)
    (home / "rolly-users.json").write_text(
        json.dumps(_USERS), encoding="utf-8"
    )
    kb.init_db()
    return home


# ---------------------------------------------------------------------------
# load_git_identities
# ---------------------------------------------------------------------------

def test_load_git_identities_reads_deniz(home_with_users):
    ids = kgi.load_git_identities()
    assert "deniz" in ids
    deniz = ids["deniz"]
    assert deniz.name == "Deniz Aydemir"
    assert deniz.email == "deniz@aydemir.us"
    assert deniz.github == "daydemir"
    # arman has no git block — it must be skipped (fails closed at resolve).
    assert "arman" not in ids


def test_load_git_identities_missing_file_returns_empty(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # No rolly-users.json written.
    assert kgi.load_git_identities() == {}


def test_load_git_identities_malformed_file_returns_empty(home_with_users):
    (home_with_users / "rolly-users.json").write_text("{not json", encoding="utf-8")
    assert kgi.load_git_identities() == {}


def test_load_git_identities_skips_incomplete_git_block(home_with_users):
    data = {
        "users": [
            {"slug": "noemail", "git": {"name": "No Email"}},
            {"slug": "noname", "git": {"email": "x@y.z"}},
            {"slug": "blank", "git": {"name": "   ", "email": ""}},
        ]
    }
    (home_with_users / "rolly-users.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    assert kgi.load_git_identities() == {}


# ---------------------------------------------------------------------------
# resolve_git_identity — fail-closed contract
# ---------------------------------------------------------------------------

def test_resolve_named_actor(home_with_users):
    ident = kgi.resolve_git_identity("deniz")
    assert ident.name == "Deniz Aydemir"
    assert ident.email == "deniz@aydemir.us"


def test_resolve_none_uses_default_actor(home_with_users):
    # No actor named → the box default (deniz).
    ident = kgi.resolve_git_identity(None)
    assert ident.slug == "deniz"


def test_resolve_default_actor_env_override(home_with_users, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DEFAULT_ACTOR", "arman")
    # Override points at arman, who has no git block → fail closed.
    with pytest.raises(kgi.GitIdentityError):
        kgi.resolve_git_identity(None)


def test_resolve_unknown_actor_raises(home_with_users):
    with pytest.raises(kgi.GitIdentityError):
        kgi.resolve_git_identity("nobody")


def test_resolve_actor_without_git_block_raises(home_with_users):
    # arman exists in the registry but has no git block.
    with pytest.raises(kgi.GitIdentityError):
        kgi.resolve_git_identity("arman")


# ---------------------------------------------------------------------------
# git_identity_env
# ---------------------------------------------------------------------------

def test_git_identity_env_author_equals_committer():
    ident = kgi.GitIdentity(
        slug="deniz",
        name="Deniz Aydemir",
        email="deniz@aydemir.us",
        github="daydemir",
    )
    env = kgi.git_identity_env(ident)
    assert env == {
        "GIT_AUTHOR_NAME": "Deniz Aydemir",
        "GIT_AUTHOR_EMAIL": "deniz@aydemir.us",
        "GIT_COMMITTER_NAME": "Deniz Aydemir",
        "GIT_COMMITTER_EMAIL": "deniz@aydemir.us",
    }


# ---------------------------------------------------------------------------
# create_task persistence + get_task hydration
# ---------------------------------------------------------------------------

def test_create_task_persists_actor_slug(home_with_users):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="commit as deniz", actor_slug="deniz")
        task = kb.get_task(conn, tid)
    assert task is not None
    assert task.actor_slug == "deniz"


def test_create_task_actor_slug_defaults_none(home_with_users):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="no actor")
        task = kb.get_task(conn, tid)
    assert task is not None
    assert task.actor_slug is None


# ---------------------------------------------------------------------------
# Spawn-time injection
# ---------------------------------------------------------------------------

def test_default_spawn_injects_deniz_git_env(home_with_users, monkeypatch):
    """A deniz-actor task spawns a worker whose GIT_AUTHOR_* is Deniz."""
    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, *args, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    task = kb.Task(
        id="t_deniz",
        title="commit as deniz",
        body=None,
        assignee="teknium",
        status="staged",
        priority=0,
        created_by="user",
        created_at=0,
        started_at=None,
        completed_at=None,
        claim_lock=None,
        claim_expires=None,
        tenant=None,
        actor_slug="deniz",
    )
    kb._default_spawn(task, str(home_with_users / "ws"), board=None)

    env = captured["env"]
    assert env["GIT_AUTHOR_NAME"] == "Deniz Aydemir"
    assert env["GIT_AUTHOR_EMAIL"] == "deniz@aydemir.us"
    assert env["GIT_COMMITTER_NAME"] == "Deniz Aydemir"
    assert env["GIT_COMMITTER_EMAIL"] == "deniz@aydemir.us"


def test_default_spawn_fails_closed_on_unresolved_actor(home_with_users, monkeypatch):
    """An unresolved actor raises (the dispatcher records a spawn failure)
    rather than spawning a worker with the wrong/no identity."""
    def fake_popen(cmd, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("worker must not spawn for an unresolved actor")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    task = kb.Task(
        id="t_nobody",
        title="bad actor",
        body=None,
        assignee="teknium",
        status="staged",
        priority=0,
        created_by="user",
        created_at=0,
        started_at=None,
        completed_at=None,
        claim_lock=None,
        claim_expires=None,
        tenant=None,
        actor_slug="nobody",
    )
    with pytest.raises(kgi.GitIdentityError):
        kb._default_spawn(task, str(home_with_users / "ws"), board=None)
