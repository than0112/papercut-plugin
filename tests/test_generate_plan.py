"""Phase 5: DESIGN_SPEC.md generation and its consistency with scene.json."""

from __future__ import annotations

import json
import math
import re

import pytest

from conftest import ROOT, FakeContext, FakeLlm, call, plugin
from papercraft_hermes.core import spec
from test_build_scene import SUN, _segmented


def _built(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _segmented(tmp_path, fixture_dir, monkeypatch, emitters=[SUN])
    assert call(ctx, "papercraft_build_scene", {"project_id": pid})["ok"]
    return ctx, pid, pdir


def _cells(markdown, layer_id):
    row = re.search(rf"^\| \d+ \| `{layer_id}` \|(.*)$", markdown, re.M)
    return [c.strip() for c in row.group(1).split("|")]


def test_spec_matches_scene(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _built(tmp_path, fixture_dir, monkeypatch)
    res = call(ctx, "papercraft_generate_plan", {"project_id": pid})
    assert res["ok"], res
    assert res["data"]["verified_against_scene"] is True
    md = (pdir / "DESIGN_SPEC.md").read_text(encoding="utf-8")
    scene = json.loads((pdir / "scene.json").read_text())

    for heading in ("## A. 視覺概念", "## B. 3D 場景結構", "## C. 光影設計", "## D. 材質與渲染", "## E. 互動功能", "## F. 技術實作"):
        assert heading in md
    for k in spec.SECTIONS:
        assert md.count(f"<!-- auto:{k} -->") == 1 and md.count(f"<!-- /auto:{k} -->") == 1
    assert spec.spec_is_current(md, pdir / "scene.json")

    # Independent check: parse the tables and compare with scene.json.
    for layer in scene["layers"]:
        cells = _cells(md, layer["id"])
        assert float(cells[2]) == pytest.approx(layer["depth"]) and float(cells[3]) == pytest.approx(layer["parallaxFactor"])
    tilt = math.degrees(scene["camera"]["maxPolarAngle"])
    assert f"{tilt:.2f}°" in md
    for preset in scene["lightingPresets"].values():
        assert f"`{preset['key']['color']}`" in md and spec.fmt(preset["key"]["intensity"]) in md
    assert spec.fmt(scene["limits"]["tiltShiftPx"], 2) in md and str(scene["limits"]["fillMarginPx"]) in md
    assert "`assets/sky_emissive.png`" in md
    viewer_pkg = json.loads((ROOT / "viewer" / "package.json").read_text())
    assert f"| three | {viewer_pkg['dependencies']['three']} |" in md
    assert "**尚未實作**" in md, "unimplemented phases are stated honestly"
    assert json.loads((pdir / "status.json").read_text())["stages"]["generate_plan"]["state"] == "succeeded"


def test_english(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _built(tmp_path, fixture_dir, monkeypatch)
    assert call(ctx, "papercraft_generate_plan", {"project_id": pid, "language": "en"})["ok"]
    md = (pdir / "DESIGN_SPEC.md").read_text(encoding="utf-8")
    assert "## A. Visual concept" in md and "## F. Technical implementation" in md and "視覺概念" not in md


def test_user_notes_survive_regeneration(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _built(tmp_path, fixture_dir, monkeypatch)
    assert call(ctx, "papercraft_generate_plan", {"project_id": pid})["ok"]
    path = pdir / "DESIGN_SPEC.md"
    md = path.read_text(encoding="utf-8")
    md = md.replace("<!-- /auto:C -->", "<!-- /auto:C -->\n\n> 客戶要求：燈籠再暖一點。")
    md += "\n我的備註：展覽用 4K 螢幕。\n"
    path.write_text(md, encoding="utf-8")

    assert call(ctx, "papercraft_build_scene", {"project_id": pid, "lighting_preset": "night"})["ok"]
    assert not spec.spec_is_current(path.read_text(encoding="utf-8"), pdir / "scene.json"), "scene changed -> stale"
    res = call(ctx, "papercraft_generate_plan", {"project_id": pid})
    assert res["ok"] and res["data"]["kept_user_notes"] is True
    out = path.read_text(encoding="utf-8")
    assert "客戶要求：燈籠再暖一點。" in out and "我的備註：展覽用 4K 螢幕。" in out
    assert "目前光源: **night**" in out and spec.spec_is_current(out, pdir / "scene.json")


def test_broken_markers_are_backed_up(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _built(tmp_path, fixture_dir, monkeypatch)
    (pdir / "DESIGN_SPEC.md").write_text("# hand-written spec without markers\n", encoding="utf-8")
    res = call(ctx, "papercraft_generate_plan", {"project_id": pid})
    assert res["ok"] and any("history/DESIGN_SPEC." in w for w in res["warnings"])
    backups = list((pdir / "history").glob("DESIGN_SPEC.*.md"))
    assert len(backups) == 1 and "hand-written" in backups[0].read_text(encoding="utf-8")


def test_verify_detects_tampering(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir = _built(tmp_path, fixture_dir, monkeypatch)
    assert call(ctx, "papercraft_generate_plan", {"project_id": pid})["ok"]
    scene = json.loads((pdir / "scene.json").read_text())
    md = (pdir / "DESIGN_SPEC.md").read_text(encoding="utf-8")
    assert spec.verify_spec(md, scene) == []
    layer = scene["layers"][2]
    tampered = md.replace(f"| `{layer['id']}` |", f"| `{layer['id']}` |", 1)
    row = re.search(rf"^\| \d+ \| `{layer['id']}` \|.*$", tampered, re.M).group(0)
    tampered = tampered.replace(row, row.replace(f"| {spec.fmt(layer['depth'])} |", "| 9.99 |", 1))
    assert any("depth" in p for p in spec.verify_spec(tampered, scene))


def test_requires_scene(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, _ = _segmented(tmp_path, fixture_dir, monkeypatch)
    assert call(ctx, "papercraft_generate_plan", {"project_id": pid})["error"]["code"] == "precondition_failed"
    ctx2 = FakeContext(config={"workspace_dir": str(tmp_path / "ws")}, llm=FakeLlm([]))
    plugin.register(ctx2)
    assert call(ctx2, "papercraft_generate_plan", {"project_id": pid, "language": "fr"})["error"]["code"] == "invalid_argument"


def test_fmt():
    assert spec.fmt(0.30000000001) == "0.3" and spec.fmt(1.0) == "1" and spec.fmt(-0.0) == "0"
    assert spec.fmt([0.5, 2, 1.23456]) == "[0.5, 2, 1.2346]" and spec.fmt(True) == "true"
