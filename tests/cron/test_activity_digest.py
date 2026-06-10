from __future__ import annotations

from datetime import datetime, timezone, timedelta

from cron.activity_digest import CronDigestItem, render_digest, select_recent_jobs


NOW = datetime(2026, 6, 9, 20, 15, tzinfo=timezone.utc)


def test_select_recent_jobs_filters_to_window_and_excludes_self() -> None:
    jobs = [
        {
            "id": "a",
            "name": "Digest Rolly Agent Bridge queue",
            "last_run_at": (NOW - timedelta(minutes=2)).isoformat(),
            "last_status": "ok",
        },
        {
            "id": "b",
            "name": "Rolly cron activity digest",
            "last_run_at": (NOW - timedelta(minutes=1)).isoformat(),
            "last_status": "ok",
        },
        {
            "id": "c",
            "name": "Daily L2 cleanup",
            "last_run_at": (NOW - timedelta(minutes=20)).isoformat(),
            "last_status": "ok",
        },
        {
            "id": "d",
            "name": "Rolly brain auto-push",
            "last_run_at": (NOW - timedelta(minutes=7)).isoformat(),
            "last_status": "error",
            "last_error": "boom",
        },
    ]

    items = select_recent_jobs(
        jobs,
        since=NOW - timedelta(minutes=15),
        until=NOW,
        exclude_names={"Rolly cron activity digest"},
    )

    assert [item.name for item in items] == ["Digest Rolly Agent Bridge queue", "Rolly brain auto-push"]
    assert items[0].success is True
    assert items[1].success is False
    assert items[1].error == "boom"


def test_render_digest_bundles_duplicate_names_and_marks_failures() -> None:
    items = [
        CronDigestItem(
            name="Digest Rolly Agent Bridge queue",
            job_id="a",
            last_run_at=NOW - timedelta(minutes=2),
            success=True,
        ),
        CronDigestItem(
            name="Digest Rolly Agent Bridge queue",
            job_id="a2",
            last_run_at=NOW - timedelta(minutes=3),
            success=True,
        ),
        CronDigestItem(
            name="Daily L2 cleanup",
            job_id="b",
            last_run_at=NOW - timedelta(minutes=1),
            success=False,
            error="TimeoutError: idle",
        ),
    ]

    message = render_digest(items, since=NOW - timedelta(minutes=15), until=NOW)

    assert message is not None
    assert "Rolly cron digest — last 15m" in message
    assert "`Digest Rolly Agent Bridge queue` ×2 ✓" in message
    assert "`Daily L2 cleanup` ⚠: TimeoutError: idle" in message
