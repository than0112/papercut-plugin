"""papercraft_build_scene — compose the segmented layers into a validated scene.json."""

from __future__ import annotations

import math
import shutil
from typing import Any

import numpy as np
from PIL import Image

from ..core import scene_builder as sb
from ..core.errors import ErrorCode, PaperCraftError, ok, tool_handler
from ..core.pipeline import Runtime, set_stage
from ..core.schemas import validate_scene
from ..core.storage import Project, utc_now
from ..core.version import VERSION
from ._common import PROJECT_ID_PROPERTY, open_project_for

NAME = "papercraft_build_scene"

SCHEMA = {
    "name": NAME,
    "description": (
        "Step 3. Place each segmented layer at its depth in a Three.js scene and write a schema-validated "
        "scene.json: orthographic camera, per-layer depth and parallax, paper materials, key + ambient light "
        "derived from the analysed light (plus day and night presets), glow maps for lit windows, lanterns "
        "and a drawn sun (only their bright pixels), and camera/parallax limits computed from the "
        "segmentation fill margin so hidden gaps never show. Returns how to open the live preview."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": PROJECT_ID_PROPERTY,
            "lighting_preset": {
                "type": "string",
                "enum": ["auto", "day", "night"],
                "default": "auto",
                "description": "Active lighting. 'auto' and 'day' use the analysed light; 'night' uses moonlight.",
            },
            "camera": {
                "type": "string",
                "enum": ["orthographic", "perspective"],
                "default": "orthographic",
                "description": "Orthographic preserves the original framing (recommended).",
            },
            "depth_spacing": {
                "type": "number",
                "minimum": 0.05,
                "maximum": 5,
                "default": 0.6,
                "description": "Average world units between consecutive layers (image height = 8 units).",
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
    project = open_project_for("build_scene", args, runtime)
    preset = args.get("lighting_preset", "auto")
    camera_type = args.get("camera", "orthographic")
    spacing = args.get("depth_spacing", 0.6)
    if preset not in ("auto", "day", "night"):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "lighting_preset must be auto, day or night.")
    if camera_type not in ("orthographic", "perspective"):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "camera must be orthographic or perspective.")
    if isinstance(spacing, bool) or not isinstance(spacing, (int, float)) or not 0.05 <= spacing <= 5:
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "depth_spacing must be a number from 0.05 to 5.")

    set_stage(project, "build_scene", "running")
    try:
        scene, report = build(project, preset=preset, camera_type=camera_type, spacing=float(spacing))
    except PaperCraftError as exc:
        exc.details.setdefault("project_id", project.id)
        set_stage(project, "build_scene", "failed", error=exc.to_dict())
        raise
    except Exception as exc:
        set_stage(project, "build_scene", "failed", error={"code": "internal_error", "message": str(exc)})
        raise
    set_stage(project, "build_scene", "degraded" if scene["mode"] == "preview" else "succeeded", mode=scene["mode"])
    return ok(NAME, _summary(project, scene, report), warnings=scene.get("warnings", []))


