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

    def __init__(self, fixture_dir=None, monkeypatch=None, *, drop_fraction=0.0, skip_layer=None,
                 gt=None, ids=None, flag_uncertain=False):
        self.gt = gt if gt is not None else _ground_truth(fixture_dir)
        self.ids = ids or LAYER_IDS
        self.regions = None
        self.refined = None
        self.drop_fraction = drop_fraction
        self.skip_layer = skip_layer
        self.flag_uncertain = flag_uncertain
        self.batch = None
        real, real_sub, real_render = seg.oversegment, seg.subdivide, seg.render_refine_marks

        def spy(work_rgb, **kw):
            self.regions = real(work_rgb, **kw)
            return self.regions

        def spy_sub(regions, targets):
            self.refined = real_sub(regions, targets)
            return self.refined

        def spy_render(work_rgb, regions, parts):
            self.batch = list(parts)  # the parts numbered 1..n on the image being sent
            return real_render(work_rgb, regions, parts)

        monkeypatch.setattr(seg, "oversegment", spy)
        monkeypatch.setattr(seg, "subdivide", spy_sub)
        monkeypatch.setattr(seg, "render_refine_marks", spy_render)

    def _gt_work(self, labels):
        h, w = labels.shape  # work resolution: sample the ground truth there
        return self.gt[(np.arange(h) * self.gt.shape[0] // h)][:, (np.arange(w) * self.gt.shape[1] // w)]

    def _majority(self, gt_work, mask):
        values = gt_work[mask]
        counts = np.bincount(values[values >= 0], minlength=len(self.ids))
        return int(counts.argmax()), counts.max() / max(1, counts.sum())

    def __call__(self, llm_call):
        assert [b["type"] for b in llm_call["input"]] == ["image", "image"], "original + marks image"
        assert llm_call["provider"] is None and llm_call["model"] is None
        if llm_call["purpose"] == "papercraft.segment.refine":
            refined = self.refined[0]
            gt_work = self._gt_work(refined.labels)
            answer = {str(i): self.ids[self._majority(gt_work, refined.labels == part)[0]]
                      for i, part in enumerate(self.batch, start=1)}
            return {"assignments": answer, "uncertain": []}
        r = self.regions
        gt_work = self._gt_work(r.labels)
        assignments = {}
        uncertain = []
        for region, number in r.numbers.items():
            layer, purity = self._majority(gt_work, r.labels == region)
            if self.flag_uncertain and purity < 0.95:
                uncertain.append(number)
            if self.skip_layer is not None and layer == self.skip_layer:
                layer = max(0, layer - 1)
            assignments[str(number)] = self.ids[layer]
        drop = int(len(assignments) * self.drop_fraction)
        # drop the largest regions first so the unlabelled AREA is large
        for number in [str(n) for n in range(1, drop + 1)]:
            assignments.pop(number, None)
        return {"assignments": assignments, "uncertain": uncertain, "notes": "oracle"}


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
    source = Image.open(pdir / "source" / "source.png").convert("RGBA")
    stack = Image.new("RGBA", source.size, (0, 0, 0, 255))
    for layer in manifest["layers"]:
        stack = Image.alpha_composite(stack, Image.open(pdir / layer["asset"]))
    assert np.array_equal(np.asarray(stack)[..., :3], np.asarray(source)[..., :3]), \
        "restacking the layers must reproduce the source exactly"
    assert all(not a.all() for a in alphas[1:]), "nearer layers are cut out, not copies of the image"
    fractions = [l["provenance"]["inpaintedFraction"] for l in manifest["layers"]]
    assert all(f > 0 for f in fractions[:-1]) and fractions[-1] == 0, fractions
    assert all(l["provenance"]["source"] == "auto" for l in manifest["layers"])

    for rel in ("review/overlay.png", "review/layer_map.png", "review/marks.png", "segmentation/regions.json", "logs/segment_labels_1.txt"):
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


# --------------------------------------------------------------------------- refinement round


def _same_colour_scene(tmp_path):
    """Sky and blossoms of almost the same pink, separated only by a thin paper edge."""
    from PIL import ImageDraw

    w, h = 800, 500
    img = Image.new("RGB", (w, h), (240, 160, 168))
    gt = np.zeros((h, w), dtype=int)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 330, w, h], fill=(120, 115, 120))  # mountains
    gt[330:, :] = 1
    for cx, cy, r in ((90, 70, 70), (220, 40, 55), (700, 80, 80), (580, 30, 45)):
        draw.ellipse([cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3], fill=(200, 120, 130))  # paper edge
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(244, 166, 174))  # blossom
        yy, xx = np.ogrid[:h, :w]
        gt[(yy - cy) ** 2 + (xx - cx) ** 2 <= (r + 3) ** 2] = 2
    path = tmp_path / "pink.png"
    img.save(path)
    return path, gt


