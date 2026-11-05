"""papercraft_update_scene — apply a validated Scene Patch from a natural-language request (phase 6).

The calling agent translates the user's words ("make it night with cold
moonlight") into ``patch`` operations; this tool validates them against an
allow-list of editable paths and the scene schema, snapshots the previous
scene.json into history/, writes the new one and re-renders the affected
DESIGN_SPEC.md sections. Source images and layer assets are never modified.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import not_implemented, tool_handler
from ..core.pipeline import Runtime
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_update_scene"

SCHEMA = {
    "name": NAME,
    "description": (
        "Modify an existing PaperCraft scene from a natural-language request. Translate the "
        "user's request into 'patch' operations (JSON-Patch style) on scene.json paths such as "
        "/lighting/key/color, /lighting/key/intensity, /lighting/ambient/intensity, "
        "/lighting/rim, /interaction/parallax/strength, /layers/<index>/depth. The patch is "
        "validated against the scene schema before anything is written; the old scene is kept "
        "in history and DESIGN_SPEC.md is updated to match. Assets are never modified."
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
                        "path": {
                            "type": "string",
                            "description": "JSON Pointer into scene.json, e.g. /lighting/key/color",
                        },
                        "value": {"description": "New value for replace/add."},
                    },
                    "required": ["op", "path"],
                    "additionalProperties": False,
                },
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Validate and return the diff without writing anything.",
            },
        },
        "required": ["project_id", "instruction", "patch"],
        "additionalProperties": False,
    },
}


def handle(args: dict[str, Any], runtime: Runtime, **kwargs: Any) -> str:
    return _handle(args, runtime)


@tool_handler(NAME)
def _handle(args: dict[str, Any], runtime: Runtime) -> str:
    open_project_for("update_scene", args, runtime)
    raise not_implemented(NAME, "phase 6: agent iteration")
