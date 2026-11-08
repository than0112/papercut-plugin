"""Scene Patch: validated, atomic edits of scene.json.

The calling agent turns a request ("make it night with cold moonlight") into
JSON-Patch-style operations. This module
- resolves ``/layers/<id>/...`` to array indices and ``{"$preset": name}`` values,
- refuses paths outside an allow-list (assets, masks, source image, ids, provenance
  and derived data can never be edited),
- applies all operations to a copy (all or nothing),
- keeps the scene consistent (colour temperature dropped when only the key colour
  changes, lighting preset label set to "custom" when lighting is edited),
- re-checks the safety bounds and, if needed, rebalances camera tilt vs. parallax
  (only the quantity the patch did not ask for is reduced), reporting each adjustment.
Schema and semantic validation happen in the caller (core.schemas.validate_scene).
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from .errors import ErrorCode, PaperCraftError

ALLOWED = [re.compile(p) for p in (
    r"^/name$",
    r"^/lighting(/.*)?$",
    r"^/lightingPresets/[a-z0-9][a-z0-9_-]{0,63}(/.*)?$",
    r"^/interaction(/.*)?$",
    r"^/rendering/(toneMapping|exposure|shadows|maxPixelRatio|clipToFrame|contactShadows(/.*)?)$",
    r"^/camera/(zoom|minZoom|maxZoom|maxPolarAngle|maxAzimuthAngle|fov)$",
    r"^/layers/\d+/(name|depth|offset(/[01])?|scale|visible|parallaxFactor|castShadow|receiveShadow)$",
    r"^/layers/\d+/material/(roughness|metalness|opacity|tint|emissiveIntensity|paperTexture(/.*)?)$",
)]
TILT_PATHS = re.compile(r"^/camera/(maxPolarAngle|maxAzimuthAngle)$")
PARALLAX_PATHS = re.compile(r"^/interaction(/parallax(/.*)?)?$|^/layers/\d+/parallaxFactor$")
GEOMETRY_PATHS = re.compile(r"^/layers/\d+/depth$")


def _tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"path {pointer!r} must be a JSON Pointer starting with '/'.")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _pointer(tokens: list[str]) -> str:
    return "/" + "/".join(t.replace("~", "~0").replace("/", "~1") for t in tokens)


def resolve(scene: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    """Normalise one operation: layer ids -> indices, $preset values -> lighting objects."""
    if not isinstance(op, dict) or op.get("op") not in ("replace", "add", "remove") or not isinstance(op.get("path"), str):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"invalid patch operation: {op!r}")
    tokens = _tokens(op["path"])
    if len(tokens) >= 2 and tokens[0] == "layers" and not tokens[1].isdigit():
        ids = [layer["id"] for layer in scene["layers"]]
        if tokens[1] not in ids:
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"unknown layer id {tokens[1]!r} in {op['path']}",
                                  details={"layers": ids})
        tokens[1] = str(ids.index(tokens[1]))
    out = {"op": op["op"], "path": _pointer(tokens)}
    if op["op"] != "remove":
        if "value" not in op:
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"{op['op']} {op['path']} needs a value")
        value = op["value"]
        if isinstance(value, dict) and set(value) == {"$preset"}:
            name = value["$preset"]
            presets = scene.get("lightingPresets", {})
            if not (tokens[0] in ("lighting",) or tokens[0] == "lightingPresets") or name not in presets:
                raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"unknown lighting preset {name!r}",
                                      details={"presets": list(presets)})
            value = copy.deepcopy(presets[name])
        out["value"] = value
    if not any(p.match(out["path"]) for p in ALLOWED):
        raise PaperCraftError(
            ErrorCode.INVALID_ARGUMENT,
            f"{op['path']} cannot be edited (assets, masks, the source image, ids, provenance and derived data "
            "are protected; run papercraft_segment / papercraft_build_scene to change those).",
            details={"path": op["path"]},
        )
    return out


def _apply_one(doc: Any, op: dict[str, Any]) -> tuple[Any, Any]:
    """Apply in place; return (old value or None, new value or None)."""
    tokens = _tokens(op["path"])
    parent = doc
    for t in tokens[:-1]:
        if isinstance(parent, list):
            if not t.isdigit() or int(t) >= len(parent):
                raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"{op['path']}: index {t} out of range")
            parent = parent[int(t)]
        elif isinstance(parent, dict) and t in parent:
            parent = parent[t]
        else:
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"{op['path']}: '{t}' does not exist")
    last = tokens[-1]
    if isinstance(parent, list):
        if not last.isdigit() or int(last) >= len(parent):
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"{op['path']}: arrays can only be edited in place")
        i = int(last)
        if op["op"] == "remove":
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"{op['path']}: array items cannot be removed")
        old, parent[i] = parent[i], copy.deepcopy(op["value"])
        return old, parent[i]
    if not isinstance(parent, dict):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"{op['path']}: parent is not an object")
    if op["op"] == "remove":
        if last not in parent:
            raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, f"{op['path']} does not exist")
        return parent.pop(last), None
    if op["op"] == "replace" and last not in parent:
        op["op"] = "add"  # agent-friendly: setting an optional field that is still at its default
    old = parent.get(last)
    parent[last] = copy.deepcopy(op["value"])
    return old, parent[last]


def _floor(value: float, digits: int = 4) -> float:
    return math.floor(value * 10 ** digits) / 10 ** digits


def shifts(scene: dict[str, Any]) -> tuple[float, float]:
    """(tilt_px, parallax_px): largest relative shift between a completed-but-limited layer and a nearer one."""
    layers = sorted(scene["layers"], key=lambda l: l["order"])[1:]  # the back layer is complete everywhere
    if len(layers) < 2:
        return 0.0, 0.0
    ppu = scene["units"]["pixelsPerUnit"]
    cam = scene["camera"]
    theta = max(cam.get("maxPolarAngle", 0.35), cam.get("maxAzimuthAngle", 0.35))
    depths = [l["depth"] for l in layers]
    factors = [l.get("parallaxFactor", 1) for l in layers]
    par = scene["interaction"].get("parallax", {})
    strength = par.get("strength", 0.15) if par.get("enabled", True) else 0.0
    return (max(depths) - min(depths)) * math.sin(theta) * ppu, strength * (max(factors) - min(factors)) * ppu


def apply_patch(scene: dict[str, Any], patch: list[dict[str, Any]], *, respect_limits: bool = True
                ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Return (new scene, diff, adjustments, resolved ops). Raises on any invalid operation."""
    if not isinstance(patch, list) or not patch:
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "patch must be a non-empty list of operations.")
    new = copy.deepcopy(scene)
    resolved = [resolve(new, op) for op in patch]
    diff, adjustments = [], []
    for op in resolved:
        old, value = _apply_one(new, op)  # may turn 'replace' of a missing member into 'add' 
        diff.append({"path": op["path"], "op": op["op"], "old": old, "new": value})
    touched = [op["path"] for op in resolved]

    # Consistency: a hand-set key colour no longer matches the old colour temperature.
    for prefix in ["/lighting"] + [f"/lightingPresets/{n}" for n in new.get("lightingPresets", {})]:
        if f"{prefix}/key/color" in touched and f"{prefix}/key/colorTemperatureK" not in touched:
            node = new
            for t in _tokens(prefix + "/key"):
                node = node.get(t, {}) if isinstance(node, dict) else {}
            if isinstance(node, dict) and node.pop("colorTemperatureK", None) is not None:
                adjustments.append(f"{prefix}/key/colorTemperatureK removed: it described the previous key colour")
    if any(p.startswith("/lighting/") or p == "/lighting" for p in touched) and "/lighting/preset" not in touched:
        lit = new["lighting"]
        presets = new.get("lightingPresets", {})
        match = next((name for name, preset in presets.items() if preset == {**lit, "preset": preset.get("preset")}), None)
        label = match or "custom"
        if lit.get("preset") != label:
            lit["preset"] = label
            adjustments.append(f"/lighting/preset set to '{label}'")

    _rebalance(new, touched, respect_limits, adjustments)
    return new, diff, adjustments, resolved


