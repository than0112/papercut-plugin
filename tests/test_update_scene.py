"""Phase 6: Scene Patch via papercraft_update_scene."""

from __future__ import annotations

import hashlib
import json
import math

import pytest

from conftest import call
from papercraft_hermes.core import scene_patch, spec
from test_build_scene import SUN, _segmented

NIGHT_REQUEST = "把場景改成夜間，使用冷色月光，降低環境亮度。"


def _ready(tmp_path, fixture_dir, monkeypatch, *, with_spec=True):
    ctx, pid, pdir = _segmented(tmp_path, fixture_dir, monkeypatch, emitters=[SUN])
    assert call(ctx, "papercraft_build_scene", {"project_id": pid})["ok"]
    if with_spec:
        assert call(ctx, "papercraft_generate_plan", {"project_id": pid})["ok"]
    return ctx, pid, pdir


def _hashes(pdir):
    files = [*pdir.joinpath("assets").glob("*.png"), *pdir.joinpath("masks").glob("*.png"), *pdir.joinpath("source").iterdir()]
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


def test_night_request(tmp_path, fixture_dir, monkeypatch):
    """Test plan, test 5: night, cold moonlight, lower ambient."""
    ctx, pid, pdir = _ready(tmp_path, fixture_dir, monkeypatch)
    before = _hashes(pdir)
    old = json.loads((pdir / "scene.json").read_text())
    patch = [
        {"op": "replace", "path": "/lighting", "value": {"$preset": "night"}},
        {"op": "replace", "path": "/lighting/key/color", "value": "#a8c4ff"},
        {"op": "replace", "path": "/lighting/ambient/intensity", "value": 0.45},
    ]
    res = call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": NIGHT_REQUEST, "patch": patch})
    assert res["ok"], res
    scene = json.loads((pdir / "scene.json").read_text())
    assert scene["generator"]["revision"] == 1 and res["data"]["revision"] == 1
    lit = scene["lighting"]
    assert lit["key"]["color"] == "#a8c4ff" and lit["ambient"]["intensity"] == 0.45 and "rim" in lit
    assert "colorTemperatureK" not in lit["key"], "stale colour temperature removed"
    assert lit["preset"] == "custom" and lit["ambient"]["intensity"] < old["lighting"]["ambient"]["intensity"]
    assert any("colorTemperatureK removed" in a for a in res["data"]["adjustments"])

    assert _hashes(pdir) == before, "assets, masks and source are never touched"
    assert json.loads((pdir / "history" / "scene.rev0.json").read_text()) == old
    log = [json.loads(l) for l in (pdir / "history" / "patches.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["instruction"] == NIGHT_REQUEST and log[-1]["revision"] == 1

    md = (pdir / "DESIGN_SPEC.md").read_text(encoding="utf-8")
    assert spec.spec_is_current(md, pdir / "scene.json") and spec.verify_spec(md, scene) == []
    assert "`#a8c4ff`" in md and "## G. 變更紀錄" in md and NIGHT_REQUEST in md
    assert json.loads((pdir / "status.json").read_text())["stages"]["update_scene"]["state"] == "succeeded"


def test_more_parallax_lowers_tilt(tmp_path, fixture_dir, monkeypatch):
    """Plan example: make the foreground move more -> parallax up, tilt lowered to keep gaps hidden."""
    ctx, pid, pdir = _ready(tmp_path, fixture_dir, monkeypatch, with_spec=False)
    old = json.loads((pdir / "scene.json").read_text())
    want = round(old["interaction"]["parallax"]["strength"] * 1.8, 4)
    res = call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "讓前景移動幅度更加明顯",
                                                 "patch": [{"op": "replace", "path": "/interaction/parallax/strength", "value": want}]})
    assert res["ok"], res
    scene = json.loads((pdir / "scene.json").read_text())
    assert scene["interaction"]["parallax"]["strength"] == want
    assert scene["camera"]["maxPolarAngle"] < old["camera"]["maxPolarAngle"]
    assert any("tilt limit lowered" in a for a in res["data"]["adjustments"])
    tilt_px, par_px = scene_patch.shifts(scene)
    assert tilt_px + par_px <= scene["limits"]["fillMarginPx"] + 1e-6
    assert scene["limits"]["parallaxShiftPx"] >= par_px and scene["limits"]["tiltShiftPx"] >= tilt_px
    assert any("no DESIGN_SPEC.md yet" in w for w in res["warnings"])


def test_tilt_and_parallax_both_too_high_is_refused(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _ready(tmp_path, fixture_dir, monkeypatch, with_spec=False)
    patch = [{"op": "replace", "path": "/interaction/parallax/strength", "value": 1.5},
             {"op": "replace", "path": "/camera/maxPolarAngle", "value": 0.6}]
    before = (pdir / "scene.json").read_bytes()
    res = call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "x", "patch": patch})
    assert res["error"]["code"] == "scene_invalid" and "respect_limits" in res["error"]["message"]
    assert (pdir / "scene.json").read_bytes() == before
    res = call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "x", "patch": patch, "respect_limits": False})
    assert res["ok"] and any("limits exceeded" in w for w in res["warnings"])