def _pink_analysis():
    layers = [
        {"id": "sky", "name": "Sky", "role": "sky", "order": 0, "description": "pink sky", "bbox": [0, 0, 1, 0.66],
         "depthEstimate": 0, "confidence": 0.9},
        {"id": "hills", "name": "Hills", "role": "background", "order": 1, "description": "grey band",
         "bbox": [0, 0.66, 1, 1], "depthEstimate": 0.5, "confidence": 0.9},
        {"id": "blossoms", "name": "Blossoms", "role": "foreground", "order": 2, "description": "pink blossom discs",
         "bbox": [0, 0, 1, 0.35], "depthEstimate": 1, "confidence": 0.9},
    ]
    return {"schemaVersion": "1.0", "subject": "test", "style": {"description": "t", "keywords": []},
            "palette": [{"hex": "#f0a0a8", "role": "dominant"}],
            "light": {"azimuthDeg": 0, "elevationDeg": 30, "colorTemperatureK": 5000, "softness": 0.5, "confidence": 0.5},
            "camera": {"type": "orthographic", "reason": "t"}, "layers": layers,
            "reconstruction": {"recommended": "2.5d", "notes": ""}, "confidence": {"overall": 0.5}, "needsReview": []}


@pytest.mark.parametrize("mode", ["model_flags", "colour_check", "no_second_round"])
def test_refinement_round_splits_mixed_regions(tmp_path, monkeypatch, mode):
    """Mixed regions reach the second round when the model flags them, or when it does not but the
    colour check against neighbouring layers catches them. Without a second round they stay wrong."""
    from papercraft_hermes.core import segment_backends

    path, gt = _same_colour_scene(tmp_path)
    monkeypatch.setattr(segment_backends, "marks_for", lambda n: 3)  # force coarse, mixed regions
    if mode == "no_second_round":
        monkeypatch.setattr(segment_backends, "AUTO_REFINE_MAX", 0)
    oracle = Oracle(monkeypatch=monkeypatch, gt=gt, ids=["sky", "hills", "blossoms"],
                    flag_uncertain=mode == "model_flags")
    llm = FakeLlm([_pink_analysis(), oracle, oracle])
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws")}, llm=llm)
    plugin.register(ctx)
    pid = call(ctx, "papercraft_analyze", {"image_path": str(path)})["data"]["project_id"]
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"], res
    owner = np.asarray(Image.open(tmp_path / "ws" / pid / "segmentation" / "owner.png")).astype(int)
    blossom_iou = _iou(owner == 2, gt == 2)
    purposes = [c["purpose"] for c in llm.calls]
    labelling = res["data"]["labelling"]
    if mode == "no_second_round":
        assert "papercraft.segment.refine" not in purposes
        assert blossom_iou < 0.6, "without a second round the mixed region stays wrong"
        return
    assert purposes.count("papercraft.segment.refine") == labelling["refineBatches"] >= 1
    assert labelling["refined"] >= 1
    assert (tmp_path / "ws" / pid / "review" / "refine_marks.png").is_file()
    assert blossom_iou > 0.9, blossom_iou
    if mode == "colour_check":
        assert labelling["autoFlagged"], "the plugin, not the model, sent the regions to the second round"
        manifest = json.loads((tmp_path / "ws" / pid / "layers.json").read_text())
        assert schema_errors("layers", manifest) == []


