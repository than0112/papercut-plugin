"""papercraft_export_project — standalone Vite + Three.js project (phase 7).

The export only copies files (viewer template, scene.json, assets, spec,
README). It never runs npm or any other shell command; the user builds it.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import not_implemented, tool_handler
from ..core.pipeline import Runtime
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_export_project"

SCHEMA = {
    "name": NAME,
    "description": (
        "Export the project as a standalone, deployable web project: Vite + React + Three.js "
        "viewer source, scene.json, all layer assets, DESIGN_SPEC.md, README with install / "
        "build / deploy commands, and the processing log. Uses only relative paths so it runs "
        "on a clean machine with `npm install && npm run build`."
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
    open_project_for("export", args, runtime)
    raise not_implemented(NAME, "phase 7: export")
