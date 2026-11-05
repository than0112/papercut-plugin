"""papercraft_segment — split the image into transparent depth layers + masks (phase 3)."""

from __future__ import annotations

from typing import Any

from ..core.errors import not_implemented, tool_handler
from ..core.pipeline import Runtime
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_segment"

SCHEMA = {
    "name": NAME,
    "description": (
        "Step 2. Split the analysed image into 3-8 transparent PNG layers (sky, background, "
        "midground_01.., foreground) with one mask per layer, preserving the original aspect "
        "ratio, and write layers.json. Areas revealed behind removed foreground objects are "
        "inpainted or the viewing angle is limited so they are never exposed. If segmentation "
        "is unavailable the tool FAILS, unless allow_preview_fallback is true, in which case it "
        "produces a clearly labelled flat 'preview' (示意預覽) that is NOT a layered reconstruction."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": PROJECT_ID_PROPERTY,
            "allow_preview_fallback": {
                "type": "boolean",
                "default": False,
                "description": "Permit the degraded flat preview mode if real segmentation fails.",
            },
            "manual_masks": {
                "type": "object",
                "description": (
                    "Optional user-corrected masks: layer id -> path of a grayscale/alpha PNG mask "
                    "with the source image's dimensions. Recorded as provenance 'manual'."
                ),
                "additionalProperties": {"type": "string"},
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
    open_project_for("segment", args, runtime)
    raise not_implemented(NAME, "phase 3: layer conversion")
