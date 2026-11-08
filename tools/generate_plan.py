"""papercraft_generate_plan — DESIGN_SPEC.md rendered from the real scene.json."""

from __future__ import annotations

import shutil
from typing import Any

from ..core import spec
from ..core.errors import ErrorCode, PaperCraftError, ok, tool_handler
from ..core.pipeline import Runtime, set_stage
from ..core.storage import Project, utc_now
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_generate_plan"
SPEC_PATH = "DESIGN_SPEC.md"

SCHEMA = {
    "name": NAME,
    "description": (
        "Step 4. Write DESIGN_SPEC.md for the project: A visual concept and HEX palette (with the pixel "
        "check), B scene graph, layer depths, coordinates, camera and safety bounds, C lighting presets and "
        "emitters, D materials, rendering and performance, E interactions, F packages with their real "
        "versions, project files, commands, known limitations. Every number is read from scene.json / "
        "analysis.json / layers.json; the result is re-checked against scene.json before the tool succeeds. "
        "Sections are regenerated in place, so notes the user writes outside them are kept."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": PROJECT_ID_PROPERTY,
            "language": {
                "type": "string",
                "enum": ["zh-TW", "en"],
                "default": "zh-TW",
                "description": "Language of headings and fixed text (analysis prose keeps its own language).",
            },
        },
        "required": ["project_id"],
        "additionalProperties": False,
    },
}


def handle(args: dict[str, Any], runtime: Runtime, **kwargs: Any) -> str:
    return _handle(args, runtime)


def write_spec(project: Project, *, language: str) -> dict[str, Any]:
    """Render, merge into an existing spec (keeping user notes), verify, write. Used by update_scene too."""
    scene = project.read_json("scene.json")
    parts = spec.render(scene, project.read_json("analysis.json"), project.read_json("layers.json"),
                        project.read_json("project.json"), project.dir, language=language,
                        sha=spec.scene_sha256(project.path("scene.json")), generated_at=utc_now())
    existing = project.path(SPEC_PATH).read_text(encoding="utf-8") if project.exists(SPEC_PATH) else None
    markdown, merged = spec.assemble(parts, existing)
    problems = spec.verify_spec(markdown, scene)
    if problems:
        raise PaperCraftError(ErrorCode.INTERNAL, "The generated spec does not match scene.json.",
                              details={"problems": problems}, retryable=False)
    warnings = []
    if existing is not None and not merged:
        stamp = utc_now().replace(":", "").replace("+", "_")
        backup = f"history/DESIGN_SPEC.{stamp}.md"
        shutil.copyfile(project.path(SPEC_PATH), project.path(backup))
        warnings.append(f"the existing DESIGN_SPEC.md had no intact auto sections; it was saved as {backup} "
                        "and a new spec was written")
    project.write_text(SPEC_PATH, markdown)
    project.log("spec_written", language=language, merged=merged, revision=scene["generator"].get("revision", 0))
    return {"path": str(project.path(SPEC_PATH)), "merged": merged, "warnings": warnings,
            "revision": scene["generator"].get("revision", 0), "lines": markdown.count("\n") + 1}


@tool_handler(NAME)
def _handle(args: dict[str, Any], runtime: Runtime) -> str:
    language = args.get("language", "zh-TW")
    if language not in spec.T:
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "language must be zh-TW or en.")
    project = open_project_for("generate_plan", args, runtime)
    set_stage(project, "generate_plan", "running")
    try:
        result = write_spec(project, language=language)
    except PaperCraftError as exc:
        exc.details.setdefault("project_id", project.id)
        set_stage(project, "generate_plan", "failed", error=exc.to_dict())
        raise
    set_stage(project, "generate_plan", "succeeded")
    return ok(NAME, {
        "project_id": project.id,
        "spec_path": result["path"],
        "scene_revision": result["revision"],
        "kept_user_notes": result["merged"],
        "verified_against_scene": True,
        "lines": result["lines"],
        "next_step": "papercraft_update_scene (edit the scene in words) or papercraft_export_project",
    }, warnings=result["warnings"])
