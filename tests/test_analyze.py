"""Phase 2: papercraft_analyze end-to-end with a fake Hermes ctx.llm."""

from __future__ import annotations

import copy
import io
import json
import re
from dataclasses import dataclass, field

import pytest
from PIL import Image

from conftest import FakeContext, call, plugin

GOOD = {
    "schemaVersion": "1.0",
    "subject": "Rolling paper hills under a low sun with a row of pine trees",
    "style": {"description": "Layered paper-cut landscape, warm muted palette, soft drop shadows",
              "keywords": ["paper-cut", "layered", "landscape"]},
    "palette": [
        {"hex": "#f4d6aa", "role": "background", "name": "sand sky"},
        {"hex": "#f7b267", "role": "accent", "name": "sun"},
        {"hex": "#c9a7a0", "role": "secondary"},
        {"hex": "#8f4f4a", "role": "dominant"},
        {"hex": "#5a2e3a", "role": "shadow"},
    ],
    "light": {"azimuthDeg": -30, "elevationDeg": 40, "colorTemperatureK": 4200, "softness": 0.7,
              "confidence": 0.6},
    "camera": {"type": "orthographic", "reason": "Flat frontal composition", "maxTiltDeg": 20},
    "layers": [
        {"id": "sky", "name": "Sky and sun", "role": "sky", "order": 0, "description": "gradient sky, orange sun",
         "bbox": [0, 0, 1, 0.6], "depthEstimate": 0, "confidence": 0.9},
        {"id": "mountains", "name": "Far mountains", "role": "background", "order": 1,
         "description": "pale mauve ridge", "bbox": [0, 0.28, 1, 0.75], "depthEstimate": 0.25,
         "occludes": ["sky"], "confidence": 0.85},
        {"id": "hills", "name": "Hills", "role": "midground", "order": 2, "description": "terracotta hills",
         "bbox": [0, 0.5, 1, 0.95], "depthEstimate": 0.6, "occludes": ["mountains"], "confidence": 0.7},
        {"id": "foreground", "name": "Trees", "role": "foreground", "order": 3,
         "description": "dark plum pines and grass", "bbox": [0, 0.45, 1, 1], "depthEstimate": 1,
         "occludes": ["hills"], "confidence": 0.8},
    ],
    "reconstruction": {"recommended": "2.5d", "notes": "Flat layers; no visible thickness"},
    "confidence": {"overall": 0.75, "layering": 0.8},
    "needsReview": [],
}


@dataclass
class Usage:
    input_tokens: int = 1200
    output_tokens: int = 800
    total_tokens: int = 2000


@dataclass
class StructuredResult:  # mirrors agent.plugin_llm.PluginLlmStructuredResult
    text: str
    provider: str
    model: str
    agent_id: str = "default"
    usage: Usage = field(default_factory=Usage)
    parsed: object = None
    content_type: str = "text"
    audit: dict = field(default_factory=dict)


