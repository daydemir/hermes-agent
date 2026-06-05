"""Detached runner for Rolly Voice background tasks.

This process is intentionally separate from the dashboard/FastAPI process so
work queued from a live call can survive browser disconnects, call end, and
best-effort dashboard restarts. It writes task state and transcript events to
HERMES_HOME so the dashboard can rediscover status later.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _voice_transcript_path(call_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", call_id).strip("._") or "unknown-call"
    root = Path(get_hermes_home()) / "voice-transcripts"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.jsonl"


def _voice_session_id(call_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", call_id).strip("._") or "unknown-call"
    return f"dashboard_voice_{safe}"


def _append_transcript(task: dict[str, Any], event_type: str, text: str, metadata: dict[str, Any] | None = None) -> None:
    record = {
        "timestamp": _now(),
        "call_id": task["call_id"],
        "session_id": _voice_session_id(task["call_id"]),
        "user": task.get("user") or "unknown dashboard user",
        "role": "tool",
        "event_type": event_type,
        "text": text,
        "metadata": {
            "task_id": task["task_id"],
            "task_session_id": task["session_id"],
            **(metadata or {}),
        },
    }
    with _voice_transcript_path(task["call_id"]).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _progress(task: dict[str, Any], status: str, message: str, *, result: str | None = None, error: str | None = None) -> None:
    task["status"] = status
    task["updated_at"] = _now()
    if result is not None:
        task["result"] = result
    if error is not None:
        task["error"] = error
    task.setdefault("progress", []).append({"timestamp": task["updated_at"], "event_type": status, "message": message})


def _filter_voice_cli_output(output: str, task: dict[str, Any]) -> str:
    ansi = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    lines: list[str] = []
    for raw in (output or "").splitlines():
        line = ansi.sub("", raw).strip()
        if not line or line.startswith("session_id:"):
            continue
        if re.fullmatch(r"(?:⏭\s*)?Secret entry skipped", line):
            continue
        lines.append(line)
    visible = "\n".join(lines).strip()
    if visible:
        return visible
    raise RuntimeError(
        f"Background Rolly task {task['task_id']} produced no user-safe visible output. "
        f"Task session: {task['session_id']}."
    )


def _prompt(task: dict[str, Any]) -> str:
    return (
        f"Dashboard voice user: {task.get('user') or 'unknown dashboard user'}\n"
        f"Voice call id: {task['call_id']}\n\n"
        "You are handling work delegated from a live Rolly Voice call. Use normal Rolly context/tools. "
        "The user-facing surface is only Rolly; do not mention internal Fast/Regular layers. "
        "This is a durable background task: complete the requested work even if the originating call has ended. "
        "Return a concise useful handoff answer with citations/links when relevant. "
        "If more detail exists, say it is available and can be expanded.\n\n"
        f"User request: {task['request']}"
    )


def run_task(task_file: Path) -> int:
    task = json.loads(task_file.read_text(encoding="utf-8"))
    _progress(task, "running", "Rolly is working in the background.")
    _atomic_write_json(task_file, task)
    _append_transcript(task, "delegation_started", "Rolly background task started.")

    env = os.environ.copy()
    env.setdefault("HERMES_HOME", str(get_hermes_home()))
    timeout_seconds = int(os.getenv("HERMES_VOICE_BACKGROUND_TASK_TIMEOUT", "600"))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "hermes_cli.main", "chat", "-q", _prompt(task), "--source", "dashboard-voice-background", "-Q"],
            cwd=str(PROJECT_ROOT),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[:1200]
            raise RuntimeError(f"Rolly CLI tool failed: {detail}")
        result = _filter_voice_cli_output(proc.stdout or "", task)
        _progress(task, "complete", "Rolly background task completed.", result=result)
        _atomic_write_json(task_file, task)
        _append_transcript(task, "delegation_result", result, {"status": "complete"})
        return 0
    except subprocess.TimeoutExpired as exc:
        message = f"Rolly CLI tool timed out after {timeout_seconds}s"
        _progress(task, "failed", f"Rolly background task failed: {message}", error=message)
        _atomic_write_json(task_file, task)
        _append_transcript(task, "delegation_error", message, {"status": "failed"})
        return 1
    except Exception as exc:
        message = str(exc)[:1200]
        _progress(task, "failed", f"Rolly background task failed: {message}", error=message)
        _atomic_write_json(task_file, task)
        _append_transcript(task, "delegation_error", message, {"status": "failed"})
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a detached Rolly Voice background task")
    parser.add_argument("--task-file", required=True)
    args = parser.parse_args(argv)
    return run_task(Path(args.task_file))


if __name__ == "__main__":
    raise SystemExit(main())
