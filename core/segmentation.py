"""Layer segmentation: pixels -> per-layer masks, transparent layers and hidden-area fill.

Built-in pipeline (no ML model on this machine):

1. Over-segment a working copy (SLIC superpixels in Lab), then merge adjacent
   superpixels of similar colour into at most ``MAX_MARKS`` significant regions.
   Paper cuts have clean edges, so region boundaries follow the real cut lines.
2. Each significant region gets a number drawn on a "marks" image. A vision
   model assigns numbers to the layers planned in analysis.json
   (Set-of-Mark labelling). Boundaries come from pixels, semantics from the model.
3. Small regions inherit the layer of the neighbour they share most boundary with.
4. The region map is upsampled to full resolution and boundary pixels are
   re-assigned by local colour similarity.
5. Every layer except the nearest is extended behind nearer layers by a margin
   and the hidden part is filled from the layer's own colours (away from the cast
   shadow band). The farthest layer is filled completely. The margin bounds how far
   the camera may move before an unfilled gap could show.

Everything here is deterministic given the label assignment.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from skimage import color as skcolor
from skimage import graph as skgraph
from skimage.segmentation import felzenszwalb, find_boundaries

WORK_SIDE = 1024  # long side of the working copy used for over-segmentation
MAX_MARKS = 80  # numbered regions shown to the labelling model
MIN_MARK_AREA = 0.0004  # fraction of the work image; smaller regions inherit a neighbour's layer
FILL_MARGIN = 0.05  # hidden-area extension, fraction of the full-res long side
SHADOW_BAND = 0.012  # fraction of the long side next to nearer layers not used as fill colour
REFINE_RADIUS = 2.5  # boundary refinement band, in work-image pixels
FZ_SCALE = 60  # Felzenszwalb scale on the work copy (small = follows fine edges)
FZ_MIN_SIZE = 15


@dataclass
class Regions:
    """Over-segmentation of the working copy."""

    labels: np.ndarray  # (h, w) int region id, 0..n-1
    marked: list[int]  # region ids that get a number, largest first
    numbers: dict[int, int]  # region id -> number shown on the marks image (1-based)
    areas: np.ndarray  # pixel count per region
    adjacency: dict[int, dict[int, int]]  # region -> neighbour -> shared boundary length
    merge_threshold: float = 0.0  # Lab distance used to merge the base segments


@dataclass
class LayerMaps:
    """Final per-layer result at full resolution."""

    owner: np.ndarray  # (H, W) int8: layer index owning each visible pixel, -1 = transparent source
    support: list[np.ndarray]  # per layer bool: visible + filled extension
    filled: list[np.ndarray]  # per layer bool: the filled (inpainted) part only
    rgba: list[np.ndarray]  # per layer (H, W, 4) uint8
    confidence: list[float] = field(default_factory=list)


# --------------------------------------------------------------------------- loading


def load_rgba(path: str) -> np.ndarray:
    with Image.open(path) as img:
        img.load()
        return np.asarray(img.convert("RGBA")).copy()


def work_copy(rgba: np.ndarray) -> tuple[np.ndarray, float]:
    """RGB working copy (transparent pixels on grey) and the full-res / work scale factor.

    The copy always has a long side of WORK_SIDE (small images are enlarged), so the
    segmentation parameters mean the same thing at every input resolution.
    """
    h, w = rgba.shape[:2]
    scale = max(h, w) / WORK_SIDE
    img = Image.fromarray(rgba, "RGBA")
    if abs(scale - 1.0) > 1e-6:
        img = img.resize((max(1, round(w / scale)), max(1, round(h / scale))), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", img.size, (128, 128, 128, 255))
    return np.asarray(Image.alpha_composite(bg, img).convert("RGB")), scale


# --------------------------------------------------------------------------- over-segmentation


def _adjacency(labels: np.ndarray) -> dict[int, dict[int, int]]:
    adj: dict[int, dict[int, int]] = {}
    for a, b in ((labels[:, :-1], labels[:, 1:]), (labels[:-1, :], labels[1:, :])):
        diff = a != b
        pairs = np.stack([a[diff], b[diff]], axis=1)
        pairs.sort(axis=1)
        uniq, counts = np.unique(pairs, axis=0, return_counts=True)
        for (x, y), c in zip(uniq.tolist(), counts.tolist()):
            adj.setdefault(x, {})[y] = adj.get(x, {}).get(y, 0) + c
            adj.setdefault(y, {})[x] = adj.get(y, {}).get(x, 0) + c
    return adj


def _relabel(labels: np.ndarray) -> np.ndarray:
    _, inverse = np.unique(labels, return_inverse=True)
    return inverse.reshape(labels.shape)


def oversegment(work_rgb: np.ndarray, *, max_marks: int = MAX_MARKS) -> Regions:
    """Edge-following over-segmentation, merged by colour until <= max_marks regions are significant.

    Felzenszwalb's graph segmentation at a fine scale follows the paper cut lines
    exactly. Adjacent regions are then merged transitively when their mean Lab
    colours differ by less than a threshold, so smooth gradients and soft cast
    shadows join the paper they lie on. The threshold is the smallest one that
    leaves at most ``max_marks`` significant regions.
    """
    lab = skcolor.rgb2lab(work_rgb)
    n_pixels = work_rgb.shape[0] * work_rgb.shape[1]
    base = felzenszwalb(work_rgb, scale=FZ_SCALE, sigma=0.8, min_size=FZ_MIN_SIZE)
    rag = skgraph.rag_mean_color(lab, base, mode="distance")
    min_area = max(16, int(MIN_MARK_AREA * n_pixels))

    def merged_at(thresh: float) -> np.ndarray:
        return _relabel(skgraph.cut_threshold(base, rag, thresh, in_place=False))

    def significant(labels: np.ndarray) -> int:
        return int(np.sum(np.bincount(labels.ravel()) >= min_area))

    lo, hi = 0.5, 60.0
    best = merged_at(hi)
    for _ in range(12):
        mid = (lo + hi) / 2
        candidate = merged_at(mid)
        if significant(candidate) <= max_marks:
            best, hi = candidate, mid
        else:
            lo = mid

    areas = np.bincount(best.ravel())
    marked = [int(r) for r in np.argsort(-areas) if areas[r] >= min_area][:max_marks]
    return Regions(labels=best, marked=marked, numbers={r: i + 1 for i, r in enumerate(marked)},
                   areas=areas, adjacency=_adjacency(best), merge_threshold=round(hi, 2))


# --------------------------------------------------------------------------- marks image


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render_marks(work_rgb: np.ndarray, regions: Regions) -> Image.Image:
    """Work image with region outlines and each significant region's number at its most interior point."""
    img = work_rgb.astype(np.float32)
    edges = find_boundaries(regions.labels, mode="inner")
    img[edges] = img[edges] * 0.25 + np.array([255, 255, 255]) * 0.75
    out = Image.fromarray(img.astype(np.uint8))
    draw = ImageDraw.Draw(out)
    for region in regions.marked:
        mask = regions.labels == region
        sl = ndi.find_objects(mask.astype(np.int8))[0]
        dist = ndi.distance_transform_edt(np.pad(mask[sl], 1))[1:-1, 1:-1]
        y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
        radius = float(dist[y, x])
        size = int(np.clip(radius * 1.1, 11, 26))
        draw.text((x + sl[1].start, y + sl[0].start), str(regions.numbers[region]), anchor="mm",
                  font=_font(size), fill=(255, 255, 255), stroke_width=max(2, size // 6), stroke_fill=(0, 0, 0))
    return out


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- labels -> layer map


def region_layer_map(regions: Regions, assignment: dict[int, int]) -> tuple[np.ndarray, dict[int, str], np.ndarray]:
    """Layer index per region; unassigned regions take the neighbour with the longest shared boundary.

    ``assignment`` maps region id -> layer index for the regions the labeller assigned.
    Returns the per-pixel layer map (work resolution), how each region got its layer, and the
    per-region layer index.
    """
    n = len(regions.areas)
    layer = np.full(n, -1, dtype=np.int64)
    how: dict[int, str] = {}
    for region, idx in assignment.items():
        layer[region] = idx
        how[region] = "labelled"
    pending = [r for r in range(n) if layer[r] < 0]
    while pending:
        progressed = []
        for r in pending:
            votes: dict[int, int] = {}
            for nb, length in regions.adjacency.get(r, {}).items():
                if layer[nb] >= 0:
                    votes[int(layer[nb])] = votes.get(int(layer[nb]), 0) + length
            if votes:
                progressed.append((r, max(votes, key=votes.get)))
        if not progressed:  # disconnected leftovers: farthest layer
            for r in pending:
                layer[r] = 0
                how[r] = "default"
            break
        for r, idx in progressed:
            layer[r] = idx
            how[r] = "inherited"
        pending = [r for r in pending if layer[r] < 0]
    return layer[regions.labels], how, layer


def upsample_and_refine(work_map: np.ndarray, full_rgb: np.ndarray, scale: float, n_layers: int) -> np.ndarray:
    """Nearest-upsample the work-resolution layer map, then re-decide pixels near layer boundaries.

    A boundary pixel goes to the candidate layer whose local mean colour (in Lab) is closest.
    """
    h, w = full_rgb.shape[:2]
    if work_map.shape == (h, w):
        full = work_map.copy()
    else:
        ys = np.minimum((np.arange(h) / scale).astype(int), work_map.shape[0] - 1)
        xs = np.minimum((np.arange(w) / scale).astype(int), work_map.shape[1] - 1)
        full = work_map[ys][:, xs]
    radius = max(1, int(round(REFINE_RADIUS * max(scale, 1.0))))
    band = find_boundaries(full, mode="thick")
    band = ndi.binary_dilation(band, iterations=radius)
    if not band.any():
        return full
    lab = skcolor.rgb2lab(full_rgb)
    win = 4 * radius + 1
    best = np.full((h, w), np.inf)
    choice = full.copy()
    for k in range(n_layers):
        mk = (full == k).astype(np.float64)
        if not mk.any():
            continue
        support = ndi.uniform_filter(mk, win)
        present = band & (support > 0.05)
        if not present.any():
            continue
        mean = np.stack([ndi.uniform_filter(lab[..., c] * mk, win) for c in range(3)], -1) / np.maximum(support, 1e-6)[..., None]
        dist = np.linalg.norm(lab - mean, axis=-1)
        better = present & (dist < best)
        best[better] = dist[better]
        choice[better] = k
    full[band] = choice[band]
    return full


def remove_islands(owner: np.ndarray, n_layers: int, *, min_fraction: float = 0.00002) -> np.ndarray:
    """Re-assign tiny isolated specks (edge noise) to the nearest pixel outside any speck."""
    min_px = max(4, int(min_fraction * owner.size))
    specks = np.zeros(owner.shape, dtype=bool)
    for k in range(n_layers):
        comps, n = ndi.label(owner == k)
        if n:
            sizes = np.bincount(comps.ravel())
            small = sizes < min_px
            small[0] = False
            specks |= small[comps]
    if not specks.any() or specks.all():
        return owner
    _, (iy, ix) = ndi.distance_transform_edt(specks, return_indices=True)
    out = owner.copy()
    out[specks] = owner[iy[specks], ix[specks]]
    return out


# --------------------------------------------------------------------------- hidden-area fill


def _push_pull(rgb: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Smooth interpolation of unknown pixels from known ones (weighted image pyramid).

    Push: average known colours down a 2x pyramid. Pull: fill each level's gaps
    from the bilinear upsampling of the coarser level. Unlike nearest-pixel fill
    it leaves no straight-line seams, which suits flat paper colours.
    """
    colour = rgb.astype(np.float64) * known[..., None]
    weight = known.astype(np.float64)
    stack = []
    while max(weight.shape) > 1:  # down to a single pixel, so every gap gets a value
        stack.append((colour, weight))
        h, w = weight.shape
        ph, pw = h % 2, w % 2
        colour = np.pad(colour, ((0, ph), (0, pw), (0, 0)), mode="edge")
        weight = np.pad(weight, ((0, ph), (0, pw)), mode="edge")
        colour = colour.reshape(colour.shape[0] // 2, 2, colour.shape[1] // 2, 2, 3).sum((1, 3))
        weight = weight.reshape(weight.shape[0] // 2, 2, weight.shape[1] // 2, 2).sum((1, 3))
    estimate = colour / np.maximum(weight, 1e-9)[..., None]
    for colour, weight in reversed(stack):
        h, w = weight.shape
        zoom = (h / estimate.shape[0], w / estimate.shape[1], 1)
        up = ndi.zoom(estimate, zoom, order=1, mode="nearest", grid_mode=True)[:h, :w]
        alpha = np.clip(weight, 0, 1)[..., None]
        estimate = colour / np.maximum(weight, 1e-9)[..., None] * alpha + up * (1 - alpha)
    return estimate


def _fill_from(rgb: np.ndarray, known: np.ndarray, target: np.ndarray) -> np.ndarray:
    """``rgb`` with ``target`` pixels replaced by a smooth fill interpolated from ``known`` pixels."""
    out = rgb.copy()
    if not target.any() or not known.any():
        return out
    ys, xs = np.nonzero(target | known)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1  # work on the bounding box only
    filled = _push_pull(rgb[y0:y1, x0:x1], known[y0:y1, x0:x1])
    region = target[y0:y1, x0:x1]
    out[y0:y1, x0:x1][region] = np.clip(np.rint(filled[region]), 0, 255).astype(np.uint8)
    return out


def build_layers(full_rgba: np.ndarray, owner: np.ndarray, n_layers: int, *, margin: float = FILL_MARGIN) -> LayerMaps:
    """Transparent per-layer images: visible pixels plus a filled extension behind nearer layers."""
    h, w = owner.shape
    rgb = full_rgba[..., :3]
    long_side = max(h, w)
    margin_px = max(2, int(round(margin * long_side)))
    shadow_px = max(1, int(round(SHADOW_BAND * long_side)))
    disk = ndi.generate_binary_structure(2, 1)
    valid = owner >= 0
    support: list[np.ndarray] = []
    filled: list[np.ndarray] = []
    rgba_out: list[np.ndarray] = []
    for k in range(n_layers):
        visible = owner == k
        nearer = owner > k
        if k == 0:
            ext = valid & ~visible  # the back layer is never transparent
        else:
            reach = ndi.binary_dilation(visible, structure=disk, iterations=margin_px) if visible.any() else visible
            ext = reach & nearer
        known = visible & ~ndi.binary_dilation(nearer, structure=disk, iterations=shadow_px)
        if known.sum() < 0.25 * visible.sum():  # thin layer: shadow exclusion would leave too little
            known = visible
        layer_rgb = _fill_from(rgb, known, ext)
        sup = visible | ext
        alpha = np.where(sup, 255, 0).astype(np.uint8)
        rgba_out.append(np.dstack([layer_rgb, alpha]))
        support.append(sup)
        filled.append(ext)
    return LayerMaps(owner=owner, support=support, filled=filled, rgba=rgba_out)


# --------------------------------------------------------------------------- review images

REVIEW_COLOURS = np.array([
    [66, 133, 244], [234, 67, 53], [251, 188, 5], [52, 168, 83], [171, 71, 188],
    [255, 112, 67], [0, 172, 193], [124, 179, 66], [240, 98, 146], [121, 85, 72],
    [158, 157, 36], [92, 107, 192]], dtype=np.float32)


def overlay(full_rgb: np.ndarray, owner: np.ndarray, names: list[str]) -> Image.Image:
    """Colour-coded layer ownership over a dimmed source, with a legend."""
    base = full_rgb.astype(np.float32) * 0.45
    tint = np.zeros_like(base)
    for k in range(len(names)):
        tint[owner == k] = REVIEW_COLOURS[k % len(REVIEW_COLOURS)]
    img = base + tint * 0.55
    edges = find_boundaries(owner, mode="inner")
    img[edges] = 255
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(out)
    size = max(14, out.width // 70)
    font = _font(size)
    for k, name in enumerate(names):
        y = 10 + k * (size + 8)
        c = tuple(int(v) for v in REVIEW_COLOURS[k % len(REVIEW_COLOURS)])
        draw.rectangle([10, y, 10 + size, y + size], fill=c, outline=(255, 255, 255))
        draw.text((18 + size, y + size // 2), f"{k}  {name}", anchor="lm", font=font, fill=(255, 255, 255),
                  stroke_width=2, stroke_fill=(0, 0, 0))
    return out


def stats(maps: LayerMaps) -> list[dict[str, Any]]:
    h, w = maps.owner.shape
    total = h * w
    out = []
    for k, sup in enumerate(maps.support):
        visible = maps.owner == k
        ys, xs = np.nonzero(sup) if sup.any() else (np.array([0]), np.array([0]))
        out.append({
            "coverage": round(float(visible.sum()) / total, 5),
            "supportCoverage": round(float(sup.sum()) / total, 5),
            "inpaintedFraction": round(float(maps.filled[k].sum()) / max(1, int(sup.sum())), 5),
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
        })
    return out
