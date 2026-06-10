#!/usr/bin/env python3
"""Claude Code Stop / Notification hook → one-line status to Slack #mix-builder.

Wired as a GLOBAL hook in ``~/.claude/settings.json`` so EVERY Claude Code
session on this box pings Slack when it finishes generating (Stop) or needs
input (Notification) — so a session running unattended in tmux doesn't go
silent (card t_3b647bf9; Deniz: "notify of all sessions is fine").

The session is identified by its tmux session name (Arman's note: tmux name is
the user-facing handle), falling back to the cwd basename. Best-effort and
fully non-blocking: any error is swallowed so the hook never disrupts Claude
Code. Delivery uses ``hermes send`` (bot-token Slack, no gateway needed).

Usage (from the hook config): ``cc_slack_notify.py {stop|notification}``.
Channel override: ``CC_SLACK_NOTIFY_CHANNEL`` env (default = #mix-builder).

Mention policy (SUE-81, Deniz): routine Stop pings stay untagged, but a
Notification — Claude genuinely needs input / is waiting on a human — tags
@deniz, rate-limited to once per session handle per 30 minutes so an idle
overnight session does not ping him repeatedly.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

# #mix-builder in the suelio workspace; override per-session via env.
DEFAULT_CHANNEL = "C0B9EPEA8R0"

# Resolve `hermes` to an absolute path — a hook's PATH may not include
# ~/.local/bin, so don't rely on it being discoverable by name.
HERMES = shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")


def _tmux_session() -> str | None:
    """The tmux session name this Claude Code process runs under, if any."""
    if not os.environ.get("TMUX"):
        return None
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "#S"],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = out.stdout.strip()
    return name or None

# Deniz's Slack member id (SLACK_ALLOWED_USERS order: deniz, arman).
DENIZ_MENTION = "<@U04JU6XTF1U>"
# A genuinely-waiting session tags Deniz at most once per this window.
MENTION_COOLDOWN_SECONDS = 1800


def _should_mention(handle: str) -> bool:
    """Rate-limit @deniz tags per session handle (idle sessions re-notify)."""
    import time

    state_dir = os.path.expanduser("~/.hermes/state")
    stamp = os.path.join(state_dir, f"cc-slack-mention-{handle}.stamp")
    now = time.time()
    try:
        if now - os.path.getmtime(stamp) < MENTION_COOLDOWN_SECONDS:
            return False
    except OSError:
        pass
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(stamp, "w", encoding="utf-8") as fh:
            fh.write(str(int(now)))
    except OSError:
        pass
    return True


def main() -> None:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        event = json.loads(raw) if raw.strip() else {}
    except ValueError:
        event = {}

    kind = (sys.argv[1] if len(sys.argv) > 1 else event.get("hook_event_name", "")).lower()
    cwd = event.get("cwd") or os.getcwd()
    handle = _tmux_session() or os.path.basename(cwd.rstrip("/")) or "session"

    if "notification" in kind:
        message = (event.get("message") or "needs input").strip()
        mention = f" {DENIZ_MENTION}" if _should_mention(handle) else ""
        text = f"\u2753 `{handle}` needs input: {message}{mention}"
    else:  # stop / finished generating
        text = f"\u2705 `{handle}` finished"

    channel = os.environ.get("CC_SLACK_NOTIFY_CHANNEL", DEFAULT_CHANNEL)
    try:
        subprocess.run(
            [HERMES, "send", "--to", f"slack:{channel}", "-q", text],
            timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # best-effort — never disrupt Claude Code over a notification


if __name__ == "__main__":
    main()
