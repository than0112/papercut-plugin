"""papercraft_segment — split the analysed image into transparent depth layers + masks."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from ..core import segmentation as seg
from ..core.errors import ErrorCode, PaperCraftError, ok, tool_handler
from ..core.pipeline import Runtime, set_stage
from ..core.schemas import validate
from ..core.security import validate_image
from ..core.segment_backends import BackendResult, run_builtin, run_http
from ..core.storage import Project, utc_now
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_segment"
OWNER_PATH = "segmentation/owner.png"  # layer index per pixel, 255 = transparent
REVIEW_MAX_SIDE = 1600

SCHEMA = {
    "name": NAME,
    "description": (
        "Step 2. Split the analysed image into the depth layers planned by papercraft_analyze: one "
        "transparent full-size PNG and one mask per layer, plus layers.json. The image is divided into "
        "regions along its cut lines on this machine, and the user's vision model labels the numbered "
        "regions (or a configured segmentation service does). Each layer is extended behind nearer layers "
        "and the hidden part is filled from its own colours, so parallax does not open holes. Writes "
        "review/overlay.png so the user can check the result. To correct a layer, pass manual_masks "
        "(layer id -> mask image path); the previous result is reused and only those pixels change. If "
        "segmentation fails the tool FAILS, unless allow_preview_fallback is true, which produces a flat "
        "'preview' (示意預覽) that is NOT a layered reconstruction and must be reported as such."
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
                    "User-corrected masks: layer id -> path of a PNG/JPEG/WebP with the source image's exact "
                    "size. White (or opaque) pixels are assigned to that layer. Recorded as provenance 'manual'."
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
    project = open_project_for("segment", args, runtime)
    analysis = project.read_json("analysis.json")
    layers = sorted(analysis["layers"], key=lambda layer: layer["order"])
    source = project.path(project.read_json("project.json")["source"]["path"])
    width, height = analysis["measured"]["image"]["width"], analysis["measured"]["image"]["height"]
    allow_preview = args.get("allow_preview_fallback", False)
    if not isinstance(allow_preview, bool):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "allow_preview_fallback must be a boolean.")
    manual = _load_manual_masks(args.get("manual_masks"), layers, (height, width), runtime)

    set_stage(project, "segment", "running")
    try:
        result, reused = _ownership(project, str(source), layers, runtime, reuse=bool(manual))
    except PaperCraftError as exc:
        exc.details.setdefault("project_id", project.id)
        if allow_preview:
            return _write_preview(project, str(source), (width, height), exc)
        set_stage(project, "segment", "failed", error=exc.to_dict())
        raise
    except Exception as exc:
        set_stage(project, "segment", "failed", error={"code": "internal_error", "message": str(exc)})
        raise

    try:
        manifest = _write_layers(project, str(source), layers, result, manual, reused)
    except PaperCraftError as exc:
        exc.details.setdefault("project_id", project.id)
        set_stage(project, "segment", "failed", error=exc.to_dict())
        raise
    set_stage(project, "segment", "succeeded", mode="layered")
    return ok(NAME, _summary(project, manifest), warnings=manifest["warnings"])


# --------------------------------------------------------------------------- inputs


def _load_manual_masks(raw: Any, layers: list[dict], shape: tuple[int, int], runtime: Runtime) -> list[tuple[int, np.ndarray, str]]:
    """Validate user masks up front: (layer index, bool mask, source path), farthest layer first."""
    if raw is None:
        return []
    if not isinstance(raw, dict) or not raw or len(raw) > len(layers):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "manual_masks must map layer ids to mask image paths.")
    index = {layer["id"]: i for i, layer in enumerate(layers)}
    out = []
    for layer_id, path in raw.items():
        if layer_id not in index:
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"manual_masks: unknown layer id {layer_id!r}.",
                                  details={"layers": list(index)})
        if not isinstance(path, str) or not path:
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"manual_masks[{layer_id!r}] must be a file path.")
        validate_image(path, max_bytes=runtime.max_image_bytes)
        with Image.open(path) as img:
            img.load()
            if img.size != (shape[1], shape[0]):
                raise PaperCraftError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"manual_masks[{layer_id!r}] is {img.size[0]}x{img.size[1]}; it must match the source "
                    f"image ({shape[1]}x{shape[0]}).",
                )
            rgba = np.asarray(img.convert("RGBA"))
        alpha = rgba[..., 3]
        mask = alpha >= 128 if (alpha < 255).any() else np.asarray(Image.fromarray(rgba).convert("L")) >= 128
        if not mask.any():
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"manual_masks[{layer_id!r}] selects no pixels.")
        out.append((index[layer_id], mask, path))
    return sorted(out, key=lambda item: item[0])


def _ownership(project: Project, source: str, layers: list[dict], runtime: Runtime,
               *, reuse: bool) -> tuple[BackendResult, bool]:
    """Ownership map from the previous run (manual edit only) or from the configured backend."""
    if reuse and project.exists(OWNER_PATH) and project.exists("layers.json"):
        previous = project.read_json("layers.json")
        if previous.get("mode") == "layered" and [l["id"] for l in previous["layers"]] == [l["id"] for l in layers]:
            owner = np.asarray(Image.open(project.path(OWNER_PATH))).astype(np.int16)
            owner[owner == 255] = -1
            confidence = [l["provenance"]["confidence"] for l in previous["layers"]]
            return BackendResult(owner=owner, confidence=confidence, method=previous["backend"],
                                 meta=dict(previous.get("labelling", {}))), True

    backend = runtime.segmentation_backend
    if backend == "http":
        return run_http(source, layers, runtime.config("segmentation_url", ""),
                        timeout=runtime.segmentation_timeout), False
    if backend != "builtin":
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT,
                              f"Unknown segmentation_backend {backend!r} (use 'builtin' or 'http').")

    def log(*, attempt: int, raw: str, **fields: Any) -> None:
        project.write_text(f"logs/segment_labels_{attempt}.txt", raw or "")
        project.log("segment_label_attempt", attempt=attempt, **fields)

    return run_builtin(source, layers, runtime.analysis_provider(), timeout=runtime.analysis_timeout, log=log), False


# --------------------------------------------------------------------------- outputs


def _clear_outputs(project: Project) -> None:
    for folder in ("assets", "masks"):
        for path in project.path(folder).glob("*.png"):
            path.unlink()


def _review_copy(img: Image.Image) -> Image.Image:
    img = img.copy()
    img.thumbnail((REVIEW_MAX_SIDE, REVIEW_MAX_SIDE), Image.Resampling.LANCZOS)
    return img


def _write_layers(project: Project, source: str, layers: list[dict], result: BackendResult,
                  manual: list[tuple[int, np.ndarray, str]], reused: bool) -> dict[str, Any]:
    rgba = seg.load_rgba(source)
    owner = result.owner.copy()
    warnings = list(result.warnings)
    manual_layers: dict[int, str] = {}
    edits = []
    (project.path("masks/manual")).mkdir(parents=True, exist_ok=True)
    for k, mask, path in manual:
        target = mask & (owner >= 0)
        owner[target] = k
        copy_rel = f"masks/manual/{layers[k]['id']}.png"
        Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), "L").save(project.path(copy_rel))
        manual_layers[k] = copy_rel
        edits.append({"layer": layers[k]["id"], "mask": copy_rel, "pixels": int(target.sum())})
        project.log("manual_mask", layer=layers[k]["id"], source=path, pixels=int(target.sum()))
    if not manual:
        owner = seg.remove_islands(owner, len(layers))

    # Drop layers that ended up empty; keep orders contiguous.
    keep = [k for k in range(len(layers)) if (owner == k).any()]
    for k in range(len(layers)):
        if k not in keep:
            warnings.append(f"layer '{layers[k]['id']}' received no pixels and was dropped")
    if len(keep) < 2:
        raise PaperCraftError(ErrorCode.SEGMENTATION_FAILED,
                              "Segmentation produced fewer than two non-empty layers.", retryable=True)
    if len(keep) < 3:
        warnings.append("only two layers have pixels; the depth effect will be limited")
    remap = np.full(len(layers) + 1, -1, dtype=np.int16)
    for new, old in enumerate(keep):
        remap[old] = new
    owner = remap[owner]  # -1 maps to remap[-1] == -1
    kept = [layers[k] for k in keep]

    maps = seg.build_layers(rgba, owner, len(kept))
    layer_stats = seg.stats(maps)
    long_side = max(owner.shape)
    margin_px = max(2, int(round(seg.FILL_MARGIN * long_side)))

    _clear_outputs(project)
    entries = []
    for new, layer in enumerate(kept):
        old = keep[new]
        Image.fromarray(maps.rgba[new], "RGBA").save(project.path(f"assets/{layer['id']}.png"), optimize=True)
        visible = owner == new
        Image.fromarray(np.where(visible, 255, 0).astype(np.uint8), "L").save(
            project.path(f"masks/{layer['id']}.png"), optimize=True)
        ys, xs = np.nonzero(visible)
        st = layer_stats[new]
        is_manual = old in manual_layers
        provenance = {
            "source": "manual" if is_manual else "auto",
            "method": ("manual mask over " if is_manual else "") + result.method,
            "confidence": 1.0 if is_manual else float(result.confidence[old]),
            "inpainted": bool(maps.filled[new].any()),
            "inpaintedFraction": st["inpaintedFraction"],
        }
        if is_manual:
            provenance["manualMask"] = manual_layers[old]
        entries.append({
            "id": layer["id"], "name": layer["name"], "role": layer["role"], "order": new,
            "asset": f"assets/{layer['id']}.png", "mask": f"masks/{layer['id']}.png",
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "supportBbox": st["bbox"], "coverage": st["coverage"], "supportCoverage": st["supportCoverage"],
            "provenance": provenance,
        })

    stored = np.where(owner >= 0, owner, 255).astype(np.uint8)
    project.path("segmentation").mkdir(exist_ok=True)
    Image.fromarray(stored, "L").save(project.path(OWNER_PATH), optimize=True)
    review = {"overlay": "review/overlay.png"}
    project.path("review").mkdir(exist_ok=True)
    _review_copy(seg.overlay(rgba[..., :3], owner, [l["id"] for l in kept])).save(project.path(review["overlay"]))
    review["layer_map"] = "review/layer_map.png"
    _review_copy(seg.overlay(rgba[..., :3], owner, [l["id"] for l in kept], solid=True)).save(
        project.path(review["layer_map"]))
    for name, img in result.review_images.items():
        review[name] = f"review/{name}.png"
        img.save(project.path(review[name]))
    if result.region_record:
        project.write_json("segmentation/regions.json", result.region_record)

    low = [e["id"] for e in entries if e["provenance"]["confidence"] < 0.7]
    if low:
        warnings.append(f"low segmentation confidence for {', '.join(low)}: check review/overlay.png")
    labelling = {k: v for k, v in result.meta.items() if k in
                 ("provider", "model", "attempts", "regions", "marked", "mergeThreshold", "service", "refined",
                  "refineBatches", "autoFlagged")}
    if reused:
        labelling["reused"] = True
    manifest = {
        "schemaVersion": "1.0",
        "mode": "layered",
        "createdAt": utc_now(),
        "backend": result.method,
        "image": {"width": int(owner.shape[1]), "height": int(owner.shape[0])},
        "layers": entries,
        "fill": {"method": "push-pull interpolation from the layer's own pixels, excluding the cast-shadow band",
                 "marginPx": margin_px, "marginFraction": seg.FILL_MARGIN,
                 "shadowBandPx": max(1, int(round(seg.SHADOW_BAND * long_side)))},
        "labelling": labelling,
        "review": review,
        "warnings": warnings,
    }
    if edits:
        manifest["manualEdits"] = edits
    validate("layers", manifest)
    project.write_json("layers.json", manifest)
    return manifest


def _write_preview(project: Project, source: str, size: tuple[int, int], cause: PaperCraftError) -> str:
    """Degraded mode: one flat layer, clearly labelled. Never presented as a reconstruction."""
    rgba = seg.load_rgba(source)
    _clear_outputs(project)
    Image.fromarray(rgba, "RGBA").save(project.path("assets/preview.png"), optimize=True)
    opaque = rgba[..., 3] >= 128
    Image.fromarray(np.where(opaque, 255, 0).astype(np.uint8), "L").save(project.path("masks/preview.png"))
    ys, xs = np.nonzero(opaque)
    coverage = round(float(opaque.mean()), 5)
    warning = (f"Segmentation failed ({cause.code.value}: {cause.message}). This is a flat PREVIEW "
               "(示意預覽) of the unsegmented image, not a layered 3D reconstruction.")
    manifest = {
        "schemaVersion": "1.0", "mode": "preview", "createdAt": utc_now(), "backend": "preview",
        "image": {"width": size[0], "height": size[1]},
        "layers": [{
            "id": "preview", "name": "Unsegmented preview", "role": "background", "order": 0,
            "asset": "assets/preview.png", "mask": "masks/preview.png",
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "coverage": coverage, "supportCoverage": coverage,
            "provenance": {"source": "preview", "method": "preview:unsegmented", "confidence": 0.0,
                           "inpainted": False, "inpaintedFraction": 0.0},
        }],
        "fill": {"method": "none", "marginPx": 0, "marginFraction": 0.0},
        "warnings": [warning],
    }
    validate("layers", manifest)
    project.write_json("layers.json", manifest)
    set_stage(project, "segment", "degraded", mode="preview", error=cause.to_dict())
    return ok(NAME, {"project_id": project.id, "mode": "preview", "layers_path": str(project.path("layers.json")),
                     "cause": cause.to_dict(), "next_step": "papercraft_build_scene (preview scene)"},
              warnings=[warning])


def _summary(project: Project, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project.id,
        "mode": manifest["mode"],
        "backend": manifest["backend"],
        "layers_path": str(project.path("layers.json")),
        "layers": [
            {"id": e["id"], "order": e["order"], "coverage": e["coverage"],
             "inpainted_fraction": e["provenance"]["inpaintedFraction"],
             "confidence": e["provenance"]["confidence"], "source": e["provenance"]["source"]}
            for e in manifest["layers"]
        ],
        "review_images": {k: str(project.path(v)) for k, v in manifest.get("review", {}).items()},
        "fill_margin_px": manifest["fill"]["marginPx"],
        "labelling": manifest.get("labelling", {}),
        "next_step": "papercraft_build_scene",
        "how_to_correct": "Paint a mask for a wrong layer and call papercraft_segment again with "
                          "manual_masks={layer_id: path}; only those pixels change.",
    }
