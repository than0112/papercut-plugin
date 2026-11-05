"""papercraft_build_scene — compose layers into a validated scene.json (phase 4)."""

from __future__ import annotations

from typing import Any

from ..core.errors import not_implemented, tool_handler
from ..core.pipeline import Runtime
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_build_scene"

SCHEMA = {
    "name": NAME,
    "description": (
        "Step 3. Place each segmented layer at its depth in a Three.js scene and write a "
        "schema-validated scene.json (camera, layers, lighting, materials, interaction). "
        "The PaperCraft viewer renders directly from this file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": PROJECT_ID_PROPERTY,
            "lighting_preset": {
                "type": "string",
                "enum": ["auto", "day", "night"],
                "default": "auto",
                "description": "'auto' derives lighting from the analysed light estimate.",
            },
            "camera": {
                "type": "string",
                "enum": ["orthographic", "perspective"],
                "default": "orthographic",
                "description": "Orthographic preserves the original framing (recommended).",
            },
            "depth_spacing": {
                "type": "number",
                "minimum": 0.05,
                "maximum": 5,
                "default": 0.6,
                "description": "World units between consecutive layers before exploded view.",
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
    open_project_for("build_scene", args, runtime)
    raise not_implemented(NAME, "phase 4: Three.js scene")
