"""papercraft_export_project — standalone Vite + Three.js project (files only, no npm run here)."""

from __future__ import annotations

from typing import Any

from ..core import exporter
from ..core.errors import ErrorCode, PaperCraftError, ok, tool_handler
from ..core.pipeline import Runtime, set_stage
from ..core.schemas import validate_scene
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_export_project"

SCHEMA = {
    "name": NAME,
    "description": (
        "Export the project as a standalone, deployable web project: the Vite + React + Three.js viewer "
        "source with exact dependency versions (package-lock.json), scene.json and every file it references, "
        "the JSON Schema, DESIGN_SPEC.md, analysis, layer manifest, change log, processing log (absolute paths "
        "removed) and a README with install / build / deploy commands. Uses relative paths only (checked), so "
        "it runs on a clean machine with `npm ci && npm run build`. The plugin only copies files; it never "
        "runs npm or any other command."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": PROJECT_ID_PROPERTY,
            "zip": {
                "type": "boolean",
                "default": True,
                "description": "Also produce a .zip archive of the exported folder.",
            },
        },
        "required": ["project_id"],
        "additionalProperties": False,
    },
}


def handle(args: dict[str, Any], runtime: Runtime, **kwargs: Any) -> str:
    return _handle(args, runtime)


@tool_handler(NAME)
def _handle(args: dict[str, Any], runtime: Runtime) -> str:
    make_zip = args.get("zip", True)
    if not isinstance(make_zip, bool):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "zip must be a boolean.")
    project = open_project_for("export", args, runtime)
    validate_scene(project.read_json("scene.json"), base_dir=project.dir)
    set_stage(project, "export", "running")
    try:
        result = exporter.export(project, make_zip=make_zip)
    except PaperCraftError as exc:
        exc.details.setdefault("project_id", project.id)
        set_stage(project, "export", "failed", error=exc.to_dict())
        raise
    except OSError as exc:
        err = PaperCraftError(ErrorCode.EXPORT_FAILED, f"Writing the export failed: {exc}",
                              details={"project_id": project.id})
        set_stage(project, "export", "failed", error=err.to_dict())
        raise err from exc
    set_stage(project, "export", "succeeded")
    project.log("exported", files=result["files"], bytes=result["bytes"], zip=bool(result["zip"]))
    warnings = []
    if not project.exists("DESIGN_SPEC.md"):
        warnings.append("no DESIGN_SPEC.md yet; run papercraft_generate_plan and export again to include it")
    return ok(NAME, {
        "project_id": project.id,
        "export_dir": str(result["dir"]),
        "zip_path": str(result["zip"]) if result["zip"] else None,
        "files": result["files"],
        "size_mb": round(result["bytes"] / 1_048_576, 2),
        "checks": {"scene_valid": True, "all_assets_present": True, "no_absolute_paths": True},
        "commands": ["npm ci", "npm run dev", "npm run build", "npm run preview"],
        "deploy": "Upload dist/ (after npm run build) to any static host: GitHub Pages, Netlify, Cloudflare Pages.",
    }, warnings=warnings)