def test_layer_by_id_backlight_and_dry_run(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _ready(tmp_path, fixture_dir, monkeypatch, with_spec=False)
    patch = [{"op": "add", "path": "/lighting/rim", "value": {"color": "#7fa0ff", "intensity": 1.4, "position": [0, 2, -4]}},
             {"op": "replace", "path": "/layers/foreground/material/tint", "value": "#f0e8ff"}]
    before = (pdir / "scene.json").read_bytes()
    dry = call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "加背光", "patch": patch, "dry_run": True})
    assert dry["ok"], dry
    assert dry["data"]["dry_run"] and (pdir / "scene.json").read_bytes() == before
    assert {d["path"] for d in dry["data"]["diff"]} == {"/lighting/rim", "/layers/4/material/tint"}
    assert {d["op"] for d in dry["data"]["diff"]} == {"add"}, "replace of a missing optional field is recorded as add"
    assert call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "加背光", "patch": patch})["ok"]
    scene = json.loads((pdir / "scene.json").read_text())
    assert scene["lighting"]["rim"]["color"] == "#7fa0ff" and scene["layers"][4]["material"]["tint"] == "#f0e8ff"


@pytest.mark.parametrize("patch,needle", [
    ([{"op": "replace", "path": "/layers/0/asset", "value": "../../evil.png"}], "cannot be edited"),
    ([{"op": "replace", "path": "/source/image", "value": "x.png"}], "cannot be edited"),
    ([{"op": "replace", "path": "/layers/sky/provenance/source", "value": "manual"}], "cannot be edited"),
    ([{"op": "remove", "path": "/layers/1"}], "cannot be edited"),
    ([{"op": "replace", "path": "/layers/nope/depth", "value": 1}], "unknown layer id"),
    ([{"op": "replace", "path": "/lighting", "value": {"$preset": "dusk"}}], "unknown lighting preset"),
    ([{"op": "replace", "path": "/lighting/key/nothing/deeper", "value": 1}], "does not exist"),
    ([{"op": "explode", "path": "/name", "value": 1}], "invalid patch operation"),
])
def test_protected_and_invalid_paths(tmp_path, fixture_dir, monkeypatch, patch, needle):
    ctx, pid, pdir = _ready(tmp_path, fixture_dir, monkeypatch, with_spec=False)
    before = (pdir / "scene.json").read_bytes()
    res = call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "x", "patch": patch})
    assert res["ok"] is False and needle in res["error"]["message"], res
    assert (pdir / "scene.json").read_bytes() == before, "nothing written on refusal"


@pytest.mark.parametrize("patch,code", [
    ([{"op": "replace", "path": "/lighting/key/color", "value": "blue"}], "scene_invalid"),
    ([{"op": "replace", "path": "/lighting/key/intensity", "value": -2}], "scene_invalid"),
    ([{"op": "replace", "path": "/layers/foreground/depth", "value": 0.1}], "scene_invalid"),  # behind farther layers
])
def test_schema_and_semantic_rejections(tmp_path, fixture_dir, monkeypatch, patch, code):
    ctx, pid, pdir = _ready(tmp_path, fixture_dir, monkeypatch, with_spec=False)
    res = call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "x", "patch": patch})
    assert res["error"]["code"] == code
    assert json.loads((pdir / "scene.json").read_text())["generator"]["revision"] == 0


def test_atomic_all_or_nothing(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _ready(tmp_path, fixture_dir, monkeypatch, with_spec=False)
    patch = [{"op": "replace", "path": "/lighting/key/intensity", "value": 1.0},
             {"op": "replace", "path": "/layers/0/mask", "value": "x.png"}]
    before = (pdir / "scene.json").read_bytes()
    assert call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "x", "patch": patch})["ok"] is False
    assert (pdir / "scene.json").read_bytes() == before


def test_revert(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _ready(tmp_path, fixture_dir, monkeypatch)
    original = json.loads((pdir / "scene.json").read_text())
    assert call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "暗一點",
                                                 "patch": [{"op": "replace", "path": "/lighting/key/intensity", "value": 1.0}]})["ok"]
    res = call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "還原", "revert_to": 0})
    assert res["ok"] and res["data"]["revision"] == 2
    scene = json.loads((pdir / "scene.json").read_text())
    assert scene["lighting"] == original["lighting"] and scene["generator"]["revision"] == 2
    md = (pdir / "DESIGN_SPEC.md").read_text(encoding="utf-8")
    assert "還原" in md and "暗一點" in md
    assert call(ctx, "papercraft_update_scene", {"project_id": pid, "instruction": "x", "revert_to": 7})["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("args", [
    {"instruction": "x"},
    {"instruction": "x", "patch": [{"op": "remove", "path": "/name"}], "revert_to": 0},
    {"instruction": "", "patch": [{"op": "remove", "path": "/name"}]},
    {"instruction": "x", "patch": [{"op": "remove", "path": "/name"}], "dry_run": "yes"},
])
def test_argument_validation(tmp_path, fixture_dir, monkeypatch, args):
    ctx, pid, _ = _ready(tmp_path, fixture_dir, monkeypatch, with_spec=False)
    assert call(ctx, "papercraft_update_scene", {"project_id": pid, **args})["error"]["code"] == "invalid_argument"


def test_shifts_math():
    scene = {"layers": [{"order": i, "depth": d, "parallaxFactor": f} for i, (d, f) in enumerate([(0, 0), (1, 0.25), (3, 1)])],
             "units": {"pixelsPerUnit": 100}, "camera": {"maxPolarAngle": 0.1, "maxAzimuthAngle": 0.05},
             "interaction": {"parallax": {"enabled": True, "strength": 0.2}}}
    tilt, par = scene_patch.shifts(scene)
    assert tilt == pytest.approx(2 * math.sin(0.1) * 100) and par == pytest.approx(0.2 * 0.75 * 100)
