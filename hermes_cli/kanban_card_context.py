"""Build deterministic Kanban-card context packets for external coding agents."""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
import os
import re

from hermes_cli import kanban_db


class CardContextError(RuntimeError):
    """Raised when a card cannot be converted into launchable agent context."""


def _trim(text: Optional[str], limit: int) -> str:
    if not text:
        return ""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n…[truncated]"


def _workspace_path(task: kanban_db.Task, *, board: Optional[str] = None) -> Path:
    """Directory a Claude Code session for this card launches in: the board's
    ``default_workdir`` (a real checkout) when set, else the user's home. There
    is no per-card workspace anymore — every card launches in one place."""
    default_workdir = kanban_db.read_board_metadata(
        board if board else kanban_db.get_current_board()
    ).get("default_workdir")
    if default_workdir:
        path = Path(default_workdir).expanduser()
        if not path.is_dir():
            raise CardContextError(
                f"board default_workdir does not exist or is not a directory: {path}"
            )
        return path.resolve()
    return Path.home().resolve()


def build_card_context(task_id: str, *, board: Optional[str] = None) -> dict[str, Any]:
    """Return canonical card data plus a launchable workspace path.

    The launch dir is the board's ``default_workdir`` when set, else the user's
    home — there is no per-card workspace, so every card is launchable.
    """

    kanban_db.init_db(board=board)
    conn = kanban_db.connect(board=board)
    try:
        task = kanban_db.get_task(conn, task_id)
        if task is None:
            raise CardContextError(f"task {task_id} not found")
        workspace = _workspace_path(task, board=board)
        list_criteria = getattr(kanban_db, "list_acceptance_criteria", None)
        criteria = list_criteria(conn, task_id) if callable(list_criteria) else []
        task_dict = asdict(task)
        return {
            "task": task_dict,
            "workspace_path": str(workspace),
            "acceptance_criteria": criteria,
            "latest_summary": kanban_db.latest_summary(conn, task_id),
            "links": {
                "parents": [r["parent_id"] for r in conn.execute("SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id", (task_id,)).fetchall()],
                "children": [r["child_id"] for r in conn.execute("SELECT child_id FROM task_links WHERE parent_id = ? ORDER BY child_id", (task_id,)).fetchall()],
            },
        }
    finally:
        conn.close()


# MIX "soul" + coding principles, injected once at the top of every card-scoped
# Claude Code session. These are the only things Claude Code can't infer from the
# card itself: who MIX is / why the work matters, and Deniz's coding principles.
# Everything else should come from the card. Canonical sources — keep in sync:
#   $ROLLY_BRAIN_ROOT/wiki/mix-product-and-positioning.md
#   $ROLLY_BRAIN_ROOT/wiki/coding-principles.md
_DEFAULT_ROLLY_BRAIN_ROOT = Path(os.getenv("ROLLY_BRAIN_ROOT", "/Users/rolly/rolly-brain"))


def _brain_wiki_path(name: str) -> Path:
    return (_DEFAULT_ROLLY_BRAIN_ROOT / "wiki" / name).expanduser()


@lru_cache(maxsize=None)
def _read_brain_wiki(name: str) -> str:
    path = _brain_wiki_path(name)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _strip_frontmatter(text: str) -> str:
    text = text.strip()
    if not text.startswith("---\n"):
        return text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return text
    return parts[1].strip()


@lru_cache(maxsize=None)
def _mix_soul() -> str:
    text = _strip_frontmatter(_read_brain_wiki("mix-product-and-positioning.md"))
    if not text:
        return (
            "MIX is a location-aware audio/story platform where walking, direction, dwell time, "
            "and physical place act as the interface. MIX turns walks through real places into "
            "interactive audio stories that know where the listener is."
        )
    match = re.search(
        r"(?ms)^## Current understanding\n\n(.*?)(?:\n## |\Z)",
        text,
    )
    if not match:
        return text
    block = match.group(1).strip()
    selected: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        if line.startswith("- Best concise positioning:") or line.startswith("- MIX is a location-aware") or line.startswith("- Core experience:"):
            selected.append(line[2:].strip())
    if not selected:
        selected = [line[2:].strip() for line in block.splitlines() if line.startswith("-")][:3]
    return "\n".join(selected) if selected else text