def _rebalance(scene: dict[str, Any], touched: list[str], respect_limits: bool, adjustments: list[str]) -> None:
    limits = scene.get("limits")
    if not limits:
        return
    margin = limits["fillMarginPx"]
    tilt_px, par_px = shifts(scene)
    tilt_asked = any(TILT_PATHS.match(p) for p in touched)
    par_asked = any(PARALLAX_PATHS.match(p) for p in touched)
    if tilt_px + par_px > margin + 1e-6:
        if not respect_limits:
            scene.setdefault("warnings", []).append(
                f"limits exceeded on request: tilt + parallax shift {tilt_px + par_px:.1f} px > fill margin {margin} px; "
                "unfilled gaps can show at the extremes")
        elif tilt_asked and par_asked:
            raise PaperCraftError(
                ErrorCode.SCENE_INVALID,
                f"Together, the requested tilt and parallax shift layers by {tilt_px + par_px:.1f} px, more than the "
                f"{margin} px that segmentation filled behind them, so gaps would show. Lower one of them, or pass "
                "respect_limits=false to accept visible gaps.",
                details={"tiltShiftPx": round(tilt_px, 2), "parallaxShiftPx": round(par_px, 2), "fillMarginPx": margin},
                retryable=False)
        elif par_asked or (not tilt_asked and par_px <= margin):
            _set_tilt_for(scene, margin - par_px, adjustments)
        else:
            _set_parallax_for(scene, margin - tilt_px, adjustments)
        if respect_limits:
            tilt_px, par_px = shifts(scene)
            if tilt_px + par_px > margin + 1e-6:  # e.g. parallax alone exceeds the margin
                _set_parallax_for(scene, margin - tilt_px, adjustments)
                tilt_px, par_px = shifts(scene)
    limits["tiltShiftPx"] = math.ceil(tilt_px * 100) / 100
    limits["parallaxShiftPx"] = math.ceil(par_px * 100) / 100
    limits["tiltBudget"] = round(min(1.0, tilt_px / margin), 4) if margin else 0.0
    limits["parallaxBudget"] = round(min(1.0, par_px / margin), 4) if margin else 0.0


