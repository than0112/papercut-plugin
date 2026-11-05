"""papercraft_generate_plan — DESIGN_SPEC.md rendered from the real scene.json (phase 5)."""

from __future__ import annotations

from typing import Any

from ..core.errors import not_implemented, tool_handler
from ..core.pipeline import Runtime
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_generate_plan"

SCHEMA = {
    "name": NAME,
    "description": (
        "Step 4. Write DESIGN_SPEC.md for the project: visual concept and HEX palette, scene "
        "graph and layer depths, camera, lighting, materials and rendering, interactions, and "
        "the technical implementation (packages, file structure, build and deploy commands, "
        "known limitations). Every number in the spec is read from scene.json, so the document "
        "always matches the code."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": PROJECT_ID_PROPERTY,
            "language": {
                "type": "string",
                "enum": ["zh-TW", "en"],
                "default": "zh-TW",
                "description": "Language of the prose sections.",
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
    open_project_for("generate_plan", args, runtime)
    raise not_implemented(NAME, "phase 5: design specification")