@lru_cache(maxsize=None)
def _coding_principles() -> str:
    text = _strip_frontmatter(_read_brain_wiki("coding-principles.md"))
    if not text:
        return (
            "# Coding principles\n\n"
            "- DRY.\n"
            "- Functional.\n"
            "- Lean.\n"
            "- Clean.\n"
            "- Simple AF.\n"
            "- Hard cut.\n"
            "- No fallbacks unless explicitly requested.\n"
            "- Fail fast.\n"
            "- Compile-time type safe.\n"
            "- Minimize global mutable state.\n"
            "- Minimize side effects.\n"
            "- Minimize nil checks.\n"
            "- Minimize force unwraps.\n"
            "- Changes should simplify rather than complicate.\n"
            "- CLI-driven development: core functionality lives in CLIs, interfaces sit on top.\n"
            "- Validate functionality through the CLI.\n"
            "- Avoid loose strings.\n"
            "- Prefer readable minimal code.\n"
            "- Use strict enums with directly associated functions/parameters."
        )
    match = re.search(r"(?ms)^# Coding principles\n\n(.*?)(?:\n## |\Z)", text)
    if not match:
        return text
    block = match.group(1).strip()
    bullets = [line.strip() for line in block.splitlines() if line.strip().startswith("-")]
    if not bullets:
        return text
    return "# Coding principles\n\n" + "\n".join(bullets)


def build_claude_prompt(context: dict[str, Any]) -> str:
    """Build the initial Claude Code prompt for a card-scoped session.

    Deliberately minimal: identity + why (MIX), Deniz's coding principles
    verbatim, then the precise card. We do not re-teach Claude Code how to
    explore, edit, run tests, or hand off — it does that by default, and extra
    boilerplate only dilutes the signal that matters.
    """

    task = context["task"]
    links = context.get("links") or {}
    criteria = context.get("acceptance_criteria") or []

    lines: list[str] = [
        _mix_soul(),
        "",
        _coding_principles(),
        "",
        f"## Card {task.get('id')} — {task.get('title')}",
        f"Status: {task.get('status')} · Priority: {task.get('priority')} · "
        f"Assignee: {task.get('assignee') or 'none'} · Tenant: {task.get('tenant') or 'none'}",
        f"Workspace: {context.get('workspace_path')}",
        "",
        _trim(task.get("body"), 6000) or "(no card body)",
    ]

    if context.get("latest_summary"):
        lines += ["", "## Latest worker summary", _trim(context.get("latest_summary"), 2000)]

    if criteria:
        lines += ["", "## Acceptance criteria"]
        for item in criteria:
            mark = "x" if item.get("passed") else " "
            evidence = (
                f" — evidence: {_trim(item.get('evidence'), 700)}"
                if item.get("evidence")
                else ""
            )
            lines.append(
                f"- [{mark}] ({item.get('verifier')}) {_trim(item.get('text'), 1000)}{evidence}"
            )

    parents = links.get("parents") or []
    children = links.get("children") or []
    if parents or children:
        lines += [
            "",
            f"Parents: {', '.join(parents) or 'none'} · Children: {', '.join(children) or 'none'}",
        ]

    return "\n".join(lines).strip() + "\n"


def build_claude_card_launch(task_id: str, *, board: Optional[str] = None) -> dict[str, Any]:
    context = build_card_context(task_id, board=board)
    return {
        "task_id": context["task"]["id"],
        "workspace_path": context["workspace_path"],
        "prompt": build_claude_prompt(context),
    }
