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
    base: np.ndarray | None = None  # fine Felzenszwalb segments (before merging)
    lab: np.ndarray | None = None  # Lab work image


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


def _ward_merge(lab: np.ndarray, base: np.ndarray, *, min_area: int, max_marks: int,
                exclude: int | None = None) -> tuple[np.ndarray, float]:
    """Agglomerative merge of adjacent segments, cheapest first, until <= max_marks are significant.

    Cost of merging a and b = (n_a * n_b / (n_a + n_b)) * |mean_a - mean_b|^2 in Lab (Ward).
    Means are recomputed after every merge, so gradual colour chains (pink sky ->
    pink blossom -> white -> grey) cannot collapse into one region the way a
    transitive threshold merge does. Small specks are absorbed first.
    Returns the merged labels and the largest Lab distance between two significant regions merged.
    ``exclude`` is a segment id that never merges and is not counted (used to restrict the
    merge to part of the image).
    """
    import heapq

    n = int(base.max()) + 1
    flat = base.ravel()
    count = np.bincount(flat, minlength=n).astype(np.float64)
    sums = np.stack([np.bincount(flat, weights=lab[..., c].ravel(), minlength=n) for c in range(3)], 1)
    neighbours: list[set[int]] = [set() for _ in range(n)]
    for a, b in ((base[:, :-1], base[:, 1:]), (base[:-1, :], base[1:, :])):
        diff = a != b
        for x, y in np.unique(np.stack([a[diff], b[diff]], 1), axis=0).tolist():
            neighbours[x].add(y)
            neighbours[y].add(x)
    parent = np.arange(n)
    version = np.zeros(n, dtype=np.int64)
    alive = np.ones(n, dtype=bool)

    def cost(a: int, b: int) -> tuple[float, float]:
        d = sums[a] / count[a] - sums[b] / count[b]
        dist2 = float(d @ d)
        return count[a] * count[b] / (count[a] + count[b]) * dist2, dist2 ** 0.5

    if exclude is not None:
        for nb in neighbours[exclude]:
            neighbours[nb].discard(exclude)
        neighbours[exclude] = set()
    heap = []
    for a in range(n):
        for b in neighbours[a]:
            if a < b:
                heapq.heappush(heap, (cost(a, b)[0], a, b, 0, 0))
    significant = int(np.sum(count >= min_area)) - int(exclude is not None and count[exclude] >= min_area)
    regions_alive = n - int(exclude is not None)
    max_merged = 0.0
    # Stop when few enough significant regions remain and specks are gone.
    while heap and (significant > max_marks or regions_alive > 4 * max_marks):
        c, a, b, va, vb = heapq.heappop(heap)
        if not (alive[a] and alive[b]) or version[a] != va or version[b] != vb:
            continue
        if count[a] < count[b]:
            a, b = b, a  # keep the larger id as survivor
        if count[a] >= min_area and count[b] >= min_area:  # only merges of two real regions count
            max_merged = max(max_merged, cost(a, b)[1])
        significant -= int(count[a] >= min_area) + int(count[b] >= min_area)
        sums[a] += sums[b]
        count[a] += count[b]
        significant += int(count[a] >= min_area)
        alive[b] = False
        parent[b] = a
        regions_alive -= 1
        version[a] += 1
        for nb in neighbours[b]:
            if nb != a:
                neighbours[nb].discard(b)
                neighbours[nb].add(a)
                neighbours[a].add(nb)
        neighbours[a].discard(b)
        neighbours[b] = set()
        for nb in neighbours[a]:
            pa, pb = (a, nb) if a < nb else (nb, a)
            heapq.heappush(heap, (cost(pa, pb)[0], pa, pb, version[pa], version[pb]))

    # Resolve parents (path compression) and relabel.
    root = parent.copy()
    for i in range(n):
        r = i
        while root[r] != r:
            r = root[r]
        root[i] = r
    return _relabel(root[base]), max_merged


