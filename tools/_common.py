"""Helpers shared by the project-scoped tools."""

from __future__ import annotations

from typing import Any

from ..core.errors import ErrorCode, PaperCraftError
from ..core.pipeline import Runtime, require_prerequisites
from ..core.storage import Project

PROJECT_ID_PROPERTY = {
    "type": "string",
    "description": "The project_id returned by papercraft_analyze.",
}


def open_project_for(stage: str, args: dict[str, Any], runtime: Runtime) -> Project:
    project_id = args.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "project_id is required.")
    project = runtime.workspace.open_project(project_id)
    require_prerequisites(project, stage)
    return project
