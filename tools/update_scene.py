"""papercraft_update_scene — apply a validated Scene Patch from a natural-language request.

The calling agent translates the user's words ("make it night with cold
moonlight") into ``patch`` operations; this tool validates them against an
allow-list of editable paths and the scene schema, keeps the safety bounds,
snapshots the previous scene.json into history/, writes the new one, logs the
change and re-renders DESIGN_SPEC.md. Source images, layer assets and masks
are never modified.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.errors import ErrorCode, PaperCraftError, ok, tool_handler
from ..core.pipeline import Runtime, set_stage
from ..core.scene_patch import apply_patch, shifts
from ..core.schemas import validate_scene
from ..core.storage import utc_now
from ._common import PROJECT_ID_PROPERTY, open_project_for
from .generate_plan import SPEC_PATH, write_spec

NAME = "papercraft_update_scene"

SCHEMA = {
    "name": NAME,
    "description": (
        "Modify an existing PaperCraft scene from a natural-language request. Translate the user's request "
        "into 'patch' operations (JSON Patch: op replace/add/remove, path, value) on scene.json. Layers can be "
        "addressed by id: /layers/<layer id>/depth, /layers/<id>/parallaxFactor, /layers/<id>/material/tint. "
        "Lighting: /lighting/key/color, /lighting/key/intensity, /lighting/key/position, /lighting/ambient/intensity, "
        "/lighting/ambient/color, /lighting/rim (add {color, intensity, position} for a back light), "
        "/lighting/background, /lighting/emissiveScale; replace /lighting with {\"$preset\": \"night\"} to switch "
        "preset. Interaction: /interaction/parallax/strength, /interaction/explodedView/spacing. Camera: "
        "/camera/maxPolarAngle and /camera/maxAzimuthAngle (radians), /camera/minZoom, /camera/maxZoom. Colours "
        "are #rrggbb. Assets, masks and the source image cannot be changed. If the edit would let layers shift "
        "past the filled margin, the other of tilt/parallax is lowered and reported. The previous scene is kept "
        "in history (undo with revert_to) and DESIGN_SPEC.md is updated. Use dry_run to preview the diff."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": PROJECT_ID_PROPERTY,
            "instruction": {
                "type": "string",
                "description": "The user's original request, recorded verbatim in the change log.",
            },
            "patch": {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "description": "Ordered operations applied atomically (all or nothing).",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["replace", "add", "remove"]},
                        "path": {"type": "string", "description": "JSON Pointer, e.g. /lighting/key/color"},
                        "value": {"description": "New value for replace/add."},
                    },
                    "required": ["op", "path"],
                    "additionalProperties": False,
                },
            },
            "revert_to": {
                "type": "integer",
                "minimum": 0,
                "description": "Instead of a patch: restore the scene as it was at this revision (as a new revision).",
            },
            "respect_limits": {
                "type": "boolean",
                "default": True,
                "description": "Keep tilt + parallax within the filled margin (default). false accepts visible gaps.",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Validate and return the diff without writing anything.",
            },
        },
        "required": ["project_id", "instruction"],
        "additionalProperties": False,
    },
}


def handle(args: dict[str, Any], runtime: Runtime, **kwargs: Any) -> str:
    return _handle(args, runtime)


def _check_args(args: dict[str, Any]) -> None:
    instruction = args.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2000:
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "instruction must be a non-empty string (max 2000 chars).")
    if ("patch" in args) == ("revert_to" in args):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "Pass exactly one of patch or revert_to.")
    if "revert_to" in args and (isinstance(args["revert_to"], bool) or not isinstance(args["revert_to"], int)):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "revert_to must be a revision number.")
    for flag in ("dry_run", "respect_limits"):
        if flag in args and not isinstance(args[flag], bool):
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"{flag} must be a boolean.")


def _spec_language(project) -> str:
    text = project.path(SPEC_PATH).read_text(encoding="utf-8")
    return "zh-TW" if "設計與實作企劃書" in text.split("\n", 1)[0] else "en"


@tool_handler(NAME)
def _handle(args: dict[str, Any], runtime: Runtime) -> str:
    _check_args(args)
    project = open_project_for("update_scene", args, runtime)
    scene = project.read_json("scene.json")
    revision = scene["generator"].get("revision", 0)

    if "revert_to" in args:
        target = args["revert_to"]
        if target == revision:
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"The scene is already at revision {target}.")
        snapshot = f"history/scene.rev{target}.json"
        if not project.exists(snapshot):
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"No saved scene for revision {target}.",
                                  details={"available": sorted(p.stem.split(".rev")[1] for p in
                                                               project.path("history").glob("scene.rev*.json"))})
        new = project.read_json(snapshot)
        diff, adjustments, resolved, kind = [{"path": "/", "op": "revert", "old": revision, "new": target}], [], [], "revert"
    else:
        new, diff, adjustments, resolved = apply_patch(scene, args["patch"],
                                                       respect_limits=args.get("respect_limits", True))
        kind = "patch"
    new["generator"]["revision"] = revision + 1
    new["generator"]["updatedAt"] = utc_now()
    validate_scene(new, base_dir=project.dir)

    tilt_px, par_px = shifts(new)
    summary = {
        "project_id": project.id,
        "revision": revision + 1,
        "previous_revision": revision,
        "diff": diff,
        "adjustments": adjustments,
        "limits": new.get("limits", {}),
        "shift_px": {"tilt": round(tilt_px, 2), "parallax": round(par_px, 2)},
    }
    if args.get("dry_run"):
        summary["revision"] = None
        summary["dry_run"] = True
        return ok(NAME, summary, warnings=["dry run: nothing was written"])

    set_stage(project, "update_scene", "running")
    project.write_json(f"history/scene.rev{revision}.json", scene)
    project.write_json("scene.json", new)
    entry = {"revision": revision + 1, "at": new["generator"]["updatedAt"], "kind": kind,
             "instruction": args["instruction"], "patch": resolved, "diff": diff, "adjustments": adjustments}
    with project.path("history/patches.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    project.log("scene_updated", revision=revision + 1, kind=kind, paths=[d["path"] for d in diff])

    warnings = list(new.get("warnings", [])[len(scene.get("warnings", [])):])
    if project.exists(SPEC_PATH):
        try:
            spec = write_spec(project, language=_spec_language(project))
            summary["spec_updated"] = spec["path"]
            warnings += spec["warnings"]
        except PaperCraftError as exc:
            warnings.append(f"scene updated, but DESIGN_SPEC.md could not be regenerated: {exc.message}")
            summary["spec_updated"] = False
    else:
        summary["spec_updated"] = False
        warnings.append("no DESIGN_SPEC.md yet; run papercraft_generate_plan to document the scene")
    set_stage(project, "update_scene", "succeeded")
    summary["preview"] = "An open viewer reloads scene.json automatically within a few seconds."
    return ok(NAME, summary, warnings=warnings)
