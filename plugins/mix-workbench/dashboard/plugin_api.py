"""Mix Workbench dashboard plugin (SUE-75) — read-only browser over the
mix-mono builder artifacts so Deniz can audit visual output without the
Tailscale-only workbench: UI screen captures (refreshed every iOS
pre-push), walk-eval experiment runs, and simulator transcripts.

Mounted at /api/plugins/mix-workbench/ by the dashboard plugin system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

WORKBENCH_ROOT = Path("/Users/rolly/Build/mix/mix-mono/workbench").resolve()

# Read-only browser-asset allowlist; nothing else leaves the workbench.
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/plain",
}

# What surfaces in the browser, deliberately curated rather than a full
# filesystem walk: (group id, label, relative root, glob, cap).
GROUPS = [
    ("ui-screens", "UI screens (latest pre-push capture)",
     "data/runs/prepush-ui-regression/screens", "*.png", 40),
    ("walk-eval", "Walk-eval experiment runs",
     "quality-review/experiments", "**/*.json", 120),
    ("sim-transcripts", "Simulator runs",
     "data/runs", "**/transcript*.json", 40),
]


def _entry(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "relpath": str(path.relative_to(WORKBENCH_ROOT)),
        "mtime": int(stat.st_mtime),
        "size": stat.st_size,
        "kind": "image" if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else "file",
    }


@router.get("/runs")
async def runs() -> dict[str, Any]:
    """The curated artifact groups, newest first within each group."""
    groups = []
    for group_id, label, rel_root, glob, cap in GROUPS:
        root = WORKBENCH_ROOT / rel_root
        files: list[dict[str, Any]] = []
        if root.exists():
            matches = sorted(
                (p for p in root.glob(glob) if p.is_file() and p.suffix.lower() in CONTENT_TYPES),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:cap]
            files = [_entry(p) for p in matches]
        groups.append({"id": group_id, "label": label, "files": files})
    return {"workbenchRoot": str(WORKBENCH_ROOT), "groups": groups}


@router.get("/file")
async def file(path: str) -> FileResponse:
    """Serve one allowlisted workbench file (traversal-safe)."""
    target = (WORKBENCH_ROOT / path).resolve()
    if not target.is_relative_to(WORKBENCH_ROOT):
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    suffix = target.suffix.lower()
    if suffix not in CONTENT_TYPES:
        raise HTTPException(status_code=404, detail="File not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        target,
        media_type=CONTENT_TYPES[suffix],
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )
