from __future__ import annotations

import pytest


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    (home / "profiles" / "default").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _credential_config():
    return {
        "kanban": {
            "identities": {
                "deniz": {
                    "git_accounts": ["deniz-github"],
                    "git_author_name": "Deniz Aydemir",
                    "git_author_email": "deniz@example.test",
                },
                "arman": {"git_accounts": ["arman-github"]},
            },
            "credential_accounts": {
                "deniz-github": {
                    "provider": "github",
                    "owner": "deniz",
                    "env": "DENIZ_GITHUB_TOKEN",
                },
                "arman-github": {
                    "provider": "github",
                    "owner": "arman",
                    "env": "ARMAN_GITHUB_TOKEN",
                },
            },
        }
    }


def test_resolve_kanban_credentials_requires_explicit_owner_account(monkeypatch):
    from hermes_cli.kanban_credentials import (
        KanbanCredentialError,
        resolve_kanban_credentials,
    )

    monkeypatch.setenv("DENIZ_GITHUB_TOKEN", "deniz-secret")
    monkeypatch.setenv("ARMAN_GITHUB_TOKEN", "arman-secret")

    resolved = resolve_kanban_credentials(
        actor_slug="deniz",
        account_key="deniz-github",
        config=_credential_config(),
    )

    assert resolved is not None
    assert resolved.env["GH_TOKEN"] == "deniz-secret"
    assert resolved.env["GITHUB_TOKEN"] == "deniz-secret"
    assert "deniz-secret" not in repr(resolved.summary)
    assert resolved.summary == {
        "actor_slug": "deniz",
        "account_key": "deniz-github",
        "provider": "github",
        "source": "env:DENIZ_GITHUB_TOKEN",
    }

    with pytest.raises(KanbanCredentialError, match="belongs to actor 'arman'"):
        resolve_kanban_credentials(
            actor_slug="deniz",
            account_key="arman-github",
            config=_credential_config(),
        )

    with pytest.raises(KanbanCredentialError, match="requires both actor_slug and git_account"):
        resolve_kanban_credentials(
            actor_slug="deniz",
            account_key=None,
            config=_credential_config(),
        )


def test_task_stores_only_credential_selectors_not_material(isolated_home, monkeypatch):
    from hermes_cli import kanban_db

    conn = kanban_db.connect()
    task_id = kanban_db.create_task(
        conn,
        title="credential scoped work",
        assignee="default",
        actor_slug="deniz",
        git_account="deniz-github",
    )
    row = conn.execute("SELECT actor_slug, git_account FROM tasks WHERE id = ?", (task_id,)).fetchone()

    assert dict(row) == {"actor_slug": "deniz", "git_account": "deniz-github"}
    assert "secret" not in repr(dict(row)).lower()
    task = kanban_db.get_task(conn, task_id)
    assert task is not None
    assert task.actor_slug == "deniz"
    assert task.git_account == "deniz-github"

    with pytest.raises(ValueError, match="actor_slug and git_account"):
        kanban_db.create_task(
            conn,
            title="half scoped",
            assignee="default",
            actor_slug="deniz",
        )


def test_default_spawn_scopes_selected_credentials_and_clears_ambient(isolated_home, monkeypatch, tmp_path):
    from hermes_cli import kanban_db

    monkeypatch.setattr("hermes_cli.config.load_config", lambda: _credential_config())
    monkeypatch.setenv("DENIZ_GITHUB_TOKEN", "deniz-secret")
    monkeypatch.setenv("GH_TOKEN", "ambient-wrong-user")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-wrong-user")

    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs["env"]
            self.pid = 4242

    monkeypatch.setattr(kanban_db.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(kanban_db, "_resolve_hermes_argv", lambda: ["hermes"])
    monkeypatch.setattr(kanban_db, "_kanban_worker_skill_available", lambda _home: False)

    task = kanban_db.Task(
        id="t_scoped",
        title="scoped",
        body=None,
        assignee="default",
        status="running",
        priority=0,
        created_by=None,
        created_at=1,
        started_at=None,
        completed_at=None,
        workspace_kind="scratch",
        workspace_path=str(tmp_path),
        claim_lock=None,
        claim_expires=None,
        tenant=None,
        actor_slug="deniz",
        git_account="deniz-github",
    )

    pid = kanban_db._default_spawn(task, str(tmp_path))

    assert pid == 4242
    assert captured["env"]["GH_TOKEN"] == "deniz-secret"
    assert captured["env"]["GITHUB_TOKEN"] == "deniz-secret"
    assert captured["env"]["HERMES_KANBAN_ACTOR_SLUG"] == "deniz"
    assert captured["env"]["HERMES_KANBAN_GIT_ACCOUNT"] == "deniz-github"
    assert captured["env"]["GIT_AUTHOR_EMAIL"] == "deniz@example.test"
    assert "ambient-wrong-user" not in {captured["env"]["GH_TOKEN"], captured["env"]["GITHUB_TOKEN"]}


def test_default_spawn_fails_closed_for_unauthorized_account(isolated_home, monkeypatch, tmp_path):
    from hermes_cli import kanban_db

    monkeypatch.setattr("hermes_cli.config.load_config", lambda: _credential_config())
    monkeypatch.setenv("ARMAN_GITHUB_TOKEN", "arman-secret")

    task = kanban_db.Task(
        id="t_bad",
        title="bad",
        body=None,
        assignee="default",
        status="running",
        priority=0,
        created_by=None,
        created_at=1,
        started_at=None,
        completed_at=None,
        workspace_kind="scratch",
        workspace_path=str(tmp_path),
        claim_lock=None,
        claim_expires=None,
        tenant=None,
        actor_slug="deniz",
        git_account="arman-github",
    )

    with pytest.raises(RuntimeError, match="credential resolution failed closed"):
        kanban_db._default_spawn(task, str(tmp_path))
