"""papercraft_analyze — vision analysis of a papercraft image into analysis.json (phase 2)."""

from __future__ import annotations

from typing import Any

from ..core.errors import ErrorCode, PaperCraftError, not_implemented, tool_handler
from ..core.pipeline import Runtime
from ..core.security import validate_image

NAME = "papercraft_analyze"

SCHEMA = {
    "name": NAME,
    "description": (
        "Step 1 of the PaperCraft 3D pipeline. Analyse ONE paper-cut / papercraft style image "
        "(PNG, JPEG or WebP, max 10 MB) and create a PaperCraft project. Produces a structured "
        "analysis.json: subject and style, foreground/midground/background layer plan with "
        "occlusion order, colour palette (HEX), estimated light direction and colour temperature, "
        "a suggested camera, and confidence scores with the items the user should review. "
        "Use when the user gives an image and asks for a 3D / 2.5D / Three.js / parallax scene. "
        "Returns a project_id that every later papercraft_* tool needs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Local path of the uploaded image file.",
            },
            "project_name": {
                "type": "string",
                "description": "Optional human-readable name for the scene/project.",
            },
            "brief": {
                "type": "string",
                "description": (
                    "Optional user requirements to take into account, e.g. 'keep the original "
                    "composition, soft afternoon light, strong mouse parallax'."
                ),
            },
            "max_layers": {
                "type": "integer",
                "minimum": 3,
                "maximum": 8,
                "default": 5,
                "description": "Upper bound on planned depth layers (3-8).",
            },
        },
        "required": ["image_path"],
        "additionalProperties": False,
    },
}


def handle(args: dict[str, Any], runtime: Runtime, **kwargs: Any) -> str:
    return _handle(args, runtime)


@tool_handler(NAME)
def _handle(args: dict[str, Any], runtime: Runtime) -> str:
    image_path = args.get("image_path")
    if not isinstance(image_path, str) or not image_path.strip():
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "image_path is required.")
    max_layers = args.get("max_layers", 5)
    if not isinstance(max_layers, int) or not 3 <= max_layers <= 8:
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "max_layers must be an integer from 3 to 8.")

    info = validate_image(image_path, max_bytes=runtime.max_image_bytes)

    err = not_implemented(NAME, "phase 2: image analysis")
    err.details["validated_image"] = info.to_dict()
    raise err
