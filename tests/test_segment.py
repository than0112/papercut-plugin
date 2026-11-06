"""Phase 3: papercraft_segment with an oracle labeller on the synthetic fixture (known ground truth)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from PIL import Image

from conftest import FakeContext, FakeLlm, call, plugin
from papercraft_hermes.core import segmentation as seg
from papercraft_hermes.core.schemas import schema_errors
from papercraft_hermes.core.segment_backends import parse_labels

LAYER_IDS = ["sky", "mountains", "hills_far", "hills_near", "foreground"]


def _analysis_reply(fixture_dir):
    scene = json.loads((fixture_dir / "scene.json").read_text())
    descriptions = {
        "sky": "pale sand gradient sky with an orange sun", "mountains": "pale mauve far mountain ridge",
        "hills_far": "terracotta rolling hills", "hills_near": "dark red near hills",
        "foreground": "dark plum pine trees and ground",
    }
    layers = [{"id": l["id"], "name": l["name"], "role": l["role"], "order": l["order"],
               "description": descriptions[l["id"]], "bbox": [0, 0, 1, 1],
               "depthEstimate": l["order"] / 4, "confidence": 0.9} for l in scene["layers"]]
    return {
        "schemaVersion": "1.0", "subject": "Paper hills", "style": {"description": "paper-cut", "keywords": []},
        "palette": [{"hex": "#5a2e3a", "role": "dominant"}],
        "light": {"azimuthDeg": -30, "elevationDeg": 40, "colorTemperatureK": 4500, "softness": 0.6, "confidence": 0.5},
        "camera": {"type": "orthographic", "reason": "frontal"}, "layers": layers,
        "reconstruction": {"recommended": "2.5d", "notes": ""}, "confidence": {"overall": 0.8}, "needsReview": [],
    }


def _ground_truth(fixture_dir):
    alphas = [np.asarray(Image.open(fixture_dir / "assets" / f"{i}.png").getchannel("A")) >= 128 for i in LAYER_IDS]
    gt = np.full(alphas[0].shape, -1)
    for k, a in enumerate(alphas):
        gt[a] = k
    return gt


class Oracle:
    """Fake labeller: answers each numbered region with its majority ground-truth layer."""

    def __init__(self, fixture_dir, monkeypatch, *, drop_fraction=0.0, skip_layer=None):
        self.gt = _ground_truth(fixture_dir)
        self.regions = None
        self.drop_fraction = drop_fraction
        self.skip_layer = skip_layer
        real = seg.oversegment

        def spy(work_rgb, **kw):
            self.regions = real(work_rgb, **kw)
            return self.regions

        monkeypatch.setattr(seg, "oversegment", spy)

    def __call__(self, llm_call):
        assert [b["type"] for b in llm_call["input"]] == ["image", "image"], "original + marks image"
        assert llm_call["provider"] is None and llm_call["model"] is None
        r = self.regions
        h, w = r.labels.shape  # work resolution: sample the ground truth there
        gt_work = self.gt[(np.arange(h) * self.gt.shape[0] // h)][:, (np.arange(w) * self.gt.shape[1] // w)]
        assignments = {}
        for region, number in r.numbers.items():
            values = gt_work[r.labels == region]
            layer = int(np.bincount(values[values >= 0]).argmax())
            if self.skip_layer is not None and layer == self.skip_layer:
                layer = max(0, layer - 1)
            assignments[str(number)] = LAYER_IDS[layer]
        drop = int(len(assignments) * self.drop_fraction)
        # drop the largest regions first so the unlabelled AREA is large
        for number in [str(n) for n in range(1, drop + 1)]:
            assignments.pop(number, None)
        return {"assignments": assignments, "uncertain": [], "notes": "oracle"}


def _project(tmp_path, fixture_dir, replies, **config):
    llm = FakeLlm([_analysis_reply(fixture_dir), *replies])
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws"), **config}, llm=llm)
    plugin.register(ctx)
    res = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})
    assert res["ok"], res
    pid = res["data"]["project_id"]
    return ctx, pid, tmp_path / "ws" / pid


def _iou(a, b):
    return float((a & b).sum() / max(1, (a | b).sum()))


def test_segment_end_to_end(tmp_path, fixture_dir, monkeypatch):
    oracle = Oracle(fixture_dir, monkeypatch)
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [oracle])
    source_sha = hashlib.sha256((pdir / "source" / "source.png").read_bytes()).hexdigest()

    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"], res
    manifest = json.loads((pdir / "layers.json").read_text())
    assert schema_errors("layers", manifest) == []
    assert manifest["mode"] == "layered" and [l["id"] for l in manifest["layers"]] == LAYER_IDS
    assert json.loads((pdir / "status.json").read_text())["stages"]["segment"]["state"] == "succeeded"

    gt = _ground_truth(fixture_dir)
    owner = np.asarray(Image.open(pdir / "segmentation" / "owner.png")).astype(int)
    owner[owner == 255] = -1
    ious = [_iou(owner == k, gt == k) for k in range(5)]
    assert min(ious) > 0.88, ious  # oracle labels -> measures the pipeline itself
    assert (owner == gt).mean() > 0.96

    alphas = []
    for k, layer in enumerate(manifest["layers"]):
        asset = Image.open(pdir / layer["asset"])
        assert asset.mode == "RGBA" and asset.size == (480, 320)
        alpha = np.asarray(asset.getchannel("A")) > 0
        alphas.append(alpha)
        visible = np.asarray(Image.open(pdir / layer["mask"])) > 0
        assert np.array_equal(visible, owner == k), "mask = owned pixels"
        assert alpha[visible].all(), "every owned pixel is opaque in the asset"
    assert alphas[0].all(), "the back layer is never transparent"
    assert all(not a.all() for a in alphas[1:]), "nearer layers are cut out, not copies of the image"
    fractions = [l["provenance"]["inpaintedFraction"] for l in manifest["layers"]]
    assert all(f > 0 for f in fractions[:-1]) and fractions[-1] == 0, fractions
    assert all(l["provenance"]["source"] == "auto" for l in manifest["layers"])

    for rel in ("review/overlay.png", "review/marks.png", "segmentation/regions.json", "logs/segment_labels_1.txt"):
        assert (pdir / rel).is_file(), rel
    assert manifest["labelling"]["model"] == "vision-test" and manifest["fill"]["marginPx"] == 24
    assert hashlib.sha256((pdir / "source" / "source.png").read_bytes()).hexdigest() == source_sha
    assert res["data"]["next_step"] == "papercraft_build_scene"


def test_repair_round_completes_labels(tmp_path, fixture_dir, monkeypatch):
    oracle = Oracle(fixture_dir, monkeypatch)
    partial = lambda c: {"assignments": dict(list(oracle(c)["assignments"].items())[:3])}  # noqa: E731
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [partial, oracle])
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"], res
    assert res["data"]["labelling"]["attempts"] == 2
    assert "regions without a layer" in ctx.llm.calls[-1]["instructions"]


def test_insufficient_labels_fail_honestly(tmp_path, fixture_dir, monkeypatch):
    oracle = Oracle(fixture_dir, monkeypatch, drop_fraction=0.5)
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [oracle, oracle])
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"] is False and res["error"]["code"] == "segmentation_failed"
    assert not (pdir / "layers.json").exists()
    assert json.loads((pdir / "status.json").read_text())["stages"]["segment"]["state"] == "failed"


def test_preview_fallback_is_labelled(tmp_path, fixture_dir):
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [RuntimeError("vision down")])
    res = call(ctx, "papercraft_segment", {"project_id": pid, "allow_preview_fallback": True})
    assert res["ok"] is True and res["data"]["mode"] == "preview"
    assert any("示意預覽" in w and "not a layered" in w for w in res["warnings"])
    manifest = json.loads((pdir / "layers.json").read_text())
    assert manifest["mode"] == "preview" and manifest["layers"][0]["provenance"]["source"] == "preview"
    stage = json.loads((pdir / "status.json").read_text())["stages"]["segment"]
    assert stage["state"] == "degraded" and stage["mode"] == "preview"


def test_no_fallback_means_failure(tmp_path, fixture_dir):
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [RuntimeError("vision down")])
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"] is False and res["error"]["code"] == "model_unavailable"
    assert not (pdir / "layers.json").exists()


def test_segment_requires_analysis(tmp_path, fixture_dir):
    from papercraft_hermes.core.security import validate_image
    from papercraft_hermes.core.storage import Workspace

    project = Workspace(tmp_path / "ws").create_project(validate_image(fixture_dir / "source.png"))
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws")}, llm=FakeLlm([]))
    plugin.register(ctx)
    res = call(ctx, "papercraft_segment", {"project_id": project.id})
    assert res["error"]["code"] == "precondition_failed"


def test_empty_layer_is_dropped(tmp_path, fixture_dir, monkeypatch):
    oracle = Oracle(fixture_dir, monkeypatch, skip_layer=2)  # nothing labelled hills_far
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [oracle])
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"], res
    ids = [l["id"] for l in res["data"]["layers"]]
    assert "hills_far" not in ids and [l["order"] for l in res["data"]["layers"]] == list(range(4))
    assert any("hills_far" in w and "dropped" in w for w in res["warnings"])


def test_manual_mask_reuses_previous_result(tmp_path, fixture_dir, monkeypatch):
    oracle = Oracle(fixture_dir, monkeypatch)
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [oracle])
    assert call(ctx, "papercraft_segment", {"project_id": pid})["ok"]
    calls_before = len(ctx.llm.calls)

    mask = np.zeros((320, 480), dtype=np.uint8)
    mask[250:300, 20:120] = 255  # part of the ground -> claim it for the sky
    mask_path = tmp_path / "fix_sky.png"
    Image.fromarray(mask, "L").save(mask_path)
    res = call(ctx, "papercraft_segment", {"project_id": pid, "manual_masks": {"sky": str(mask_path)}})
    assert res["ok"], res
    assert len(ctx.llm.calls) == calls_before, "a manual edit must not call the model again"
    manifest = json.loads((pdir / "layers.json").read_text())
    sky = manifest["layers"][0]
    assert sky["provenance"]["source"] == "manual" and sky["provenance"]["manualMask"] == "masks/manual/sky.png"
    assert manifest["labelling"]["reused"] is True and manifest["manualEdits"][0]["pixels"] == 5000
    assert all(l["provenance"]["source"] == "auto" for l in manifest["layers"][1:])
    owner = np.asarray(Image.open(pdir / "segmentation" / "owner.png"))
    assert (owner[250:300, 20:120] == 0).all()


@pytest.mark.parametrize("masks,code", [
    ({"nope": "x.png"}, "invalid_argument"),
    ({"sky": "WRONG_SIZE"}, "invalid_argument"),
    ({"sky": "EMPTY"}, "invalid_argument"),
    ({"sky": "/does/not/exist.png"}, "invalid_argument"),
])
def test_manual_mask_validation(tmp_path, fixture_dir, masks, code):
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [])
    if masks.get("sky") == "WRONG_SIZE":
        Image.new("L", (100, 100), 255).save(tmp_path / "w.png")
        masks = {"sky": str(tmp_path / "w.png")}
    if masks.get("sky") == "EMPTY":
        Image.new("L", (480, 320), 0).save(tmp_path / "e.png")
        masks = {"sky": str(tmp_path / "e.png")}
    res = call(ctx, "papercraft_segment", {"project_id": pid, "manual_masks": masks})
    assert res["error"]["code"] == code
    assert json.loads((pdir / "status.json").read_text())["stages"]["segment"]["state"] == "pending"


# --------------------------------------------------------------------------- http backend


class _Service(BaseHTTPRequestHandler):
    reply = None
    status = 200
    received = None

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        type(self).received = json.loads(self.rfile.read(length))
        body = json.dumps(type(self).reply).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def service():
    server = HTTPServer(("127.0.0.1", 0), _Service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/segment"
    server.shutdown()


def test_http_backend(tmp_path, fixture_dir, service):
    gt = _ground_truth(fixture_dir)
    buf = io.BytesIO()
    Image.fromarray((gt + 1).astype(np.uint8), "L").save(buf, "PNG")
    _Service.status = 200
    _Service.reply = {"labelMap": {"mime": "image/png", "data": base64.b64encode(buf.getvalue()).decode()},
                      "confidence": {"sky": 0.95}, "method": "gt-service"}
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [], segmentation_backend="http", segmentation_url=service)
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"], res
    assert _Service.received["layers"][0]["id"] == "sky" and _Service.received["image"]["width"] == 480
    assert res["data"]["backend"] == "http:gt-service"
    assert res["data"]["layers"][0]["confidence"] == 0.95 and res["data"]["layers"][1]["confidence"] == 0.5
    owner = np.asarray(Image.open(pdir / "segmentation" / "owner.png")).astype(int)
    owner[owner == 255] = -1
    assert (owner == gt).mean() > 0.99


def test_http_backend_errors(tmp_path, fixture_dir, service):
    _Service.status = 500
    _Service.reply = {"error": "boom"}
    ctx, pid, pdir = _project(tmp_path, fixture_dir, [], segmentation_backend="http", segmentation_url=service)
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["error"]["code"] == "segmentation_failed" and res["error"]["retryable"] is True

    ctx2, pid2, _ = _project(tmp_path / "b", fixture_dir, [], segmentation_backend="http",
                             segmentation_url="http://example.com/segment")
    assert call(ctx2, "papercraft_segment", {"project_id": pid2})["error"]["code"] == "invalid_argument"


# --------------------------------------------------------------------------- units


def test_parse_labels():
    ids = ["a", "b"]
    ok, unc, problems = parse_labels({"assignments": {"1": "a", "2": "b"}, "uncertain": [2]}, 2, ids)
    assert ok == {1: 0, 2: 1} and unc == [2] and problems == []
    _, _, problems = parse_labels({"assignments": {"1": "z", "x": "a", "9": "a"}}, 2, ids)
    assert any("unknown layer" in p for p in problems) and any("does not exist" in p for p in problems)
    assert any("without a layer" in p for p in problems)
    assert parse_labels(["nope"], 2, ids)[2]


def test_fill_is_smooth_and_keeps_visible_pixels():
    rgba = np.zeros((60, 80, 4), dtype=np.uint8)
    rgba[..., :3] = (200, 100, 50)
    rgba[..., 3] = 255
    rgba[:, 40:, :3] = (10, 10, 10)  # nearer layer's pixels
    owner = np.zeros((60, 80), dtype=np.int16)
    owner[:, 40:] = 1
    maps = seg.build_layers(rgba, owner, 2, margin=0.1)
    back = maps.rgba[0]
    assert (back[..., 3] == 255).all()
    assert np.abs(back[:, 40:, :3].astype(int) - (200, 100, 50)).max() <= 2, "fill uses the back layer's colour"
    assert np.array_equal(back[:, :40], rgba[:, :40]), "owned pixels are untouched"
    assert (maps.rgba[1][:, :40, 3] == 0).all(), "the nearest layer is not extended"


def test_remove_islands():
    owner = np.zeros((50, 50), dtype=np.int16)
    owner[20, 20] = 1
    owner[:, 40:] = 1
    out = seg.remove_islands(owner, 2)
    assert out[20, 20] == 0 and (out[:, 40:] == 1).all()
