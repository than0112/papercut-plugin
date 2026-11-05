"""Scene / analysis / layers JSON Schema contract."""

from __future__ import annotations

import copy
import json

import pytest

from conftest import ROOT, plugin  # noqa: F401
from papercraft_hermes.core.errors import ErrorCode, PaperCraftError
from papercraft_hermes.core.schemas import load_schema, schema_errors, validate_scene

TEMPLATE = json.loads((ROOT / "templates" / "scene_template.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["scene", "analysis", "layers"])
def test_schemas_are_valid(name):
    load_schema(name)


def test_template_is_valid():
    assert schema_errors("scene", TEMPLATE) == []
    validate_scene(TEMPLATE)


def test_fixture_scene_is_valid_with_assets(fixture_dir):
    scene = json.loads((fixture_dir / "scene.json").read_text())
    validate_scene(scene, base_dir=fixture_dir)
    assert len(scene["layers"]) >= 3
    assert all(layer["provenance"]["method"] == "synthetic-fixture" for layer in scene["layers"])


def test_committed_viewer_fixture_is_valid():
    d = ROOT / "viewer" / "public" / "scenes" / "fixture"
    validate_scene(json.loads((d / "scene.json").read_text()), base_dir=d)


def test_fixture_layers_are_really_separate(fixture_dir):
    """Each ground-truth layer has its own transparent region (not copies of the full image)."""
    from PIL import Image

    coverages = []
    for layer in json.loads((fixture_dir / "scene.json").read_text())["layers"]:
        alpha = Image.open(fixture_dir / layer["asset"]).getchannel("A")
        hist = alpha.histogram()
        coverages.append(sum(hist[128:]) / (alpha.width * alpha.height))
    assert coverages[0] == pytest.approx(1.0)  # sky is opaque
    assert all(0.05 < c < 0.95 for c in coverages[1:])
    assert len(set(round(c, 3) for c in coverages)) == len(coverages)


@pytest.mark.parametrize("mutate,expect", [
    (lambda s: s.update(schemaVersion="2.0"), "schemaVersion"),
    (lambda s: s["lighting"]["key"].update(color="orange"), "lighting/key/color"),
    (lambda s: s["layers"][0].update(asset="../../etc/passwd"), "layers/0/asset"),
    (lambda s: s["layers"][0].update(asset="https://evil.example/x.png"), "layers/0/asset"),
    (lambda s: s["layers"][0].update(asset="/abs/x.png"), "layers/0/asset"),
    (lambda s: s["layers"][0].update(unknown=1), "layers/0"),
    (lambda s: s["lighting"]["key"].update(intensity=-1), "lighting/key/intensity"),
    (lambda s: s.update(mode="3d-mesh"), "mode"),
])
def test_schema_rejects(mutate, expect):
    scene = copy.deepcopy(TEMPLATE)
    mutate(scene)
    errors = schema_errors("scene", scene)
    assert any(e.startswith(expect) for e in errors), errors


def test_semantic_duplicate_order():
    scene = copy.deepcopy(TEMPLATE)
    scene["layers"][1]["order"] = scene["layers"][0]["order"]
    with pytest.raises(PaperCraftError) as exc:
        validate_scene(scene)
    assert exc.value.code is ErrorCode.SCENE_INVALID


def test_layered_scene_cannot_hide_preview_layers():
    scene = copy.deepcopy(TEMPLATE)
    scene["layers"][0]["provenance"]["source"] = "preview"
    with pytest.raises(PaperCraftError):
        validate_scene(scene)


def test_missing_assets_reported(tmp_path):
    with pytest.raises(PaperCraftError) as exc:
        validate_scene(TEMPLATE, base_dir=tmp_path)
    assert exc.value.code is ErrorCode.ASSET_MISSING