def build(project: Project, *, preset: str, camera_type: str, spacing: float) -> tuple[dict[str, Any], dict[str, Any]]:
    analysis = project.read_json("analysis.json")
    manifest = project.read_json("layers.json")
    meta = project.read_json("project.json")
    width, height = manifest["image"]["width"], manifest["image"]["height"]
    preview = manifest["mode"] == "preview"
    warnings: list[str] = list(manifest.get("warnings", [])) if preview else []

    by_id = {layer["id"]: layer for layer in analysis["layers"]}
    layers = sorted(manifest["layers"], key=lambda layer: layer["order"])
    for layer in layers:
        for rel in (layer["asset"], layer["mask"]):
            if not project.exists(rel):
                raise PaperCraftError(ErrorCode.ASSET_MISSING, f"{rel} is missing; run papercraft_segment again.")

    ppu = height / sb.FRAME_HEIGHT
    estimates = [by_id.get(layer["id"], {}).get("depthEstimate", i) for i, layer in enumerate(layers)]
    depths = sb.layer_depths([float(e) for e in estimates], spacing)
    margin = int(manifest["fill"]["marginPx"])
    limits = sb.view_limits(depths, ppu, margin, analysis["camera"].get("maxTiltDeg"), preview)

    # Glow maps (layered scenes only).
    emissive: dict[str, str] = {}
    intensity: dict[str, float] = {}
    emitter_report: list[dict[str, Any]] = []
    emitters = analysis["light"].get("emitters", [])
    for path in project.path("assets").glob("*_emissive.png"):
        path.unlink()
    if emitters and not preview:
        with Image.open(project.path(meta["source"]["path"])) as img:
            source_rgb = np.asarray(img.convert("RGB"))
        maps, intensity, emitter_report, emitter_warnings = sb.emissive_maps(
            source_rgb, sb.load_owner_masks(project.dir, layers), emitters)
        warnings += emitter_warnings
        for layer_id, rgb in maps.items():
            rel = f"assets/{layer_id}_emissive.png"
            Image.fromarray(rgb, "RGB").save(project.path(rel), optimize=True)
            emissive[layer_id] = rel

    distance = sb.CAMERA_DISTANCE + depths[-1]
    scene_layers = []
    for k, layer in enumerate(layers):
        hint = by_id.get(layer["id"], {}).get("geometryHint", "plane")
        if hint != "plane":
            warnings.append(f"layer '{layer['id']}': geometry '{hint}' suggested by the analysis is rendered as "
                            "a plane in this version")
        material: dict[str, Any] = {
            "roughness": 0.95, "metalness": 0, "alphaTest": 0.5, "side": "double",
            "paperTexture": {"enabled": True, "strength": 0.03, "scale": 8},
        }
        if layer["id"] in emissive:
            material.update({"emissive": "#ffffff", "emissiveIntensity": round(intensity[layer["id"]], 3),
                             "emissiveMap": emissive[layer["id"]]})
        scale = 1.0
        if camera_type == "perspective":  # nearer planes shrink so every layer covers the frame at rest
            scale = round((distance - depths[k]) / distance, 5)
        prov = layer["provenance"]
        scene_layers.append({
            "id": layer["id"], "name": layer["name"], "role": layer["role"], "order": k,
            "asset": layer["asset"], "mask": layer["mask"], "depth": depths[k], "offset": [0, 0], "scale": scale,
            "geometry": {"type": "plane", "thickness": 0.02},
            "material": material,
            "parallaxFactor": limits["factors"][k],
            "castShadow": k > 0, "receiveShadow": True, "visible": True,
            "provenance": {"source": prov["source"], "method": prov["method"][:120],
                           "confidence": prov["confidence"], "inpainted": prov["inpainted"]},
        })

    day = sb.day_lighting(analysis["light"])
    night = sb.night_lighting()
    theta = limits["theta"]  # already rounded down by view_limits
    camera: dict[str, Any] = {
        "type": camera_type, "position": [0, 0, round(distance, 3)], "target": [0, 0, 0],
        "zoom": 1, "minZoom": 0.7, "maxZoom": 3, "near": 0.1, "far": round(distance * 4, 1),
        "maxPolarAngle": theta, "maxAzimuthAngle": theta,
    }
    if camera_type == "perspective":
        camera["fov"] = round(math.degrees(2 * math.atan(sb.FRAME_HEIGHT / 2 / distance)) * 1.04, 3)

    if not preview and len(layers) >= 3:
        warnings.append("the exploded view separates layers beyond the fill margin; unfilled gaps can show "
                        "while it is open (inspection mode)")
    scene = {
        "schemaVersion": "1.0",
        "sceneId": project.id,
        "name": meta.get("name") or project.id,
        "mode": manifest["mode"],
        "generator": {"name": "papercraft-3d", "version": VERSION, "createdAt": utc_now(),
                      "updatedAt": utc_now(), "revision": 0},
        "source": {"image": meta["source"]["path"], "width": width, "height": height,
                   "sha256": meta["source"]["sha256"]},
        "units": {"pixelsPerUnit": round(ppu, 4)},
        "frame": {"width": round(width / ppu, 4), "height": sb.FRAME_HEIGHT},
        "camera": camera,
        "layers": scene_layers,
        "lighting": night if preset == "night" else day,
        "lightingPresets": {"day": day, "night": night},
        "rendering": {"toneMapping": "neutral", "exposure": 1, "outputColorSpace": "srgb", "antialias": True,
                      "shadows": True, "maxPixelRatio": 2, "clipToFrame": True,
                      "contactShadows": {"enabled": False, "opacity": 0.4, "blur": 2.5}},
        "interaction": {"orbit": True, "zoom": True,
                        "parallax": {"enabled": limits["strength"] > 0, "strength": limits["strength"]},
                        "explodedView": {"enabled": not preview, "spacing": round(spacing * 1.2, 3)}},
        "limits": {"fillMarginPx": margin, "tiltShiftPx": math.ceil(limits["tilt_px"] * 100) / 100,
                   "parallaxShiftPx": math.ceil(limits["parallax_px"] * 100) / 100,
                   "tiltBudget": sb.TILT_BUDGET, "parallaxBudget": sb.PARALLAX_BUDGET,
                   **({"maxTiltDegAnalysis": analysis["camera"]["maxTiltDeg"]}
                      if "maxTiltDeg" in analysis["camera"] else {})},
        "warnings": warnings,
    }
    validate_scene(scene, base_dir=project.dir)
    if project.exists("scene.json"):
        stamp = utc_now().replace(":", "").replace("+", "_")
        shutil.copyfile(project.path("scene.json"), project.path(f"history/scene.{stamp}.json"))
    project.write_json("scene.json", scene)
    project.log("scene_built", layers=len(scene_layers), mode=scene["mode"], preset=preset, camera=camera_type)
    return scene, {"emitters": emitter_report}


def _summary(project: Project, scene: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    cam = scene["camera"]
    viewer_hint = (
        "In the plugin's viewer folder: npm install, then start the dev server with PAPERCRAFT_PROJECT set to "
        "the project directory and open http://localhost:5173/?scene=project/scene.json"
    )
    return {
        "project_id": project.id,
        "scene_path": str(project.path("scene.json")),
        "mode": scene["mode"],
        "layers": [{"id": l["id"], "depth": l["depth"], "parallaxFactor": l["parallaxFactor"],
                    "glow": "emissiveMap" in l["material"]} for l in scene["layers"]],
        "camera": {"type": cam["type"], "max_tilt_deg": round(math.degrees(cam["maxPolarAngle"]), 2)},
        "parallax_strength": scene["interaction"]["parallax"]["strength"],
        "limits": scene["limits"],
        "lighting": {"active": scene["lighting"]["preset"], "presets": list(scene["lightingPresets"]),
                     "key_color": scene["lighting"]["key"]["color"]},
        "emitters": report["emitters"],
        "preview": {
            "how": viewer_hint,
            "powershell": f'$env:PAPERCRAFT_PROJECT="{project.dir}"; npm run dev',
            "bash": f'PAPERCRAFT_PROJECT="{project.dir}" npm run dev',
            "url": "http://localhost:5173/?scene=project/scene.json",
        },
        "next_step": "papercraft_generate_plan",
    }
