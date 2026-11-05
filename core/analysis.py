"""Image analysis: prompt, model-facing schema, measured palette and validation.

The vision model's reply is untrusted. It is accepted only after it passes
``schemas/analysis.schema.json`` *and* the semantic checks below. Anything the
model claims that we can measure ourselves (the colour palette) is
cross-checked against the pixels. Mismatches become ``needsReview`` items;
they are never silently "fixed".
"""

from __future__ import annotations

import copy
import io
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from .schemas import load_schema, schema_errors

PROMPT_VERSION = "analysis-v1"
MAX_MODEL_SIDE = 1536  # long side of the copy sent to the model; bboxes are normalised
PALETTE_SIZE = 8
PALETTE_MISMATCH_RGB = 60.0  # Euclidean RGB distance beyond which a claimed colour is "not in the image"

# Fields the plugin fills in itself; the model must not produce them.
_PLUGIN_OWNED = ("meta", "measured")


def model_schema() -> dict[str, Any]:
    """The analysis schema minus plugin-owned fields (what the model is asked to produce)."""
    schema = copy.deepcopy(load_schema("analysis"))
    for key in _PLUGIN_OWNED:
        schema["properties"].pop(key, None)
    schema.pop("$id", None)
    return schema


def build_instructions(*, max_layers: int, brief: str | None) -> str:
    schema_text = json.dumps(model_schema(), ensure_ascii=False, separators=(",", ":"))
    brief_text = brief.strip() if brief and brief.strip() else "(none)"
    return f"""You are analysing ONE image in a paper-cut / papercraft / layered paper-art style, so it
can be rebuilt as a layered 2.5D Three.js scene (transparent cut-out planes stacked in depth).

Produce ONE JSON object that matches the JSON schema at the end. Rules:

LAYERS (most important)
- Plan between 3 and {max_layers} depth layers. Each layer is a group of paper pieces that sit at
  roughly the same depth and can be cut out together. Prefer fewer, cleaner layers over many tiny ones.
- "order": 0 is the farthest layer (sky or back wall), increasing toward the viewer. Orders are unique.
- "depthEstimate": 0 = farthest, 1 = nearest; it must increase with "order".
- "bbox": normalised [x0, y0, x1, y1] of the layer's visible pixels, origin at the TOP-LEFT, x0<x1, y0<y1.
- "description": say precisely which objects belong to the layer (shapes, colours, position) so a
  segmentation step can find them without seeing your reasoning.
- "occludes": ids of FARTHER layers this layer covers.
- "id": lowercase, e.g. sky, background, midground_01, midground_02, foreground.
- Use "geometryHint" = "extrude" only for pieces whose paper thickness is clearly visible.

COLOURS AND LIGHT
- "palette": the main colours actually present, as #RRGGBB, with a role for each.
- "light": where the light comes from, judged from highlights and the drop shadows between paper
  layers. azimuthDeg: 0 = from the viewer, -90 = from the left, +90 = from the right.
  elevationDeg: positive = from above. colorTemperatureK: warm ~3000, neutral ~5500, cool ~8000.

HONESTY
- Give calibrated confidences in [0, 1]. Lower them when the image is ambiguous.
- Put anything a human should check in "needsReview" (field path + reason), e.g. an object whose
  layer is unclear, or light direction you could not infer.
- Do not describe things that are not in the image.

User brief (take it into account, and write prose fields in the brief's language; English if none):
{brief_text}

JSON schema:
{schema_text}"""


def build_repair_instructions(base: str, previous: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems[:25])
    return (
        f"{base}\n\nYour previous answer was rejected. Problems:\n{listed}\n\n"
        f"Previous answer (fix it; return the complete corrected JSON object only):\n{previous[:12000]}"
    )


def prepare_model_image(path: str) -> tuple[bytes, str, tuple[int, int]]:
    """A downscaled copy for the model: PNG if the source has alpha, else JPEG q90."""
    with Image.open(path) as img:
        img.load()
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.thumbnail((MAX_MODEL_SIDE, MAX_MODEL_SIDE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        if has_alpha:
            img.save(buf, "PNG", optimize=True)
            return buf.getvalue(), "image/png", img.size
        img.save(buf, "JPEG", quality=90, optimize=True)
        return buf.getvalue(), "image/jpeg", img.size


def _opaque_pixels(path: str, side: int = 256) -> np.ndarray:
    """(N, 3) uint8 RGB of the opaque pixels of a downscaled copy."""
    with Image.open(path) as img:
        img.load()
        rgba = img.convert("RGBA")
    rgba.thumbnail((side, side), Image.Resampling.BILINEAR)
    arr = np.asarray(rgba).reshape(-1, 4)
    return arr[arr[:, 3] >= 128][:, :3]


def measure_palette(path: str, colors: int = PALETTE_SIZE) -> list[dict[str, Any]]:
    """Dominant colours measured from the pixels (median cut on a 256px copy), most common first."""
    pixels = _opaque_pixels(path)
    if pixels.size == 0:
        return []
    sample = Image.fromarray(pixels.reshape(1, -1, 3), "RGB")
    quant = sample.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    pal = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    out = []
    for count, idx in counts:
        r, g, b = pal[idx * 3: idx * 3 + 3]
        out.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "share": round(count / total, 4)})
    return out