class FakeLlm:
    """Scripted replies; parsing follows Hermes's json_mode behaviour (fence strip + json.loads)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def complete_structured(self, *, instructions, input, json_schema=None, json_mode=False, schema_name=None,
                            system_prompt=None, provider=None, model=None, temperature=None, max_tokens=None,
                            timeout=None, agent_id=None, profile=None, purpose=None, task=None):
        self.calls.append(dict(instructions=instructions, input=input, json_schema=json_schema,
                               json_mode=json_mode, provider=provider, model=model, timeout=timeout,
                               purpose=purpose))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        match = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
        try:
            parsed, ctype = json.loads(match.group(1) if match else text), "json"
        except ValueError:
            parsed, ctype = None, "text"
        return StructuredResult(text=text, provider="openrouter", model="vision-test", parsed=parsed,
                                content_type=ctype)


def _ctx(tmp_path, replies, **config):
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws"), **config}, llm=FakeLlm(replies))
    plugin.register(ctx)
    return ctx


def _project_dir(tmp_path, project_id):
    return tmp_path / "ws" / project_id


def test_analyze_success(tmp_path, fixture_dir):
    ctx = _ctx(tmp_path, [GOOD])
    res = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png"), "brief": "soft light"})
    assert res["ok"] is True, res
    data = res["data"]
    pdir = _project_dir(tmp_path, data["project_id"])
    doc = json.loads((pdir / "analysis.json").read_text())

    assert [layer["id"] for layer in doc["layers"]] == ["sky", "mountains", "hills", "foreground"]
    assert doc["meta"]["attempts"] == 1 and doc["meta"]["model"] == "vision-test"
    assert doc["meta"]["brief"] == "soft light"
    assert doc["measured"]["image"] == {"width": 480, "height": 320}
    assert len(doc["measured"]["palette"]) >= 4
    assert json.loads((pdir / "status.json").read_text())["stages"]["analyze"]["state"] == "succeeded"
    assert (pdir / "logs" / "analysis_reply_1.txt").is_file()
    assert data["needs_review"] == []  # all claimed colours exist in the fixture
    assert data["next_step"] == "papercraft_segment"

    llm_call = ctx.llm.calls[0]
    assert llm_call["json_mode"] is True and llm_call["json_schema"] is None
    assert llm_call["provider"] is None and llm_call["model"] is None, "must not trip the trust gate"
    assert "soft light" in llm_call["instructions"] and '"layers"' in llm_call["instructions"]
    assert '"meta"' not in llm_call["instructions"], "plugin-owned fields are not requested from the model"
    assert llm_call["input"][0]["type"] == "image"


def test_model_image_is_downscaled(tmp_path):
    big = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 2000), "#c9a7a0").save(big, "JPEG")
    ctx = _ctx(tmp_path, [GOOD])
    res = call(ctx, "papercraft_analyze", {"image_path": str(big)})
    assert res["ok"], res
    sent = ctx.llm.calls[0]["input"][0]
    with Image.open(io.BytesIO(sent["data"])) as img:
        assert max(img.size) == 1536 and sent["mime_type"] == "image/jpeg"


def test_repair_round_fixes_invalid_reply(tmp_path, fixture_dir):
    bad = copy.deepcopy(GOOD)
    bad["layers"][1]["bbox"] = [0.9, 0.2, 0.1, 0.5]  # x0 > x1
    ctx = _ctx(tmp_path, ["```json\n" + json.dumps(bad) + "\n```", GOOD])
    res = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})
    assert res["ok"], res
    assert res["data"]["attempts"] == 2
    repair = ctx.llm.calls[1]["instructions"]
    assert "previous answer was rejected" in repair and "layers/1/bbox" in repair


def test_non_json_then_valid(tmp_path, fixture_dir):
    ctx = _ctx(tmp_path, ["Sure! Here is my analysis of the image...", GOOD])
    res = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})
    assert res["ok"] and res["data"]["attempts"] == 2


def test_rejected_twice_fails_honestly(tmp_path, fixture_dir):
    too_many = copy.deepcopy(GOOD)
    too_many["layers"] = too_many["layers"] * 2  # duplicates, also > max_layers
    ctx = _ctx(tmp_path, [too_many, too_many])
    res = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png"), "max_layers": 4})
    assert res["ok"] is False
    err = res["error"]
    assert err["code"] == "model_output_invalid" and err["retryable"] is True
    pdir = _project_dir(tmp_path, err["details"]["project_id"])
    assert not (pdir / "analysis.json").exists(), "no analysis may be written on failure"
    status = json.loads((pdir / "status.json").read_text())["stages"]["analyze"]
    assert status["state"] == "failed" and status["error"]["code"] == "model_output_invalid"


def test_model_unavailable_and_timeout(tmp_path, fixture_dir):
    src = str(fixture_dir / "source.png")
    ctx = _ctx(tmp_path, [RuntimeError("provider 503"), TimeoutError()])
    first = call(ctx, "papercraft_analyze", {"image_path": src})
    assert first["error"]["code"] == "model_unavailable" and "vision-capable" in first["error"]["message"]
    second = call(ctx, "papercraft_analyze", {"image_path": src})
    assert second["error"]["code"] == "timeout" and second["error"]["retryable"]


def test_no_llm_access(tmp_path, fixture_dir):
    ctx = FakeContext(config={"workspace_dir": str(tmp_path / "ws")}, llm=None)
    plugin.register(ctx)
    res = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})
    assert res["error"]["code"] == "model_unavailable"


def test_reanalyse_existing_project(tmp_path, fixture_dir):
    ctx = _ctx(tmp_path, [RuntimeError("down"), GOOD])
    failed = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})
    pid = failed["error"]["details"]["project_id"]
    retry = call(ctx, "papercraft_analyze", {"project_id": pid})
    assert retry["ok"] and retry["data"]["project_id"] == pid
    assert len([p for p in (tmp_path / "ws").iterdir()]) == 1, "retry must not create a second project"


def test_palette_mismatch_and_occlusion_flagged(tmp_path, fixture_dir):
    odd = copy.deepcopy(GOOD)
    odd["palette"].append({"hex": "#00ff00", "role": "accent", "name": "neon green"})
    odd["layers"][0]["occludes"] = ["foreground"]  # sky cannot cover the nearest layer
    odd["layers"][2]["occludes"] = ["ghost"]  # unknown id
    ctx = _ctx(tmp_path, [odd])
    res = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})
    assert res["ok"], res
    fields = {item["field"] for item in res["data"]["needs_review"]}
    assert "palette/5" in fields and "layers/sky/occludes" in fields
    assert any("ghost" in w for w in res["warnings"])
    doc = json.loads((_project_dir(tmp_path, res["data"]["project_id"]) / "analysis.json").read_text())
    assert "ghost" not in doc["layers"][2].get("occludes", [])


def test_orders_are_renumbered(tmp_path, fixture_dir):
    gappy = copy.deepcopy(GOOD)
    for layer, order in zip(gappy["layers"], (0, 2, 5, 9)):
        layer["order"] = order
    ctx = _ctx(tmp_path, [gappy])
    res = call(ctx, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})
    assert [layer["order"] for layer in res["data"]["layers"]] == [0, 1, 2, 3]
    assert any("renumbered" in w for w in res["warnings"])


@pytest.mark.parametrize("args", [
    {"image_path": "a.png", "project_id": "x"},
    {"image_path": "a.png", "max_layers": True},
    {"image_path": "a.png", "max_layers": 9},
    {"image_path": "a.png", "brief": 5},
])
def test_argument_validation(tmp_path, args):
    ctx = _ctx(tmp_path, [])
    assert call(ctx, "papercraft_analyze", args)["error"]["code"] == "invalid_argument"
