"""papercraft_analyze — vision analysis of a papercraft image into analysis.json."""

from __future__ import annotations

import json
from typing import Any

from ..core.analysis import (
    MAX_MODEL_SIDE,
    PROMPT_VERSION,
    Review,
    build_instructions,
    build_repair_instructions,
    finalize,
    measure_palette,
    prepare_model_image,
    present_colours,
    review_analysis,
)
from ..core.errors import ErrorCode, PaperCraftError, ok, tool_handler
from ..core.pipeline import Runtime, set_stage
from ..core.schemas import validate
from ..core.security import ImageInfo, validate_image
from ..core.storage import Project, utc_now

NAME = "papercraft_analyze"
MAX_ATTEMPTS = 2  # first answer + one repair round with the exact validation errors

SCHEMA = {
    "name": NAME,
    "description": (
        "Step 1 of the PaperCraft 3D pipeline. Analyse ONE paper-cut / papercraft style image "
        "(PNG, JPEG or WebP, max 10 MB) with the user's vision model and create a PaperCraft "
        "project. Writes analysis.json: subject and style, a 3-8 layer depth plan (sky/background "
        "to foreground, occlusion, normalised bounding boxes), HEX palette cross-checked against "
        "colours measured from the pixels, estimated light direction and colour temperature, a "
        "suggested camera, confidences, and needsReview items. Use when the user gives an image and "
        "asks for a 3D / 2.5D / Three.js / parallax scene. Pass image_path for a new image, or "
        "project_id to re-analyse an existing project. Returns the project_id that later "
        "papercraft_* tools need. Show the user the needsReview items before continuing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Local path of the uploaded image file (new project).",
            },
            "project_id": {
                "type": "string",
                "description": "Re-analyse the source image of this existing project instead.",
            },
            "project_name": {
                "type": "string",
                "description": "Optional human-readable name for a new project.",
            },
            "brief": {
                "type": "string",
                "description": (
                    "Optional user requirements, e.g. 'keep the original composition, soft "
                    "afternoon light, strong mouse parallax'. Prose fields follow its language."
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
        "additionalProperties": False,
    },
}


def handle(args: dict[str, Any], runtime: Runtime, **kwargs: Any) -> str:
    return _handle(args, runtime)


def _arguments(args: dict[str, Any]) -> tuple[str | None, str | None, int, str | None, str | None]:
    image_path = args.get("image_path")
    project_id = args.get("project_id")
    if (image_path is None) == (project_id is None):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "Pass exactly one of image_path or project_id.")
    if image_path is not None and (not isinstance(image_path, str) or not image_path.strip()):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "image_path must be a non-empty string.")
    if project_id is not None and not isinstance(project_id, str):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "project_id must be a string.")
    max_layers = args.get("max_layers", 5)
    if isinstance(max_layers, bool) or not isinstance(max_layers, int) or not 3 <= max_layers <= 8:
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "max_layers must be an integer from 3 to 8.")
    brief = args.get("brief")
    if brief is not None and (not isinstance(brief, str) or len(brief) > 4000):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "brief must be a string of at most 4000 characters.")
    name = args.get("project_name")
    if name is not None and (not isinstance(name, str) or len(name) > 200):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "project_name must be a string of at most 200 characters.")
    return image_path, project_id, max_layers, brief, name


def _open(runtime: Runtime, image_path: str | None, project_id: str | None,
          name: str | None) -> tuple[Project, ImageInfo]:
    workspace = runtime.workspace
    if image_path is not None:
        info = validate_image(image_path, max_bytes=runtime.max_image_bytes)
        return workspace.create_project(info, name=name), info
    project = workspace.open_project(project_id)  # type: ignore[arg-type]
    meta = project.read_json("project.json")
    info = validate_image(project.path(meta["source"]["path"]), max_bytes=runtime.max_image_bytes)
    return project, info


