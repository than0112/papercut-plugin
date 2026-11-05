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

PROMPT_VERSION = "analysis-v2"
MAX_MODEL_SIDE = 1536  # long side of the copy sent to the model; bboxes are normalised
PALETTE_SIZE = 12
MIN_CLUSTER_SHARE = 0.005  # drop k-means clusters smaller than 0.5% of the pixels
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


def build_instructions(*, max_layers: int, brief: str | None,
                       measured: list[dict[str, Any]] | None = None) -> str:
    schema_text = json.dumps(model_schema(), ensure_ascii=False, separators=(",", ":"))
    brief_text = brief.strip() if brief and brief.strip() else "(none)"
    if measured:
        listed = ", ".join(f"{c['hex']} ({c['share'] * 100:.1f}%)" for c in measured)
        colour_source = (
            "- MEASURED COLOURS: the plugin clustered the actual pixels into these colours (share of image):\n"
            f"  {listed}\n"
            "  Take palette hex values from this list where they fit, and give each a role and a name.\n"
            "  Add a colour that is not listed only for a small but clearly visible accent.\n"
            "- The plugin checks every palette colour against the pixels itself. Do NOT add needsReview\n"
            "  items about being unable to sample pixels."
        )
    else:
        colour_source = "- Use colours that are actually visible."
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
  segmentation step can find them without seeing your reasoning. Keep each under ~120 words.
- "occludes": ids of FARTHER layers this layer covers.
- "id": lowercase, e.g. sky, background, midground_01, midground_02, foreground.
- Use "geometryHint" = "extrude" only for pieces whose paper thickness is clearly visible.

COLOURS
- "palette": 5-12 main colours as #rrggbb, each with a role.
{colour_source}

LIGHT
- "light": the main light that shades the paper surfaces, judged from highlights and the drop
  shadows between paper layers (not from where a sun is drawn). azimuthDeg: 0 = from the viewer,
  -90 = from the left, +90 = from the right. elevationDeg: positive = from above.
  colorTemperatureK: warm ~3000, neutral ~5500, cool ~8000.
- "light.emitters": every visible element that should glow in the 3D scene (a drawn sun or moon,
  lanterns, lit windows, lamps, fire), with the id of the layer it belongs to, a normalised bbox and
  its colour. Leave it empty if nothing glows.

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
        f"Previous answer (fix it; return the complete corrected JSON object only):\n{previous[:30000]}"
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


def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB uint8 (N, 3) -> CIE Lab (D65), for perceptual clustering."""
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def _kmeans(x: np.ndarray, k: int, *, iters: int = 25, seed: int = 0) -> np.ndarray:
    """Deterministic k-means++ / Lloyd. Returns the cluster label of each row."""
    rng = np.random.default_rng(seed)
    centres = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        d2 = np.min(((x[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
        if d2.sum() == 0:
            break
        centres.append(x[rng.choice(len(x), p=d2 / d2.sum())])
    c = np.array(centres)
    labels = np.zeros(len(x), dtype=np.int64)
    for _ in range(iters):
        labels = np.argmin(((x[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        new = np.array([x[labels == j].mean(0) if np.any(labels == j) else c[j] for j in range(len(c))])
        if np.allclose(new, c):
            break
        c = new
    return labels


def measure_palette(path: str, colors: int = PALETTE_SIZE) -> list[dict[str, Any]]:
    """Dominant colours measured from the pixels, most common first.

    k-means in CIE Lab on a 256 px copy (deterministic). Unlike median cut in
    RGB it keeps minority hues that differ strongly from the rest (water, moss,
    lanterns) as their own clusters.
    """
    pixels = _opaque_pixels(path)
    if pixels.size == 0:
        return []
    if len(pixels) > 20000:
        pixels = pixels[np.random.default_rng(0).choice(len(pixels), 20000, replace=False)]
    labels = _kmeans(_srgb_to_lab(pixels), min(colors, len(np.unique(pixels, axis=0))))
    out = []
    for j in np.unique(labels):
        members = pixels[labels == j]
        share = len(members) / len(pixels)
        if share < MIN_CLUSTER_SHARE:
            continue
        r, g, b = np.rint(members.mean(0)).astype(int)
        out.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "share": round(share, 4)})
    return sorted(out, key=lambda c: -c["share"])


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
    palette_check: dict[str, Any] = field(default_factory=dict)  # what the pixel cross-check found


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
    for i, emitter in enumerate(data["light"].get("emitters", [])):
        x0, y0, x1, y1 = emitter["bbox"]
        if not (x0 < x1 and y0 < y1):
            review.errors.append(f"light/emitters/{i}/bbox: expected x0 < x1 and y0 < y1, got {emitter['bbox']}")
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
    for emitter in data["light"].get("emitters", []):
        if emitter.get("layerId") and emitter["layerId"] not in by_id:
            review.warnings.append(f"emitter '{emitter['name']}' names unknown layer '{emitter['layerId']}' (unlinked)")
    if by_order[0]["role"] not in ("sky", "background"):
        review.warnings.append(f"the farthest layer has role '{by_order[0]['role']}', expected sky/background")

    if present is not None and len(present):
        mismatched = []
        for i, colour in enumerate(data["palette"]):
            nearest = float(np.min(np.linalg.norm(present - np.array(_rgb(colour["hex"])), axis=1)))
            if nearest > PALETTE_MISMATCH_RGB:
                mismatched.append(colour["hex"].lower())
                review.needs_review.append({
                    "field": f"palette/{i}",
                    "reason": f"{colour['hex']} is not close to any colour measured in the image "
                              f"(nearest distance {nearest:.0f}); see measured.palette",
                })
        review.palette_check = {
            "method": "each palette colour vs. every colour covering >= 0.2% of the pixels "
                      f"(RGB distance <= {PALETTE_MISMATCH_RGB:.0f})",
            "checked": len(data["palette"]),
            "found_in_image": len(data["palette"]) - len(mismatched),
            "not_found": mismatched,
        }
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
    for colour in out["palette"]:
        colour["hex"] = colour["hex"].lower()
    for emitter in out["light"].get("emitters", []):
        emitter["color"] = emitter["color"].lower()
        if emitter.get("layerId") and emitter["layerId"] not in known:
            del emitter["layerId"]
    out["needsReview"] = list(out.get("needsReview", [])) + review.needs_review
    if review.warnings:
        out["warnings"] = list(out.get("warnings", [])) + review.warnings
    return out
