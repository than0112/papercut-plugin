"""Phase 4: papercraft_build_scene on the segmented synthetic fixture."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from PIL import Image

from conftest import FakeContext, FakeLlm, call, plugin
from papercraft_hermes.core import scene_builder as sb
from papercraft_hermes.core.schemas import schema_errors, validate_scene
from test_segment import Oracle, _analysis_reply

SUN = {"name": "sun", "kind": "sun", "layerId": "sky", "bbox": [0.66, 0.1, 0.82, 0.36], "color": "#f7b267",
       "intensity": 0.5}


def _segmented(tmp_path, fixture_dir, monkeypatch, *, emitters=(), preview=False):
    reply = _analysis_reply(fixture_dir)
    reply["light"]["emitters"] = list(emitters)
    reply["camera"]["maxTiltDeg"] = 20
    replies = [reply, RuntimeError("down")] if preview else [reply, Oracle(fixture_dir, monkeypatch)]
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws")}, llm=FakeLlm(replies))
    plugin.register(ctx)
    pid = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})["data"]["project_id"]
    seg = call(ctx, "papercraft_segment", {"project_id": pid, "allow_preview_fallback": preview})
    assert seg["ok"], seg
    return ctx, pid, tmp_path / "ws" / pid


def test_build_scene(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _segmented(tmp_path, fixture_dir, monkeypatch, emitters=[SUN])
    res = call(ctx, "papercraft_build_scene", {"project_id": pid})
    assert res["ok"], res
    scene = json.loads((pdir / "scene.json").read_text())
    assert schema_errors("scene", scene) == []
    validate_scene(scene, base_dir=pdir)  # every referenced file exists
    assert json.loads((pdir / "status.json").read_text())["stages"]["build_scene"]["state"] == "succeeded"

    layers = scene["layers"]
    depths = [l["depth"] for l in layers]
    assert depths[0] == 0 and all(b > a for a, b in zip(depths, depths[1:]))
    assert [l["parallaxFactor"] for l in layers][0] == 0 and layers[-1]["parallaxFactor"] == 1
    assert scene["frame"]["height"] == 8 and scene["frame"]["width"] == pytest.approx(8 * 480 / 320, rel=1e-3)
    assert scene["source"]["image"] == "source/source.png"

    # The bounds: recompute the shifts from the scene itself and check them against the fill margin.
    lim, ppu = scene["limits"], scene["units"]["pixelsPerUnit"]
    tilt_px = (depths[-1] - depths[1]) * math.sin(scene["camera"]["maxPolarAngle"]) * ppu
    para_px = scene["interaction"]["parallax"]["strength"] * (layers[-1]["parallaxFactor"] - layers[1]["parallaxFactor"]) * ppu
    assert tilt_px == pytest.approx(lim["tiltShiftPx"], abs=0.05)
    assert para_px == pytest.approx(lim["parallaxShiftPx"], abs=0.05)
    assert tilt_px <= sb.TILT_BUDGET * lim["fillMarginPx"] + 1e-6
    assert para_px <= sb.PARALLAX_BUDGET * lim["fillMarginPx"] + 1e-6
    assert scene["camera"]["maxPolarAngle"] <= math.radians(20) + 1e-9

    day, night = scene["lightingPresets"]["day"], scene["lightingPresets"]["night"]
    assert scene["lighting"] == day and day["key"]["position"][0] < 0, "light from the left (azimuth -30)"
    assert night["emissiveScale"] > day["emissiveScale"] and "rim" in night

    sky = layers[0]
    assert sky["material"]["emissiveMap"] == "assets/sky_emissive.png"
    glow = np.asarray(Image.open(pdir / sky["material"]["emissiveMap"]).convert("L")) > 8
    owned = np.asarray(Image.open(pdir / sky["mask"])) > 0
    h, w = glow.shape
    box = np.zeros_like(glow)
    # emitter box as the builder computes it (floor start, ceil end) plus the 2 px soft edge
    box[int(0.1 * h) - 2:math.ceil(0.36 * h) + 2, int(0.66 * w) - 2:math.ceil(0.82 * w) + 2] = True
    assert glow.any() and not (glow & ~box).any(), "glow stays inside the emitter box (+2 px soft edge)"
    assert (glow & ~owned).sum() < 0.05 * glow.sum(), "glow only on the layer's own pixels (plus blur)"
    assert res["data"]["emitters"][0]["pixels"] > 0 and res["data"]["preview"]["url"].endswith("scene.json")
    assert all("emissiveMap" not in l["material"] for l in layers[1:])


def test_rebuild_keeps_history_and_night_preset(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _segmented(tmp_path, fixture_dir, monkeypatch)
    assert call(ctx, "papercraft_build_scene", {"project_id": pid})["ok"]
    res = call(ctx, "papercraft_build_scene", {"project_id": pid, "lighting_preset": "night"})
    assert res["ok"]
    assert len(list((pdir / "history").glob("scene.*.json"))) == 1
    scene = json.loads((pdir / "scene.json").read_text())
    assert scene["lighting"]["preset"] == "night"


def test_perspective_scales_nearer_layers(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _segmented(tmp_path, fixture_dir, monkeypatch)
    res = call(ctx, "papercraft_build_scene", {"project_id": pid, "camera": "perspective"})
    assert res["ok"], res
    scene = json.loads((pdir / "scene.json").read_text())
    scales = [l["scale"] for l in scene["layers"]]
    assert scales[0] == 1 and all(b < a for a, b in zip(scales, scales[1:]))
    assert 5 < scene["camera"]["fov"] < 60


def test_preview_scene(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _segmented(tmp_path, fixture_dir, monkeypatch, preview=True)
    res = call(ctx, "papercraft_build_scene", {"project_id": pid})
    assert res["ok"], res
    scene = json.loads((pdir / "scene.json").read_text())
    assert scene["mode"] == "preview" and len(scene["layers"]) == 1
    assert scene["interaction"]["parallax"]["enabled"] is False
    assert any("示意預覽" in w for w in scene["warnings"])
    assert json.loads((pdir / "status.json").read_text())["stages"]["build_scene"]["state"] == "degraded"


def test_requires_segmentation(tmp_path, fixture_dir):
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws")}, llm=FakeLlm([_analysis_reply(fixture_dir)]))
    plugin.register(ctx)
    pid = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})["data"]["project_id"]
    assert call(ctx, "papercraft_build_scene", {"project_id": pid})["error"]["code"] == "precondition_failed"


@pytest.mark.parametrize("args", [{"lighting_preset": "dusk"}, {"camera": "fisheye"}, {"depth_spacing": 0},
                                  {"depth_spacing": True}])
def test_argument_validation(tmp_path, fixture_dir, monkeypatch, args):
    ctx, pid, _ = _segmented(tmp_path, fixture_dir, monkeypatch)
    assert call(ctx, "papercraft_build_scene", {"project_id": pid, **args})["error"]["code"] == "invalid_argument"


def test_units():
    assert sb.kelvin_to_hex(6600) in ("#ffffff", "#fffefa", "#fffffe") or sb.kelvin_to_hex(6600).startswith("#ff")
    warm, cool = sb.kelvin_to_hex(3000), sb.kelvin_to_hex(9000)
    assert int(warm[5:7], 16) < int(cool[5:7], 16), "warm light has less blue"
    x, y, z = sb.light_position(-90, 0)
    assert x < -9 and abs(y) < 1e-6 and z == 1.0, "from the left; key kept in front of the paper"
    assert sb.layer_depths([0, 0.5, 0.4, 1], 0.6) == sb.layer_depths([0, 1 / 3, 2 / 3, 1], 0.6)
    two = sb.view_limits([0, 1], 100, 50, None, False)
    assert two["tilt_px"] == 0 and two["theta"] == pytest.approx(math.radians(sb.DEFAULT_MAX_TILT_DEG))


def test_camera_note_when_analysis_suggests_another(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _segmented(tmp_path, fixture_dir, monkeypatch)
    analysis = json.loads((pdir / "analysis.json").read_text(encoding="utf-8"))
    analysis["camera"]["type"] = "perspective"
    (pdir / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    cam = call(ctx, "papercraft_build_scene", {"project_id": pid})["data"]["camera"]
    assert cam["type"] == "orthographic" and "perspective" in cam["note"]
    cam = call(ctx, "papercraft_build_scene", {"project_id": pid, "camera": "perspective"})["data"]["camera"]
    assert "note" not in cam


def _lamp_scene():
    """A lantern: amber ring around a white-hot core, next to white paper and a detached pale blob."""
    img = np.zeros((100, 160, 3), np.uint8)
    img[:] = (200, 120, 150)                                   # pink sky
    img[20:80, 20:80] = (242, 178, 70)                         # amber glass
    img[35:65, 35:65] = (250, 252, 215)                        # white-hot core, enclosed by amber
    img[45:55, 45:55] = (255, 255, 255)                        # pure white centre: no hue at all
    img[20:80, 80:110] = (240, 238, 236)                       # white paper wall touching the lamp
    img[40:55, 120:140] = (252, 250, 212)                      # pale-yellow blob, not connected to amber
    owners = {"lamp": np.ones(img.shape[:2], bool)}
    emitter = {"name": "lantern", "kind": "lamp", "layerId": "lamp", "bbox": [0.0, 0.0, 1.0, 1.0],
               "color": "#f2b246", "intensity": 0.7}
    return img, owners, emitter


def test_emitter_hot_core_glows_but_paper_does_not():
    img, owners, emitter = _lamp_scene()
    maps, _, report, _ = sb.emissive_maps(img, owners, [emitter])
    glow = maps["lamp"].max(axis=-1) > 8
    assert glow[25:75, 25:75].all(), "amber ring and its white-hot core both glow"
    assert not glow[25:75, 85:110].any(), "untinted white paper next to the lamp stays dark"
    assert not glow[42:53, 122:138].any(), "a pale blob not connected to the emitter colour stays dark"
    assert report[0]["hotCorePixels"] >= 30 * 30 - 4 * 30
    assert 0 < report[0]["layerFraction"] == report[0]["boxFraction"] < 1


def test_grey_emitter_hot_core():
    """A near-white emitter (moon) has no hue: only brightness and connection decide."""
    img = np.zeros((60, 60, 3), np.uint8)
    img[:] = (20, 30, 60)
    img[10:50, 10:50] = (200, 210, 225)
    img[20:40, 20:40] = (255, 255, 255)
    moon = {"name": "moon", "kind": "moon", "layerId": "sky", "bbox": [0, 0, 1, 1], "color": "#c8d2e1"}
    maps, _, report, _ = sb.emissive_maps(img, {"sky": np.ones((60, 60), bool)}, [moon])
    glow = maps["sky"].max(axis=-1) > 8
    assert glow[12:48, 12:48].all() and not glow[:5].any()
