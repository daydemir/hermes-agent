"""Rolly background-activity → Slack mirror.

A single best-effort helper that posts a one-line note to the Rolly activity
channel (``#rolly-activity``) so the team can see what Rolly does autonomously
in the background — cron jobs, background tasks, etc. — WITHOUT opening the
dashboard, and without it landing in Telegram (card t_a01bd1aa; Deniz: "anything
Rolly does in the bg that's not a Telegram message").

Gated on the ``ROLLY_ACTIVITY_CHANNEL`` env var (a Slack channel id). Unset =
no-op, so this is inert until configured. Delivery is via ``hermes send``
(bot-token Slack, no gateway needed) and fully swallowed on error so mirroring
never breaks the background work it is reporting on.

Any background subsystem can import this and call ``notify_activity("…")``.
"""
from __future__ import annotations

import os
import shutil
import subprocess


def activity_channel() -> str:
    """The configured Slack channel id for background activity, or ""."""
    return os.environ.get("ROLLY_ACTIVITY_CHANNEL", "").strip()


def notify_activity(text: str) -> None:
    """Post ``text`` to the Rolly activity channel. No-op if unconfigured.

    Best-effort: any delivery error is swallowed — a mirror failure must never
    disrupt the background action being reported.
    """
    channel = activity_channel()
    if not channel or not text or not text.strip():
        return
    hermes = shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
    try:
        subprocess.run(
            [hermes, "send", "--to", f"slack:{channel}", "-q", text.strip()],
            timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
