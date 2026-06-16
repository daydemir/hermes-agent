"""Per-card credential isolation helpers for Kanban workers.

Cards may name an actor plus an account selector (for example a GitHub
account).  The selector is not a secret: it is a lookup key into local config
or environment.  This module resolves that selector at dispatch time, injects
only the scoped runtime environment for the worker process, and fails closed on
unknown, unauthorized, or missing credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional


class KanbanCredentialError(RuntimeError):
    """Raised when a per-card credential selector cannot be resolved safely."""


@dataclass(frozen=True)
class ResolvedKanbanCredentials:
    """Credential resolution result safe to mention in logs.

    ``env`` may contain secret values and must never be logged directly.
    ``summary`` contains only non-secret selector metadata.
    """

    env: dict[str, str]
    summary: dict[str, str]


_AMBIENT_GITHUB_TOKEN_VARS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "COPILOT_GITHUB_TOKEN",
)


_GITHUB_PROVIDER_NAMES = {"github", "gh", "git"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _kanban_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return _as_mapping(config.get("kanban"))


def _identity_config(config: Mapping[str, Any], actor_slug: str) -> Mapping[str, Any]:
    identities = _as_mapping(_kanban_config(config).get("identities"))
    return _as_mapping(identities.get(actor_slug))


def _account_config(config: Mapping[str, Any], account_key: str) -> Mapping[str, Any]:
    accounts = _as_mapping(_kanban_config(config).get("credential_accounts"))
    return _as_mapping(accounts.get(account_key))


def _allowed_accounts(identity: Mapping[str, Any], provider: str) -> set[str]:
    allowed: set[str] = set()
    for key in ("credential_accounts", "accounts"):
        value = identity.get(key)
        if isinstance(value, (list, tuple, set)):
            allowed.update(_text(item) for item in value if _text(item))
    provider_key = f"{provider}_accounts"
    value = identity.get(provider_key)
    if isinstance(value, (list, tuple, set)):
        allowed.update(_text(item) for item in value if _text(item))
    return allowed


def _secret_from_account(account: Mapping[str, Any]) -> tuple[str, str]:
    """Return ``(secret, source_label)`` for an account config.

    Secret values are intentionally looked up from environment variables by
    default.  Inline ``token`` is supported for compatibility with isolated test
    homes, but callers still must never log the returned value.
    """

    env_var = _text(account.get("env") or account.get("env_var") or account.get("token_env"))
    if env_var:
        secret = _text(os.environ.get(env_var))
        if not secret:
            raise KanbanCredentialError(
                f"credential env var {env_var!r} is not set for selected account"
            )
        return secret, f"env:{env_var}"

    token = _text(account.get("token") or account.get("access_token"))
    if token:
        return token, "inline-token"

    raise KanbanCredentialError("selected credential account has no env/token source")


def resolve_kanban_credentials(
    *,
    actor_slug: Optional[str],
    account_key: Optional[str],
    config: Mapping[str, Any],
) -> Optional[ResolvedKanbanCredentials]:
    """Resolve explicit per-card credentials and return scoped env updates.

    If neither ``actor_slug`` nor ``account_key`` is set, returns ``None`` and
    leaves legacy tasks alone.  If either is set, both must be present and must
    match a configured identity/account relationship.  There is deliberately no
    fallback to ambient ``GH_TOKEN`` / ``GITHUB_TOKEN`` values or another user's
    account.
    """

    actor = _text(actor_slug)
    account_name = _text(account_key)
    if not actor and not account_name:
        return None
    if not actor or not account_name:
        raise KanbanCredentialError(
            "per-card credential selection requires both actor_slug and git_account"
        )

    identity = _identity_config(config, actor)
    if not identity:
        raise KanbanCredentialError(f"unknown kanban actor_slug {actor!r}")

    account = _account_config(config, account_name)
    if not account:
        raise KanbanCredentialError(f"unknown kanban credential account {account_name!r}")

    provider = _text(account.get("provider") or "github").lower()
    owner = _text(account.get("owner") or account.get("actor_slug"))
    if owner and owner != actor:
        raise KanbanCredentialError(
            f"credential account {account_name!r} belongs to actor {owner!r}, not {actor!r}"
        )

    allowed = _allowed_accounts(identity, provider)
    if allowed and account_name not in allowed:
        raise KanbanCredentialError(
            f"credential account {account_name!r} is not authorized for actor {actor!r}"
        )

    secret, source = _secret_from_account(account)
    env: dict[str, str] = {}

    # Remove ambient GitHub tokens first so a missing/unauthorized selector can
    # never silently reuse another user's token.  The dispatcher applies this
    # only after all checks above pass; errors fail closed before spawning.
    for var in _AMBIENT_GITHUB_TOKEN_VARS:
        env[var] = ""

    if provider in _GITHUB_PROVIDER_NAMES:
        env["GH_TOKEN"] = secret
        env["GITHUB_TOKEN"] = secret
    else:
        env_var = _text(account.get("runtime_env") or account.get("target_env"))
        if not env_var:
            raise KanbanCredentialError(
                f"credential provider {provider!r} requires runtime_env/target_env"
            )
        env[env_var] = secret

    git_name = _text(account.get("git_author_name") or identity.get("git_author_name"))
    git_email = _text(account.get("git_author_email") or identity.get("git_author_email"))
    if git_name:
        env["GIT_AUTHOR_NAME"] = git_name
        env["GIT_COMMITTER_NAME"] = git_name
    if git_email:
        env["GIT_AUTHOR_EMAIL"] = git_email
        env["GIT_COMMITTER_EMAIL"] = git_email

    return ResolvedKanbanCredentials(
        env=env,
        summary={
            "actor_slug": actor,
            "account_key": account_name,
            "provider": provider,
            "source": source,
        },
    )


def apply_kanban_credentials_to_env(
    env: dict[str, str],
    *,
    actor_slug: Optional[str],
    account_key: Optional[str],
    config: Mapping[str, Any],
) -> Optional[dict[str, str]]:
    """Resolve per-card credentials and mutate ``env`` with scoped values."""

    resolved = resolve_kanban_credentials(
        actor_slug=actor_slug,
        account_key=account_key,
        config=config,
    )
    if resolved is None:
        return None
    for key, value in resolved.env.items():
        if value:
            env[key] = value
        else:
            env.pop(key, None)
    return resolved.summary
