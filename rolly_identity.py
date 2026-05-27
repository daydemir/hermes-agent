"""Rolly's tiny canonical user registry.

Canonical user IDs are human slugs (``deniz``, ``buket``). Platform IDs
(Telegram numeric IDs, future email/phone IDs) are external accounts attached
to a slug. Runtime session storage should use the slug whenever it can be
resolved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home


@dataclass(frozen=True)
class RollyUser:
    slug: str
    display_name: str
    role: str = "user"


REGISTRY_PATH = "rolly-users.json"


def _registry_path() -> Path:
    return get_hermes_home() / REGISTRY_PATH


@lru_cache(maxsize=1)
def _registry() -> dict[str, Any]:
    path = _registry_path()
    if not path.exists():
        return {"users": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    users = data.get("users", []) if isinstance(data, dict) else []
    return {"users": users if isinstance(users, list) else []}


def list_users() -> list[RollyUser]:
    users: list[RollyUser] = []
    for item in _registry()["users"]:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip().lower()
        display_name = str(item.get("display_name") or slug).strip()
        role = str(item.get("role") or "user").strip().lower()
        if slug:
            users.append(RollyUser(slug=slug, display_name=display_name, role=role))
    return users


def known_slugs() -> frozenset[str]:
    return frozenset(user.slug for user in list_users())


def admin_slugs() -> frozenset[str]:
    return frozenset(user.slug for user in list_users() if user.role == "admin")


def normalize_slug(value: Optional[str]) -> Optional[str]:
    slug = str(value or "").strip().lower()
    return slug if slug in known_slugs() else None


def resolve_platform_user(platform: str, platform_user_id: Optional[str]) -> Optional[str]:
    platform = str(platform or "").strip().lower()
    platform_user_id = str(platform_user_id or "").strip()
    if not platform or not platform_user_id:
        return None

    for item in _registry()["users"]:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip().lower()
        if not slug:
            continue
        for account in item.get("accounts", []):
            if not isinstance(account, dict):
                continue
            if (
                str(account.get("platform") or "").strip().lower() == platform
                and str(account.get("user_id") or "").strip() == platform_user_id
            ):
                return slug
    return None


def canonical_user_id(platform: str, raw_user_id: Optional[str]) -> Optional[str]:
    """Return the canonical Rolly slug for a platform user, or the raw id.

    Unknown users keep their raw ID so Hermes' normal pairing/authorization
    behavior remains intact.
    """
    raw = str(raw_user_id or "").strip()
    return resolve_platform_user(platform, raw) or normalize_slug(raw) or (raw or None)