def _set_tilt_for(scene: dict[str, Any], budget_px: float, adjustments: list[str]) -> None:
    layers = sorted(scene["layers"], key=lambda l: l["order"])[1:]
    span = max(l["depth"] for l in layers) - min(l["depth"] for l in layers)
    ppu = scene["units"]["pixelsPerUnit"]
    cam = scene["camera"]
    old = max(cam.get("maxPolarAngle", 0.35), cam.get("maxAzimuthAngle", 0.35))
    theta = _floor(math.asin(max(0.0, min(1.0, budget_px / (span * ppu))))) if span > 0 else old
    theta = min(theta, old)
    cam["maxPolarAngle"] = cam["maxAzimuthAngle"] = theta
    adjustments.append(f"camera tilt limit lowered from {math.degrees(old):.2f}° to {math.degrees(theta):.2f}° "
                       "so the layers never shift past the filled margin")


def _set_parallax_for(scene: dict[str, Any], budget_px: float, adjustments: list[str]) -> None:
    layers = sorted(scene["layers"], key=lambda l: l["order"])[1:]
    spread = max(l.get("parallaxFactor", 1) for l in layers) - min(l.get("parallaxFactor", 1) for l in layers)
    par = scene["interaction"].setdefault("parallax", {})
    old = par.get("strength", 0.15)
    new = _floor(max(0.0, budget_px) / (spread * scene["units"]["pixelsPerUnit"])) if spread > 0 else old
    new = min(new, old)
    par["strength"] = new
    adjustments.append(f"parallax strength lowered from {old:g} to {new:g} so the layers never shift past the "
                       "filled margin")
