"""Regression cases recorded from real runs of the Claude Code plugin (Claude as the vision model).

tests/fixtures/replies/claude_<case>.json holds the analysis reply and the Set-of-Mark labels exactly as
Claude sent them for three real paper-cut images (lighthouse harbour, Japanese village, night forest).
The images themselves are not stored. tests/fixtures/emitters/<case>_<i>/ holds a small crop around one
glowing element of those images, the owning layer's mask for that crop, and the emitter as analysed
(bbox re-expressed relative to the crop). They pin the glow maps found on real art.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage as ndi
from skimage import color as skcolor

from conftest import ROOT
from papercraft_hermes.core import scene_builder as sb
from papercraft_hermes.core.analysis import finalize, review_analysis
from papercraft_hermes.core.segment_backends import parse_labels

FIXTURES = ROOT / "tests" / "fixtures"
CASES = ["lighthouse", "village", "forest"]

# Share of the emitter's own-layer pixels that glow, as measured when the case was recorded, minus a
# margin. A drop below the floor means the glow map lost real lights.
LAYER_FRACTION_FLOOR = {
    "lighthouse_0": 0.40,  # lamp room: amber panes + white-hot centre (was 0.33 before the hot-core fix)
    "forest_0": 0.70,      # full moon
    "forest_1": 0.37,      # arched cottage window
    "forest_2": 0.45,      # wall lantern by the door
    "forest_3": 0.44,      # bridge lantern
    "forest_4": 0.15,      # lily-of-the-valley lamp: only the rim and the centre glow, the petals do not
    "village_0": 0.65,     # stone lantern's fire window
    "village_1": 0.38,     # small paper lantern
    "village_2": 0.58,     # tall paper lantern
    "village_3": 0.57,     # shoji window
}


def _case(name: str) -> dict:
    return json.loads((FIXTURES / "replies" / f"claude_{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", CASES)
def test_recorded_analysis_passes_review(name):
    reply = _case(name)["reply"]
    review = review_analysis(reply, max_layers=8)
    assert review.errors == [], review.errors
    doc = finalize(reply, review)
    ids = {layer["id"] for layer in doc["layers"]}
    for emitter in doc["light"]["emitters"]:
        assert emitter["layerId"] in ids, emitter["name"]
        x0, y0, x1, y1 = emitter["bbox"]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, emitter["name"]


def test_recorded_analysis_respects_max_layers():
    reply = _case("forest")["reply"]
    assert len(reply["layers"]) == 8
    assert review_analysis(reply, max_layers=7).errors, "8 layers must be rejected when max_layers is 7"


@pytest.mark.parametrize("name", CASES)
def test_recorded_labels_parse(name):
    case = _case(name)
    ids = [layer["id"] for layer in sorted(case["reply"]["layers"], key=lambda l: l["order"])]
    first = case["labels"]["1"]
    numbers = [int(k) for k in first["assignments"]]
    mapping, uncertain, problems = parse_labels(first, max(numbers), ids)
    assert problems == [] and len(mapping) == max(numbers)
    assert uncertain, "every recorded run sent mixed regions to the second round"
    refine = case["labels"]["refine"]
    n = max(int(k) for k in refine["assignments"])
    assert parse_labels(refine, n, ids)[2] == []


def _emitter_case(path: Path):
    meta = json.loads((path / "emitter.json").read_text(encoding="utf-8"))
    rgb = np.asarray(Image.open(path / "crop.png").convert("RGB"))
    owner = np.asarray(Image.open(path / "mask.png")) > 0
    emitter = meta["emitter"]
    return rgb, {emitter["layerId"]: owner}, emitter


EMITTER_DIRS = sorted(p.name for p in (FIXTURES / "emitters").iterdir())


def test_every_emitter_fixture_has_a_floor():
    assert set(EMITTER_DIRS) == set(LAYER_FRACTION_FLOOR)


@pytest.mark.parametrize("name", EMITTER_DIRS)
def test_real_emitter_glow(name):
    rgb, owners, emitter = _emitter_case(FIXTURES / "emitters" / name)
    maps, intensity, report, warnings = sb.emissive_maps(rgb, owners, [emitter])
    owner = owners[emitter["layerId"]]
    glow = maps[emitter["layerId"]].max(axis=-1) > 8
    h, w = owner.shape
    x0, y0, x1, y1 = emitter["bbox"]
    box = np.zeros_like(owner)
    box[int(y0 * h):int(np.ceil(y1 * h)), int(x0 * w):int(np.ceil(x1 * w))] = True

    assert report[0]["layerFraction"] >= LAYER_FRACTION_FLOOR[name], report[0]
    assert warnings == [], "real lanterns, windows and moons must not be flagged as over-filled"
    # The brightest pixel the layer owns inside the box is the light itself; it must glow.
    lightness = skcolor.rgb2lab(rgb)[..., 0]
    candidates = np.where(box & owner, lightness, -1)
    y, x = np.unravel_index(np.argmax(candidates), candidates.shape)
    assert glow[y, x], f"{emitter['name']}: the brightest pixel stays dark"
    # No dark hole inside the glow: everything the glow encloses that is bright glows too.
    enclosed = ndi.binary_fill_holes(glow) & ~glow & box & owner & (lightness >= sb.EMITTER_HOT_L)
    assert enclosed.sum() <= 2, f"{emitter['name']}: {int(enclosed.sum())} bright pixels left dark inside the glow"
    # Glow stays inside the box (+2 px soft edge) and on the layer's own pixels.
    grown = ndi.binary_dilation(box, iterations=2)
    assert not (glow & ~grown).any()
    assert (glow & ~owner).sum() <= 0.1 * glow.sum()