def test_colour_check_ignores_pure_regions(fixture_dir):
    """With perfect labels on the synthetic fixture (cast shadows, gradients), nothing is flagged."""
    from papercraft_hermes.core import segment_backends

    gt = _ground_truth(fixture_dir)
    work, _ = seg.work_copy(seg.load_rgba(str(fixture_dir / "source.png")))
    regions = seg.oversegment(work, max_marks=segment_backends.marks_for(len(LAYER_IDS)))
    h, w = regions.labels.shape
    gt_work = gt[(np.arange(h) * gt.shape[0] // h)][:, (np.arange(w) * gt.shape[1] // w)]
    assignment = {}
    for r in regions.marked:
        values = gt_work[regions.labels == r]
        assignment[r] = int(np.bincount(values[values >= 0], minlength=len(LAYER_IDS)).argmax())
    assert seg.mixed_suspects(regions, assignment, seg.REFINE_MIN_AREA * regions.labels.size) == []


def test_refinement_failure_keeps_first_round(tmp_path, monkeypatch):
    from papercraft_hermes.core import segment_backends

    path, gt = _same_colour_scene(tmp_path)
    monkeypatch.setattr(segment_backends, "marks_for", lambda n: 3)
    oracle = Oracle(monkeypatch=monkeypatch, gt=gt, ids=["sky", "hills", "blossoms"], flag_uncertain=True)
    llm = FakeLlm([_pink_analysis(), oracle, RuntimeError("refine call down")])
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws")}, llm=llm)
    plugin.register(ctx)
    pid = call(ctx, "papercraft_analyze", {"image_path": str(path)})["data"]["project_id"]
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"], res
    assert any("refinement round failed" in w for w in res["warnings"])


def test_refine_batches_keep_regions_together():
    parent = {p: p // 100 for p in [101, 102, 103, 201, 202, 301, 302, 303, 304, 401]}
    parts = sorted(parent)
    batches = seg.refine_batches(parent, parts, targets=[1, 2, 3, 4], limit=5)
    assert batches == [[101, 102, 103, 201, 202], [301, 302, 303, 304, 401]]
    assert seg.refine_batches(parent, parts, targets=[3, 1, 2, 4], limit=4) == [
        [301, 302, 303, 304], [101, 102, 103], [201, 202, 401]], "priority order kept, oversized batches avoided"
    assert seg.refine_batches(parent, parts, targets=[1, 2, 3, 4], limit=2)[0] == [101, 102, 103], \
        "a region larger than the limit gets a batch of its own"


@pytest.mark.parametrize("fail_second", [False, True])
def test_second_round_in_batches(tmp_path, monkeypatch, fail_second):
    """Many mixed regions: the second round is sent as several smaller images, each labelled on its own."""
    from papercraft_hermes.core import segment_backends

    path, gt = _same_colour_scene(tmp_path)
    monkeypatch.setattr(segment_backends, "marks_for", lambda n: 3)
    monkeypatch.setattr(seg, "REFINE_PARTS", 8)
    monkeypatch.setattr(seg, "refine_batches",
                        lambda parent, parts, targets: [parts[: len(parts) // 2], parts[len(parts) // 2:]])
    oracle = Oracle(monkeypatch=monkeypatch, gt=gt, ids=["sky", "hills", "blossoms"], flag_uncertain=True)
    second = RuntimeError("second batch down") if fail_second else oracle
    llm = FakeLlm([_pink_analysis(), oracle, oracle, second])
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws")}, llm=llm)
    plugin.register(ctx)
    pid = call(ctx, "papercraft_analyze", {"image_path": str(path)})["data"]["project_id"]
    res = call(ctx, "papercraft_segment", {"project_id": pid})
    assert res["ok"], res
    pdir = tmp_path / "ws" / pid
    refine_calls = [c for c in llm.calls if c["purpose"] == "papercraft.segment.refine"]
    assert len(refine_calls) == 2 and "batch 1 of 2" in refine_calls[0]["instructions"]
    assert "batch 2 of 2" in refine_calls[1]["instructions"]
    assert res["data"]["labelling"]["refineBatches"] == 2
    assert (pdir / "review" / "refine_marks.png").is_file() and (pdir / "review" / "refine_marks_2.png").is_file()
    owner = np.asarray(Image.open(pdir / "segmentation" / "owner.png")).astype(int)
    if fail_second:
        assert any("batch 2 of 2" in w for w in res["warnings"])
    else:
        assert (pdir / "logs" / "segment_labels_refine2.txt").is_file()
        assert _iou(owner == 2, gt == 2) > 0.9