def present_colours(path: str, *, bin_size: int = 16, min_share: float = 0.002) -> np.ndarray:
    """Mean RGB of every coarse colour bin covering >= min_share of the pixels.

    Used to check claimed colours: unlike the 8-colour palette it keeps small
    accents (a sun, a lantern) that median cut merges away.
    """
    pixels = _opaque_pixels(path).astype(np.int64)
    if pixels.size == 0:
        return np.empty((0, 3))
    keys = (pixels // bin_size) @ np.array([1 << 16, 1 << 8, 1])
    uniq, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    sums = np.zeros((uniq.size, 3))
    np.add.at(sums, inverse, pixels)
    means = sums / counts[:, None]
    return means[counts >= max(1, int(min_share * len(pixels)))]


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@dataclass
class Review:
    """Outcome of checking a model reply."""

    errors: list[str] = field(default_factory=list)  # reject + repair
    warnings: list[str] = field(default_factory=list)  # accept, surface to the user
    needs_review: list[dict[str, str]] = field(default_factory=list)  # accept, add to needsReview


def review_analysis(data: Any, *, max_layers: int, present: np.ndarray | None = None) -> Review:
    """Schema + semantic checks of a model reply. Pure function: does not modify ``data``."""
    review = Review(errors=schema_errors("analysis", data))
    if review.errors:
        return review  # semantic checks assume the schema shape

    layers = data["layers"]
    ids = [layer["id"] for layer in layers]
    if len(layers) > max_layers:
        review.errors.append(f"layers: {len(layers)} layers planned, the maximum is {max_layers}")
    if len(set(ids)) != len(ids):
        review.errors.append("layers: ids must be unique")
    orders = [layer["order"] for layer in layers]
    if len(set(orders)) != len(orders):
        review.errors.append("layers: order values must be unique")
    for i, layer in enumerate(layers):
        x0, y0, x1, y1 = layer["bbox"]
        if not (x0 < x1 and y0 < y1):
            review.errors.append(f"layers/{i}/bbox: expected x0 < x1 and y0 < y1, got {layer['bbox']}")
    if review.errors:
        return review

    by_order = sorted(layers, key=lambda layer: layer["order"])
    by_id = {layer["id"]: layer for layer in layers}
    depths = [layer["depthEstimate"] for layer in by_order]
    if any(b < a for a, b in zip(depths, depths[1:])):
        review.warnings.append("depthEstimate does not increase with order; order is treated as authoritative")
    if sorted(orders) != list(range(len(layers))):
        review.warnings.append("layer orders are not contiguous from 0; they will be renumbered")
    for layer in layers:
        for target in layer.get("occludes", []):
            if target not in by_id:
                review.warnings.append(f"layer {layer['id']} occludes unknown layer '{target}' (ignored)")
            elif by_id[target]["order"] >= layer["order"]:
                review.needs_review.append({
                    "field": f"layers/{layer['id']}/occludes",
                    "reason": f"claims to cover '{target}', which is not behind it in the planned order",
                })
    if by_order[0]["role"] not in ("sky", "background"):
        review.warnings.append(f"the farthest layer has role '{by_order[0]['role']}', expected sky/background")

    if present is not None and len(present):
        for i, colour in enumerate(data["palette"]):
            nearest = float(np.min(np.linalg.norm(present - np.array(_rgb(colour["hex"])), axis=1)))
            if nearest > PALETTE_MISMATCH_RGB:
                review.needs_review.append({
                    "field": f"palette/{i}",
                    "reason": f"{colour['hex']} is not close to any colour measured in the image "
                              f"(nearest distance {nearest:.0f}); see measured.palette",
                })
    return review


def finalize(data: dict[str, Any], review: Review) -> dict[str, Any]:
    """Copy of an accepted reply with contiguous orders and review items merged in."""
    out = copy.deepcopy(data)
    out["layers"].sort(key=lambda layer: layer["order"])
    for i, layer in enumerate(out["layers"]):
        layer["order"] = i
    known = {layer["id"] for layer in out["layers"]}
    for layer in out["layers"]:
        if "occludes" in layer:
            layer["occludes"] = [t for t in layer["occludes"] if t in known]
    out["needsReview"] = list(out.get("needsReview", [])) + review.needs_review
    if review.warnings:
        out["warnings"] = list(out.get("warnings", [])) + review.warnings
    return out
