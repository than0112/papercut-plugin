"""Workspace and per-project storage.

Generated files never go into the plugin's install tree (``hermes plugins
update`` replaces it). The default workspace is Hermes's per-plugin data
directory; users may point ``workspace_dir`` elsewhere.

Project layout::

    <workspace>/<project_id>/
        source/            original upload (read-only copy, never modified)
        assets/            layer PNGs referenced by scene.json
        masks/             per-layer masks
        analysis.json      papercraft_analyze
        layers.json        papercraft_segment
        scene.json         papercraft_build_scene / papercraft_update_scene
        DESIGN_SPEC.md     papercraft_generate_plan
        history/           scene.json snapshots + applied patches
        logs/process.jsonl processing log (one JSON event per line)
        status.json        stage status (see pipeline.py)
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PaperCraftError
from .security import ImageInfo, require_id, resolve_within, safe_id

PLUGIN_NAME = "papercraft-3d"
PROJECT_SUBDIRS = ("source", "assets", "masks", "history", "logs")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_workspace_root() -> Path:
    """Hermes's per-plugin data dir when running inside Hermes, else a home-dir fallback."""
    try:
        from plugins.plugin_storage import plugin_data_dir  # provided by Hermes

        return Path(plugin_data_dir(PLUGIN_NAME)) / "projects"
    except ImportError:
        home = os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")
        return Path(home) / "plugin-data" / PLUGIN_NAME / "projects"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Workspace:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root else default_workspace_root()

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root.resolve()

    def create_project(self, source: ImageInfo, name: str | None = None) -> "Project":
        root = self.ensure()
        stem = safe_id(name or Path(source.path).stem, fallback="scene")
        project_id = f"{stem[:48]}-{uuid.uuid4().hex[:8]}"
        project = Project(root, project_id)
        for sub in PROJECT_SUBDIRS:
            (project.dir / sub).mkdir(parents=True, exist_ok=True)
        ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[source.format]
        dest = project.path(f"source/source{ext}")
        shutil.copyfile(source.path, dest)
        dest.chmod(0o444)  # the original must never be modified by later stages
        project.write_json(
            "project.json",
            {
                "projectId": project_id,
                "name": name or Path(source.path).stem,
                "createdAt": utc_now(),
                "source": {**source.to_dict(), "path": f"source/source{ext}"},
            },
        )
        project.log("project_created", source=source.to_dict())
        return project

    def open_project(self, project_id: str) -> "Project":
        require_id(project_id, "project_id")
        root = self.ensure()
        project = Project(root, project_id)
        if not (project.dir / "project.json").is_file():
            raise PaperCraftError(
                ErrorCode.PROJECT_NOT_FOUND,
                f"No PaperCraft project '{project_id}' in the workspace.",
                details={"project_id": project_id, "workspace": str(root)},
            )
        return project

    def list_projects(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / "project.json").is_file())


class Project:
    def __init__(self, workspace_root: Path, project_id: str) -> None:
        self.id = project_id
        self.dir = resolve_within(workspace_root, project_id)

    def path(self, relative: str) -> Path:
        """Resolve a project-relative path, refusing anything that escapes the project."""
        return resolve_within(self.dir, relative)

    def exists(self, relative: str) -> bool:
        return self.path(relative).exists()

    def read_json(self, relative: str) -> Any:
        p = self.path(relative)
        if not p.is_file():
            raise PaperCraftError(
                ErrorCode.ASSET_MISSING, f"{relative} does not exist in project {self.id}."
            )
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)

    def write_json(self, relative: str, data: Any) -> Path:
        p = self.path(relative)
        atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return p

    def write_text(self, relative: str, text: str) -> Path:
        p = self.path(relative)
        atomic_write_text(p, text)
        return p

    def log(self, event: str, **fields: Any) -> None:
        line = json.dumps({"at": utc_now(), "event": event, **fields}, ensure_ascii=False, default=str)
        p = self.path("logs/process.jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