def oversegment(work_rgb: np.ndarray, *, max_marks: int = MAX_MARKS) -> Regions:
    """Edge-following over-segmentation, merged agglomeratively to <= max_marks significant regions.

    Felzenszwalb's graph segmentation at a fine scale follows the paper cut lines;
    a Ward-style agglomerative merge then joins similar neighbours (gradients, soft
    shadows) without the chaining a transitive threshold merge suffers from.
    """
    lab = skcolor.rgb2lab(work_rgb)
    n_pixels = work_rgb.shape[0] * work_rgb.shape[1]
    base = _relabel(felzenszwalb(work_rgb, scale=FZ_SCALE, sigma=0.8, min_size=FZ_MIN_SIZE))
    min_area = max(16, int(MIN_MARK_AREA * n_pixels))
    labels, max_merged = _ward_merge(lab, base, min_area=min_area, max_marks=max_marks)
    areas = np.bincount(labels.ravel())
    marked = [int(r) for r in np.argsort(-areas) if areas[r] >= min_area][:max_marks]
    return Regions(labels=labels, marked=marked, numbers={r: i + 1 for i, r in enumerate(marked)},
                   areas=areas, adjacency=_adjacency(labels), merge_threshold=round(max_merged, 2),
                   base=base, lab=lab)


REFINE_MAX_REGIONS = 6  # uncertain regions subdivided in the second labelling round
REFINE_MIN_AREA = 0.005  # ... if they cover at least this fraction of the image
REFINE_PARTS = 12  # target sub-regions per refined region
# Mixed-region check after the first round: a region whose two colour groups look like neighbours
# labelled with a different layer is sent to the second round even if the model did not flag it.
MIX_MATCH_LAB = 15.0  # a colour group "looks like" a neighbour within this Lab distance ...
MIX_GAIN_LAB = 8.0  # ... and at least this much closer than to any neighbour of the region's own layer
MIX_MIN_SHARE = 0.15  # the foreign-looking group covers at least this share of the region
MIX_L_WEIGHT = 0.5  # lightness counts less than hue: a cast shadow is a darker shade of the same paper


def _two_colour_groups(regions: Regions, r: int) -> list[tuple[np.ndarray, float]] | None:
    """Split region ``r`` into two colour groups of its base segments (area-weighted 2-means in Lab)."""
    mask = regions.labels == r
    ids, inv, counts = np.unique(regions.base[mask], return_inverse=True, return_counts=True)
    if len(ids) < 2:
        return None
    means = np.zeros((len(ids), 3))
    np.add.at(means, inv.ravel(), regions.lab[mask])
    means /= counts[:, None]
    weights = counts.astype(float)
    centre = np.average(means, axis=0, weights=weights)
    cov = ((means - centre).T * weights) @ (means - centre) / weights.sum()
    projection = (means - centre) @ np.linalg.eigh(cov)[1][:, -1]
    group = projection > np.average(projection, weights=weights)
    for _ in range(10):
        if group.all() or not group.any():
            return None
        m1, m0 = (np.average(means[g], axis=0, weights=weights[g]) for g in (group, ~group))
        new = np.linalg.norm(means - m1, axis=1) < np.linalg.norm(means - m0, axis=1)
        if (new == group).all():
            break
        group = new
    if group.all() or not group.any():
        return None
    return [(np.average(means[g], axis=0, weights=weights[g]), float(weights[g].sum())) for g in (~group, group)]


def mixed_suspects(regions: Regions, assignment: dict[int, int], min_px: float) -> list[tuple[float, int]]:
    """Labelled regions that probably mix layers, as (score, region id), most suspicious first.

    Each region is split into two colour groups. If one group is clearly closer in colour to an adjacent
    region labelled with another layer than to any adjacent region of its own layer, the region probably
    holds pieces of both layers (sky showing between blossoms, a trunk behind flowers).
    """
    n = int(regions.labels.max()) + 1
    flat = regions.labels.ravel()
    count = np.maximum(np.bincount(flat, minlength=n), 1).astype(float)
    mean = np.stack([np.bincount(flat, regions.lab[..., i].ravel(), minlength=n) for i in range(3)], 1) / count[:, None]
    # Mean colour of each layer over all its labelled regions: the reference when a region has no
    # adjacent region of its own layer.
    layer_sum: dict[int, np.ndarray] = {}
    layer_px: dict[int, float] = {}
    for r, k in assignment.items():
        layer_sum[k] = layer_sum.get(k, 0) + mean[r] * count[r]
        layer_px[k] = layer_px.get(k, 0) + count[r]
    weight = np.array([MIX_L_WEIGHT, 1.0, 1.0])
    out = []
    for r, own in assignment.items():
        if regions.areas[r] < min_px or regions.base is None:
            continue
        neighbours = [nb for nb in regions.adjacency.get(r, {}) if nb in assignment]
        if not neighbours:
            continue
        groups = _two_colour_groups(regions, r)
        if not groups:
            continue
        total = groups[0][1] + groups[1][1]
        score = 0.0
        for colour, area in groups:
            nearest: dict[int, float] = {}
            for nb in neighbours:
                d = float(np.linalg.norm((mean[nb] - colour) * weight))
                nearest[assignment[nb]] = min(nearest.get(assignment[nb], np.inf), d)
            rest = layer_px[own] - count[r]
            if rest > 0:  # the rest of the region's own layer, anywhere in the image
                own_mean = (layer_sum[own] - mean[r] * count[r]) / rest
                nearest[own] = min(nearest.get(own, np.inf), float(np.linalg.norm((own_mean - colour) * weight)))
            layer = min(nearest, key=nearest.get)
            gain = nearest.get(own, np.inf) - nearest[layer]
            share = area / total
            if layer != own and nearest[layer] < MIX_MATCH_LAB and gain > MIX_GAIN_LAB and share >= MIX_MIN_SHARE:
                score = max(score, share * min(gain, 40.0) * float(regions.areas[r]) / regions.labels.size)
        if score > 0:
            out.append((score, int(r)))
    return sorted(out, reverse=True)


