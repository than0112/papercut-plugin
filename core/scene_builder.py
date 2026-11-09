"""analysis.json + layers.json -> scene.json (and emissive maps).

Every number written here is derived from the two input files plus a few named
constants below, so DESIGN_SPEC.md can explain each value and a re-run gives the
same scene.

Safety bounds
    papercraft_segment completed every layer ``fill.marginPx`` behind nearer
    layers. Tilting the camera by θ shifts two layers Δz apart by Δz·sin θ (in
    world units); mouse parallax shifts them by strength·Δfactor. Both are kept
    within the margin (TILT_BUDGET + PARALLAX_BUDGET = 1), so the unfilled area
    behind a layer can never be revealed in normal viewing. The exploded view is
    an inspection mode and is exempt (a scene warning says so).

Lighting
    The source art already contains painted light and shadow. With three.js
    physical light units a white diffuse surface shows its texture colour at a
    total irradiance of π, so ambient + key·cos(angle) is set to TOTAL_LIGHT
    (slightly below π): ~55% ambient keeps the painted look, ~45% directional
    key light adds real shadows between the paper layers.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage import color as skcolor

FRAME_HEIGHT = 8.0  # world units for the image height
CAMERA_DISTANCE = 20.0
TILT_BUDGET = 0.6
PARALLAX_BUDGET = 0.4
DEFAULT_MAX_TILT_DEG = 20.0
MAX_PARALLAX = 0.3  # world units
TOTAL_LIGHT = 3.0
AMBIENT_SHARE = 0.55
EMITTER_LAB_DISTANCE = 32.0
EMITTER_MIN_L = 50.0
# Hot cores: the brightest part of a lamp or window is drawn nearly white, too far from the
# emitter colour to match. Such pixels glow when they are very bright, tinted towards the
# emitter's hue (white paper is not) and connected to pixels that do match the colour.
EMITTER_HOT_L = 85.0
EMITTER_HOT_TINT = 6.0  # Lab chroma along the emitter's hue direction
# Share of its box an emitter may light before the glow map is flagged for review. A disc or a
# paper lantern fills ~79% of a tight box; a tightly boxed window is mostly glass between bars.
FILL_WARNING = {"sun": 0.9, "moon": 0.9, "lantern": 0.9, "lamp": 0.9, "window": 0.8}
DAY_EMISSIVE = 0.6
NIGHT_EMISSIVE = 1.8


def kelvin_to_hex(kelvin: float) -> str:
    """Approximate sRGB colour of a black body (Tanner Helland's fit), 1000-40000 K."""
    t = max(1000.0, min(40000.0, kelvin)) / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    return "#" + "".join(f"{int(max(0, min(255, round(v)))):02x}" for v in (r, g, b))


def light_position(azimuth_deg: float, elevation_deg: float, radius: float = 10.0) -> list[float]:
    """Analysis convention: azimuth 0 = from the viewer, -90 = from the left; elevation up."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    x = radius * math.cos(el) * math.sin(az)
    y = radius * math.sin(el)
    z = max(1.0, radius * math.cos(el) * math.cos(az))  # keep the key in front of the paper
    return [round(x, 3), round(y, 3), round(z, 3)]


def _key_intensity(position: list[float], ambient: float, total: float) -> float:
    x, y, z = position
    cos_front = z / math.sqrt(x * x + y * y + z * z)  # facing +z
    return round(min(6.0, max(0.3, (total - ambient) / max(cos_front, 0.15))), 3)


def day_lighting(light: dict[str, Any]) -> dict[str, Any]:
    position = light_position(light["azimuthDeg"], light["elevationDeg"])
    ambient = round(TOTAL_LIGHT * AMBIENT_SHARE, 3)
    softness = float(light.get("softness", 0.6))
    return {
        "preset": "day",
        "ambient": {"type": "ambient", "color": "#ffffff", "intensity": ambient},
        "key": {
            "color": kelvin_to_hex(light["colorTemperatureK"]),
            "colorTemperatureK": light["colorTemperatureK"],
            "intensity": _key_intensity(position, ambient, TOTAL_LIGHT),
            "position": position,
            "castShadow": True,
            "shadow": {"mapSize": 2048, "bias": -0.0005, "normalBias": 0.02,
                       "radius": round(2 + 6 * softness, 2)},
        },
        "background": "#1b1b1f",
        "emissiveScale": DAY_EMISSIVE,
    }


def night_lighting() -> dict[str, Any]:
    position = light_position(35, 45)
    ambient = 0.75
    return {
        "preset": "night",
        "ambient": {"type": "hemisphere", "color": "#8da2e0", "groundColor": "#1c1830", "intensity": ambient},
        "key": {
            "color": kelvin_to_hex(9000), "colorTemperatureK": 9000,
            "intensity": _key_intensity(position, ambient, 1.6),
            "position": position, "castShadow": True,
            "shadow": {"mapSize": 2048, "bias": -0.0005, "normalBias": 0.02, "radius": 6},
        },
        "rim": {"color": "#6f8cff", "intensity": 1.2, "position": [0, 3, -4]},
        "background": "#0b0d18",
        "emissiveScale": NIGHT_EMISSIVE,
    }


def layer_depths(estimates: list[float], spacing: float) -> list[float]:
    """World Z per layer (back = 0), from the analysed depth estimates, strictly increasing."""
    n = len(estimates)
    if n == 1:
        return [0.0]
    if any(b <= a for a, b in zip(estimates, estimates[1:])):
        estimates = [i / (n - 1) for i in range(n)]
    lo, hi = estimates[0], estimates[-1]
    out = [(e - lo) / (hi - lo) * spacing * (n - 1) for e in estimates]
    for i in range(1, n):
        out[i] = max(out[i], out[i - 1] + 0.25 * spacing)
    return [round(z, 4) for z in out]


def view_limits(depths: list[float], ppu: float, margin_px: int, max_tilt_deg: float | None,
                preview: bool) -> dict[str, float]:
    """Max camera tilt (rad) and parallax strength (world units) that keep gaps hidden."""
    cap = math.radians(max_tilt_deg if max_tilt_deg is not None else DEFAULT_MAX_TILT_DEG)
    n = len(depths)
    if preview or n < 3 or margin_px <= 0:
        # Back layer is complete everywhere; with <3 layers no completed-but-limited layer exists.
        theta = min(cap, math.radians(8)) if preview else cap
        strength = 0.0 if preview else 0.15
        factors = [0.0] * n if n < 2 else [(z - depths[0]) / (depths[-1] - depths[0]) for z in depths]
        return {"theta": theta, "strength": strength, "factors": factors, "tilt_px": 0.0, "parallax_px": 0.0}
    span = depths[-1] - depths[1]  # nearest layer vs the first completed-but-limited layer
    factors = [(z - depths[0]) / (depths[-1] - depths[0]) for z in depths]
    factors = [_floor(f, 4) if f < 1 else 1.0 for f in factors]
    sin_max = min(1.0, TILT_BUDGET * margin_px / (span * ppu))
    theta = _floor(min(cap, math.asin(sin_max)), 4)  # round DOWN so the stored value keeps the bound
    dfactor = factors[-1] - factors[1]
    strength = min(MAX_PARALLAX, PARALLAX_BUDGET * margin_px / (dfactor * ppu)) if dfactor > 0 else MAX_PARALLAX
    strength = _floor(strength, 4)
    return {"theta": theta, "strength": strength, "factors": factors,
            "tilt_px": span * math.sin(theta) * ppu, "parallax_px": strength * dfactor * ppu}


def _floor(value: float, digits: int) -> float:
    scale = 10 ** digits
    return math.floor(value * scale) / scale


def emissive_maps(source_rgb: np.ndarray, owners: dict[str, np.ndarray], emitters: list[dict[str, Any]]
                  ) -> tuple[dict[str, np.ndarray], dict[str, float], list[dict[str, Any]], list[str]]:
    """Glow maps from the analysed emitters: only bright pixels close to the emitter colour.

    Returns per-layer RGB emissive maps, per-layer intensity, a per-emitter report and warnings.
    The bbox only limits the search; it is never used as the glowing area itself.
    """
    h, w = source_rgb.shape[:2]
    lab = skcolor.rgb2lab(source_rgb)
    maps: dict[str, np.ndarray] = {}
    intensity: dict[str, float] = {}
    report, warnings = [], []
    for emitter in emitters:
        layer = emitter.get("layerId")
        if layer not in owners:
            warnings.append(f"emitter '{emitter['name']}' is not linked to a scene layer; skipped")
            continue
        x0, y0, x1, y1 = emitter["bbox"]
        bx0, by0 = int(x0 * w), int(y0 * h)
        bx1, by1 = max(bx0 + 1, int(math.ceil(x1 * w))), max(by0 + 1, int(math.ceil(y1 * h)))
        hexc = emitter["color"].lstrip("#")
        target = skcolor.rgb2lab(np.array([[[int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4)]]]))[0, 0]
        box = np.zeros((h, w), dtype=bool)
        box[by0:by1, bx0:bx1] = True
        candidate = box & owners[layer]
        dist = np.linalg.norm(lab - target, axis=-1)
        seeds = candidate & (dist < EMITTER_LAB_DISTANCE) & (lab[..., 0] > EMITTER_MIN_L)
        hot = candidate & ~seeds & _hot_core(lab, target)
        glow = ndi.binary_propagation(seeds, mask=seeds | hot) if hot.any() else seeds
        # A pure-white centre has no hue at all; take it when the glow fully encloses it.
        glow |= ndi.binary_fill_holes(glow) & candidate & (lab[..., 0] >= EMITTER_HOT_L)
        hot_px = int((glow & ~seeds).sum())
        glow = ndi.binary_opening(glow, iterations=1)  # drop single-pixel speckle
        pixels = int(glow.sum())
        box_px = int(box.sum())
        owned_px = int(candidate.sum())
        if pixels == 0:
            warnings.append(f"emitter '{emitter['name']}' matched no pixels of its colour; no glow added")
            report.append({"name": emitter["name"], "layer": layer, "pixels": 0, "boxFraction": 0.0,
                           "layerFraction": 0.0, "hotCorePixels": 0})
            continue
        # Soft edge for anti-aliasing, confined to 2 px around the glowing pixels.
        soft = ndi.gaussian_filter(glow.astype(np.float32), 1.0) * ndi.binary_dilation(glow, iterations=2)
        rgb = (source_rgb.astype(np.float32) * soft[..., None]).clip(0, 255)
        maps[layer] = np.maximum(maps.get(layer, np.zeros_like(rgb)), rgb)
        intensity[layer] = max(intensity.get(layer, 0.0), float(emitter.get("intensity", 0.6)))
        fraction = pixels / box_px
        if fraction > FILL_WARNING.get(emitter.get("kind"), 0.6):
            warnings.append(f"emitter '{emitter['name']}' glows over {fraction:.0%} of its box; check the glow map")
        report.append({"name": emitter["name"], "layer": layer, "pixels": pixels, "boxFraction": round(fraction, 3),
                       "layerFraction": round(pixels / owned_px, 3) if owned_px else 0.0, "hotCorePixels": hot_px})
    return {k: v.astype(np.uint8) for k, v in maps.items()}, intensity, report, warnings


def _hot_core(lab: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Very bright pixels tinted towards the target hue (any bright pixel if the target is near grey)."""
    bright = lab[..., 0] >= max(EMITTER_HOT_L, min(float(target[0]), 95.0))
    chroma = float(np.hypot(target[1], target[2]))
    if chroma < 10:  # a white or grey emitter (moon, cold lamp) has no hue to compare
        return bright
    ua, ub = target[1] / chroma, target[2] / chroma
    along = lab[..., 1] * ua + lab[..., 2] * ub
    across = np.abs(lab[..., 2] * ua - lab[..., 1] * ub)
    return bright & (along >= EMITTER_HOT_TINT) & (across <= along * 1.2)


def load_owner_masks(project_dir, layers: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    return {layer["id"]: np.asarray(Image.open(project_dir / layer["mask"])) > 0 for layer in layers}
