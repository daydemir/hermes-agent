"""Per-card git identity resolution for kanban workers.

A kanban card may name a *human actor* (``actor_slug``) — the person whose
git identity the dispatched worker should commit as. This module maps that
slug to a concrete ``GitIdentity`` (name + email + optional github handle)
read from ``rolly-users.json``, and renders the ``GIT_AUTHOR_*`` /
``GIT_COMMITTER_*`` env vars the dispatcher injects at worker spawn.

Security note — this is fail-closed by design:

* ``resolve_git_identity`` returns the requested human's identity or raises
  ``GitIdentityError``. It NEVER substitutes a different human and NEVER
  returns ``None``, so a misconfigured / missing identity blocks the card
  (the dispatcher records a spawn failure) rather than silently committing
  under the ambient/wrong git config.
* A missing or malformed ``rolly-users.json`` yields an empty identity map,
  which makes every resolve fail closed (no identities → no commits).

The default actor for this box is ``deniz`` (its owner); the
``HERMES_KANBAN_DEFAULT_ACTOR`` env var overrides that default at resolve
time when a card names no actor.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional


# This box's owner — the human a card commits as when it names no actor.
# Overridable per-box via the HERMES_KANBAN_DEFAULT_ACTOR env var (read at
# resolve time, not import time, so the override is always honoured).
DEFAULT_ACTOR_SLUG = "deniz"


@dataclass(frozen=True)
class GitIdentity:
    """A human's git authorship identity, sourced from rolly-users.json."""

    slug: str
    name: str
    email: str
    github: Optional[str]


class GitIdentityError(Exception):
    """Raised when an actor cannot be resolved to a git identity.

    Caught by the dispatcher's spawn loop, which records a spawn failure
    (and auto-blocks the card after the failure limit) — so an unresolved
    identity never commits as the wrong human and never crashes the loop.
    """


def load_git_identities() -> dict[str, GitIdentity]:
    """Read all configured git identities from ``rolly-users.json``.

    Returns a ``{slug: GitIdentity}`` map for every user that carries a
    *complete* ``git`` block (both ``name`` and ``email`` present and
    non-empty; ``github`` is optional). Users without a git block — or with
    an incomplete one — are skipped so they fail closed at resolve time.

    A missing, unreadable, or malformed file returns ``{}`` (rather than
    crashing): downstream ``resolve_git_identity`` then fails closed for
    every actor.
    """
    # Lazy import to avoid an import cycle: kanban_db imports this module at
    # module top (for the spawn-time injection), so importing kanban_db here
    # at module top would re-enter a half-initialized kanban_db.
    from hermes_cli import kanban_db

    path = kanban_db.kanban_home() / "rolly-users.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    users = data.get("users")
    if not isinstance(users, list):
        return {}

    identities: dict[str, GitIdentity] = {}
    for user in users:
        if not isinstance(user, dict):
            continue
        slug = user.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        git = user.get("git")
        if not isinstance(git, dict):
            continue
        name = git.get("name")
        email = git.get("email")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(email, str) or not email.strip():
            continue
        github = git.get("github")
        github_value = (
            github.strip()
            if isinstance(github, str) and github.strip()
            else None
        )
        identities[slug] = GitIdentity(
            slug=slug,
            name=name.strip(),
            email=email.strip(),
            github=github_value,
        )
    return identities


def resolve_git_identity(actor_slug: Optional[str]) -> GitIdentity:
    """Resolve a card's actor slug to a concrete git identity.

    Effective actor = the card's ``actor_slug`` if given, else the per-box
    default (``HERMES_KANBAN_DEFAULT_ACTOR`` env override, falling back to
    ``DEFAULT_ACTOR_SLUG``).

    Fail-closed contract: returns the resolved human's identity, or raises
    ``GitIdentityError`` if it isn't configured. Never returns a different
    human and never returns ``None``.
    """
    effective = actor_slug or os.environ.get(
        "HERMES_KANBAN_DEFAULT_ACTOR", DEFAULT_ACTOR_SLUG
    )
    identity = load_git_identities().get(effective)
    if identity is None:
        raise GitIdentityError(
            f"no git identity configured for actor {effective!r}"
        )
    return identity


def git_identity_env(identity: GitIdentity) -> dict[str, str]:
    """Render the GIT_AUTHOR_* / GIT_COMMITTER_* env vars for an identity.

    Author and committer are the same human, so all four vars share the
    identity's name/email. The dispatcher merges this into the worker's
    spawn env, pinning every commit the worker makes to this human.
    """
    return {
        "GIT_AUTHOR_NAME": identity.name,
        "GIT_AUTHOR_EMAIL": identity.email,
        "GIT_COMMITTER_NAME": identity.name,
        "GIT_COMMITTER_EMAIL": identity.email,
    }