def subdivide(regions: Regions, targets: list[int]) -> tuple[Regions, dict[int, int], list[int]]:
    """Split each target region into ~REFINE_PARTS parts by re-merging its base segments.

    Returns the new Regions (untouched regions keep their id; parts get new ids), a map
    part id -> parent region id, and the part ids in display order (largest first).
    """
    labels = regions.labels.copy()
    next_id = int(labels.max()) + 1
    parent: dict[int, int] = {}
    for region in targets:
        mask = regions.labels == region
        ys, xs = np.nonzero(mask)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        sub_mask = mask[y0:y1, x0:x1]
        base = regions.base[y0:y1, x0:x1]
        inside = _relabel(np.where(sub_mask, base, -1))  # -1 sorts first -> id 0 = outside
        has_outside = bool((~sub_mask).any())
        excl = 0 if has_outside else None
        min_area = max(16, int(0.04 * sub_mask.sum() / REFINE_PARTS))
        parts, _ = _ward_merge(regions.lab[y0:y1, x0:x1], inside, min_area=min_area,
                               max_marks=REFINE_PARTS + int(has_outside), exclude=excl)
        outside_id = parts[~sub_mask][0] if has_outside else None
        for part in np.unique(parts[sub_mask]):
            if part == outside_id:
                continue
            sel = np.zeros_like(mask)
            sel[y0:y1, x0:x1] = sub_mask & (parts == part)
            labels[sel] = next_id
            parent[next_id] = region
            next_id += 1
    areas = np.bincount(labels.ravel(), minlength=next_id)
    order = sorted(parent, key=lambda r: -areas[r])
    new = Regions(labels=labels, marked=regions.marked, numbers=regions.numbers, areas=areas,
                  adjacency=_adjacency(labels), merge_threshold=regions.merge_threshold,
                  base=regions.base, lab=regions.lab)
    return new, parent, order


def render_refine_marks(work_rgb: np.ndarray, regions: Regions, parts: list[int]) -> Image.Image:
    """Everything except the parts being refined is dimmed; parts are outlined and numbered 1..n."""
    focus = np.isin(regions.labels, parts)
    img = work_rgb.astype(np.float32)
    img[~focus] *= 0.3
    sub = np.where(focus, regions.labels, -1)
    edges = find_boundaries(sub, mode="inner") & focus
    img[edges] = img[edges] * 0.25 + 255 * 0.75
    out = Image.fromarray(img.astype(np.uint8))
    draw = ImageDraw.Draw(out)
    for i, part in enumerate(parts, start=1):
        mask = regions.labels == part
        sl = ndi.find_objects(mask.astype(np.int8))[0]
        dist = ndi.distance_transform_edt(np.pad(mask[sl], 1))[1:-1, 1:-1]
        y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
        size = int(np.clip(float(dist[y, x]) * 1.1, 11, 26))
        draw.text((x + sl[1].start, y + sl[0].start), str(i), anchor="mm", font=_font(size),
                  fill=(255, 255, 255), stroke_width=max(2, size // 6), stroke_fill=(0, 0, 0))
    return out


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
    present = np.zeros(n, dtype=bool)
    present[np.unique(regions.labels)] = True
    layer = np.full(n, -1, dtype=np.int64)
    how: dict[int, str] = {}
    for region, idx in assignment.items():
        layer[region] = idx
        how[region] = "labelled"
    pending = [r for r in range(n) if layer[r] < 0 and present[r]]
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
