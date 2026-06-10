"""Build and publish the batched Rolly cron activity digest.

The cron scheduler records every completed job into
``~/.hermes/cron/activity-events.jsonl``. This module reads the current cron
state, selects jobs that ran in the last 15 minutes, and emits a single
human-readable activity digest.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable, Optional

from cron.jobs import load_jobs
from hermes_cli.rolly_activity import notify_activity
from hermes_constants import get_hermes_home
from hermes_time import now as _hermes_now

_LOOKBACK = timedelta(minutes=15)
_SELF_JOB_NAMES = {"Rolly cron activity digest"}


@dataclass(frozen=True)
class CronDigestItem:
    name: str
    job_id: str
    last_run_at: datetime
    success: bool
    error: str | None = None


def digest_state_path() -> Path:
    return get_hermes_home().resolve() / "cron" / "activity-digest-state.json"


def parse_iso_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_last_digest_at(path: Path | None = None) -> datetime | None:
    state_path = path or digest_state_path()
    try:
        raw = state_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    return parse_iso_datetime(payload.get("last_digest_at"))


def save_last_digest_at(timestamp: datetime, path: Path | None = None) -> None:
    state_path = path or digest_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(".json.tmp")
    payload = {"last_digest_at": timestamp.isoformat()}
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(state_path)


def select_recent_jobs(
    jobs: Iterable[dict],
    *,
    since: datetime,
    until: datetime,
    exclude_names: Iterable[str] = (),
) -> list[CronDigestItem]:
    excluded = {name.strip() for name in exclude_names if str(name).strip()}
    items: list[CronDigestItem] = []
    for job in jobs:
        name = str(job.get("name") or job.get("id") or "cron job").strip()
        if name in excluded:
            continue
        last_run_at = parse_iso_datetime(job.get("last_run_at"))
        if last_run_at is None or last_run_at <= since or last_run_at > until:
            continue
        items.append(
            CronDigestItem(
                name=name,
                job_id=str(job.get("id") or "").strip(),
                last_run_at=last_run_at,
                success=str(job.get("last_status") or "").strip() == "ok",
                error=_pick_error(job),
            )
        )
    items.sort(key=lambda item: (item.last_run_at, item.name), reverse=True)
    return items


def _pick_error(job: dict) -> str | None:
    for key in ("last_error", "last_delivery_error"):
        value = str(job.get(key) or "").strip()
        if value:
            return value
    return None


def render_digest(items: Iterable[CronDigestItem], *, since: datetime, until: datetime) -> str | None:
    entries = list(items)
    if not entries:
        return None

    counts = Counter(item.name for item in entries)
    latest: dict[str, CronDigestItem] = {}
    for item in entries:
        latest.setdefault(item.name, item)

    lines = ["Rolly cron digest — last 15m"]
    for name, item in sorted(latest.items(), key=lambda pair: (pair[1].last_run_at, pair[0]), reverse=True):
        count = counts[name]
        status = "✓" if item.success else "⚠"
        suffix = ""
        if not item.success and item.error:
            suffix = f": {item.error[:180]}"
        repeat = f" ×{count}" if count > 1 else ""
        lines.append(f"- `{name}`{repeat} {status}{suffix}")
    return "\n".join(lines)


def build_digest(now: datetime | None = None, *, state_path: Path | None = None) -> tuple[str | None, datetime]:
    current = now or _hermes_now()
    current = current if current.tzinfo is not None else current.replace(tzinfo=timezone.utc)
    last_digest_at = load_last_digest_at(state_path)
    lookback_start = current - _LOOKBACK
    since = max(last_digest_at, lookback_start) if last_digest_at else lookback_start
    jobs = load_jobs()
    items = select_recent_jobs(jobs, since=since, until=current, exclude_names=_SELF_JOB_NAMES)
    return render_digest(items, since=since, until=current), current


def publish_digest(now: datetime | None = None, *, state_path: Path | None = None) -> bool:
    message, current = build_digest(now=now, state_path=state_path)
    save_last_digest_at(current, path=state_path)
    if not message:
        return False
    notify_activity(message)
    return True


def main() -> int:
    try:
        publish_digest()
    except Exception:
        # The digest is best-effort only. Never let this internal mirror break cron.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
