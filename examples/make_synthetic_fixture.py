"""Generate a synthetic, procedurally drawn paper-cut fixture.

This is a TEST FIXTURE with known ground-truth layers. It is not output of
the PaperCraft pipeline and must never be presented as such: every layer's
provenance says ``method: synthetic-fixture``.

It writes::

    <out>/source.png            the flattened "paper-cut" image (pipeline input)
    <out>/assets/<layer>.png    ground-truth transparent layers, full image size
    <out>/masks/<layer>.png     ground-truth masks (L mode, 0/255)
    <out>/scene.json            a scene.json (schema 1.0) wired to those layers

Usage:  python examples/make_synthetic_fixture.py <out_dir> [--width 1200 --height 800]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

PPU = 100  # pixels per world unit
SEED = 7


def _ridge(width: int, base: float, amp: float, freq: float, phase: float, rng: np.random.Generator) -> list[tuple[float, float]]:
    xs = np.linspace(0, width, 48)
    ys = base + amp * np.sin(xs / width * math.tau * freq + phase) + rng.normal(0, amp * 0.12, xs.size)
    return [(float(x), float(y)) for x, y in zip(xs, ys)]


def _layer_from_polygon(size: tuple[int, int], top: list[tuple[float, float]], color: str) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).polygon(top + [(w, h), (0, h)], fill=color)
    return img


def _edge_highlight(img: Image.Image) -> Image.Image:
    """Lighten a thin band along the upper edge, like light catching a paper cut."""
    alpha = img.getchannel("A")
    k = img.width / 1200  # effects scale with the image, like a photo of real paper
    shifted = ImageChops.offset(alpha, 0, max(1, round(4 * k)))
    band = ImageChops.subtract(alpha, shifted).filter(ImageFilter.GaussianBlur(1.5 * k))
    white = Image.new("RGBA", img.size, (255, 255, 255, 0))
    white.putalpha(band.point(lambda v: int(v * 0.35)))
    return Image.alpha_composite(img, white)


def build_layers(width: int, height: int) -> list[dict]:
    rng = np.random.default_rng(SEED)
    size = (width, height)

    grad = np.linspace(0, 1, height)[:, None]
    top, bottom = np.array([244, 214, 170]), np.array([250, 236, 205])
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., :3] = (top * (1 - grad) + bottom * grad)[:, None, :]
    rgba[..., 3] = 255
    sky = Image.fromarray(rgba, "RGBA")
    ImageDraw.Draw(sky).ellipse([width * 0.68, height * 0.12, width * 0.80, height * 0.12 + width * 0.12], fill="#f7b267")

    specs = [
        ("mountains", "Far mountains", "background", _ridge(width, height * 0.42, height * 0.10, 1.6, 0.3, rng), "#c9a7a0"),
        ("hills_far", "Rolling hills", "midground", _ridge(width, height * 0.56, height * 0.06, 2.3, 1.7, rng), "#b77b6b"),
        ("hills_near", "Near hills", "midground", _ridge(width, height * 0.68, height * 0.05, 1.2, 4.1, rng), "#8f4f4a"),
    ]
    layers = [{"id": "sky", "name": "Sky and sun", "role": "sky", "image": sky}]
    for lid, name, role, ridge, color in specs:
        img = _edge_highlight(_layer_from_polygon(size, ridge, color))
        layers.append({"id": lid, "name": name, "role": role, "image": img})

    fg = _layer_from_polygon(size, _ridge(width, height * 0.84, height * 0.03, 3.0, 2.2, rng), "#5a2e3a")
    draw = ImageDraw.Draw(fg)
    for i, x in enumerate(np.linspace(width * 0.06, width * 0.94, 7)):
        trunk_h = height * (0.20 + 0.07 * (i % 3))
        base_y = height * 0.86
        draw.rectangle([x - 5, base_y - trunk_h * 0.35, x + 5, base_y], fill="#5a2e3a")
        for tier in range(3):
            w_t = width * (0.055 - tier * 0.012)
            y0 = base_y - trunk_h * (0.3 + tier * 0.25)
            draw.polygon([(x - w_t, y0), (x + w_t, y0), (x, y0 - trunk_h * 0.38)], fill="#5a2e3a")
    layers.append({"id": "foreground", "name": "Trees and grass", "role": "foreground",
                   "image": _edge_highlight(fg)})
    return layers


def flatten(layers: list[dict]) -> Image.Image:
    """Composite back-to-front with a soft drop shadow under each layer, like stacked paper."""
    out = layers[0]["image"].copy()
    for layer in layers[1:]:
        alpha = layer["image"].getchannel("A")
        shadow = Image.new("RGBA", out.size, (40, 20, 20, 0))
        k = out.width / 1200  # shadow size scales with the image
        shadow.putalpha(ImageChops.offset(alpha, round(6 * k), round(-8 * k))
                        .filter(ImageFilter.GaussianBlur(9 * k)).point(lambda v: int(v * 0.45)))
        out = Image.alpha_composite(out, shadow)
        out = Image.alpha_composite(out, layer["image"])
    return out


def scene_for(layers: list[dict], width: int, height: int, source_sha: str) -> dict:
    spacing = 0.6
    scene_layers = []
    for order, layer in enumerate(layers):
        scene_layers.append({
            "id": layer["id"],
            "name": layer["name"],
            "role": layer["role"],
            "order": order,
            "asset": f"assets/{layer['id']}.png",
            "mask": f"masks/{layer['id']}.png",
            "depth": round(order * spacing, 3),
            "scale": round(1 + (len(layers) - 1 - order) * 0.02, 3),
            "geometry": {"type": "plane", "thickness": 0.02},
            "material": {"roughness": 0.95, "metalness": 0, "alphaTest": 0.5, "side": "double",
                         "paperTexture": {"enabled": True, "strength": 0.06, "scale": 8}},
            "parallaxFactor": round(order / max(1, len(layers) - 1), 3),
            "castShadow": order > 0,
            "receiveShadow": True,
            "visible": True,
            "provenance": {"source": "manual", "method": "synthetic-fixture", "confidence": 1, "inpainted": False},
        })
    day = {
        "preset": "day",
        "ambient": {"type": "hemisphere", "color": "#fff4e0", "groundColor": "#6b4a3a", "intensity": 0.9},
        "key": {"color": "#ffdfb0", "colorTemperatureK": 4200, "intensity": 2.2, "position": [-4, 5, 7],
                "castShadow": True, "shadow": {"mapSize": 2048, "bias": -0.0005, "normalBias": 0.02, "radius": 4}},
        "background": "#1b1b1f",
    }
    night = {
        "preset": "night",
        "ambient": {"type": "hemisphere", "color": "#8fa8ff", "groundColor": "#1a1530", "intensity": 0.35},
        "key": {"color": "#b8c8ff", "colorTemperatureK": 8500, "intensity": 1.2, "position": [4, 6, 6],
                "castShadow": True, "shadow": {"mapSize": 2048, "bias": -0.0005, "normalBias": 0.02, "radius": 6}},
        "rim": {"color": "#6f8cff", "intensity": 1.5, "position": [0, 2, -3]},
        "background": "#0b0d18",
    }
    return {
        "schemaVersion": "1.0",
        "sceneId": "synthetic-fixture",
        "name": "Synthetic paper-cut fixture (procedurally drawn, not pipeline output)",
        "mode": "layered",
        "generator": {"name": "examples/make_synthetic_fixture.py", "version": "0.1.0", "revision": 0},
        "source": {"image": "source.png", "width": width, "height": height, "sha256": source_sha},
        "units": {"pixelsPerUnit": PPU},
        "frame": {"width": width / PPU, "height": height / PPU},
        "camera": {"type": "orthographic", "position": [0, 0, 20], "target": [0, 0, 0], "zoom": 1,
                   "minZoom": 0.6, "maxZoom": 3, "near": 0.1, "far": 100,
                   "maxPolarAngle": 0.35, "maxAzimuthAngle": 0.5},
        "layers": scene_layers,
        "lighting": day,
        "lightingPresets": {"day": day, "night": night},
        "rendering": {"toneMapping": "neutral", "exposure": 1, "outputColorSpace": "srgb", "antialias": True,
                      "shadows": True, "maxPixelRatio": 2,
                      "contactShadows": {"enabled": False, "opacity": 0.4, "blur": 2.5}},
        "interaction": {"orbit": True, "zoom": True,
                        "parallax": {"enabled": True, "strength": 0.15},
                        "explodedView": {"enabled": True, "spacing": 1.5}},
    }


def generate(out_dir: Path, width: int = 1200, height: int = 800) -> dict:
    out_dir = Path(out_dir)
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "masks").mkdir(parents=True, exist_ok=True)
    layers = build_layers(width, height)
    for layer in layers:
        layer["image"].save(out_dir / "assets" / f"{layer['id']}.png", optimize=True)
        layer["image"].getchannel("A").point(lambda v: 255 if v >= 128 else 0).save(
            out_dir / "masks" / f"{layer['id']}.png", optimize=True)
    flatten(layers).convert("RGB").save(out_dir / "source.png", optimize=True)
    sha = hashlib.sha256((out_dir / "source.png").read_bytes()).hexdigest()
    scene = scene_for(layers, width, height, sha)
    (out_dir / "scene.json").write_text(json.dumps(scene, indent=2) + "\n", encoding="utf-8")
    return scene


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args()
    scene = generate(args.out_dir, args.width, args.height)
    print(f"wrote {len(scene['layers'])} layers to {args.out_dir}")


if __name__ == "__main__":
    main()