@tool_handler(NAME)
def _handle(args: dict[str, Any], runtime: Runtime) -> str:
    image_path, project_id, max_layers, brief, name = _arguments(args)
    project, info = _open(runtime, image_path, project_id, name)
    set_stage(project, "analyze", "running")
    try:
        doc, review = _analyse(project, info, runtime, max_layers=max_layers, brief=brief)
    except PaperCraftError as exc:
        exc.details.setdefault("project_id", project.id)
        set_stage(project, "analyze", "failed", error=exc.to_dict())
        raise
    except Exception as exc:
        set_stage(project, "analyze", "failed", error={"code": "internal_error", "message": str(exc)})
        raise
    set_stage(project, "analyze", "succeeded")
    return ok(NAME, _summary(project, doc), warnings=doc.get("warnings", []))


def _analyse(project: Project, info: ImageInfo, runtime: Runtime, *, max_layers: int,
             brief: str | None) -> tuple[dict[str, Any], Review]:
    source = info.path
    measured = measure_palette(source)
    present = present_colours(source)
    image_bytes, mime, model_size = prepare_model_image(source)
    base = build_instructions(max_layers=max_layers, brief=brief, measured=measured)
    provider = runtime.analysis_provider()

    instructions = base
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = provider.analyze(image_bytes, mime, instructions=instructions, timeout=runtime.analysis_timeout)
        raw = result.raw_text or (json.dumps(result.data, ensure_ascii=False) if result.data is not None else "")
        project.write_text(f"logs/analysis_reply_{attempt}.txt", raw)
        if result.data is None:
            review = Review(errors=["the reply is not a single valid JSON object"])
        else:
            review = review_analysis(result.data, max_layers=max_layers, present=present)
        project.log("analysis_attempt", attempt=attempt, provider=result.provider, model=result.model,
                    accepted=not review.errors, errors=review.errors[:20], usage=result.usage)
        if not review.errors:
            break
        if attempt == MAX_ATTEMPTS:
            raise PaperCraftError(
                ErrorCode.MODEL_OUTPUT_INVALID,
                f"The vision model's analysis was rejected after {attempt} attempts.",
                details={"errors": review.errors[:20], "raw_reply": f"logs/analysis_reply_{attempt}.txt"},
                retryable=True,
            )
        instructions = build_repair_instructions(base, raw, review.errors)

    doc = finalize(result.data, review)
    doc["measured"] = {"image": {"width": info.width, "height": info.height}, "palette": measured}
    doc["meta"] = {
        "provider": result.provider,
        "model": result.model,
        "promptVersion": PROMPT_VERSION,
        "createdAt": utc_now(),
        "attempts": attempt,
        "maxLayers": max_layers,
        "modelImage": {"width": model_size[0], "height": model_size[1], "mimeType": mime},
        "usage": result.usage,
        "paletteCheck": review.palette_check,
        **({"brief": brief} if brief else {}),
    }
    validate("analysis", doc)  # the stored document must satisfy the full schema
    project.write_json("analysis.json", doc)
    return doc, review


def _summary(project: Project, doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project.id,
        "project_dir": str(project.dir),
        "analysis_path": str(project.path("analysis.json")),
        "subject": doc["subject"],
        "style": doc["style"]["description"],
        "layers": [
            {k: layer[k] for k in ("id", "name", "role", "order", "depthEstimate", "confidence")}
            for layer in doc["layers"]
        ],
        "palette": [c["hex"] for c in doc["palette"]],
        "measured_palette": [c["hex"] for c in doc["measured"]["palette"]],
        "palette_check": doc["meta"]["paletteCheck"],
        "light": {k: v for k, v in doc["light"].items() if k != "emitters"},
        "emitters": [
            {k: e[k] for k in ("name", "kind", "layerId", "color") if k in e}
            for e in doc["light"].get("emitters", [])
        ],
        "camera": doc["camera"],
        "reconstruction": doc["reconstruction"]["recommended"],
        "confidence": doc["confidence"],
        "needs_review": doc["needsReview"],
        "model": f"{doc['meta']['provider']}/{doc['meta']['model']}".strip("/"),
        "attempts": doc["meta"]["attempts"],
        "next_step": "papercraft_segment",
        "max_model_side_px": MAX_MODEL_SIDE,
    }
