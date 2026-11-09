"""Claude Code plugin: the stdio MCP server, driven over JSON-RPC like Claude Code does."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from conftest import ROOT
from test_analyze import GOOD
from test_segment import LAYER_IDS, _analysis_reply, _ground_truth

SERVER = ROOT / "claude" / "server.py"


class Client:
    def __init__(self, tmp_path, python=sys.executable, extra_env=None):
        env = {**os.environ, "PAPERCRAFT_WORKSPACE": str(tmp_path / "ws"), "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
               **(extra_env or {})}
        self.proc = subprocess.Popen([python, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, env=env)
        self.next_id = 0

    def request(self, method, params=None):
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        while True:
            msg = json.loads(self.proc.stdout.readline())
            if msg.get("id") == self.next_id:
                return msg
            assert msg.get("method", "").startswith("notifications/")

    def call(self, name, args):
        res = self.request("tools/call", {"name": name, "arguments": args})["result"]
        texts = [json.loads(c["text"]) if c["text"].lstrip().startswith("{") else c["text"]
                 for c in res["content"] if c["type"] == "text"]
        images = [c for c in res["content"] if c["type"] == "image"]
        return res["isError"], texts, images

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


@pytest.fixture
def client(tmp_path):
    c = Client(tmp_path)
    init = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert init["result"]["serverInfo"]["name"] == "papercraft-3d"
    c.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    yield c
    c.close()


def _decode(img):
    return Image.open(io.BytesIO(base64.b64decode(img["data"])))


def _oracle_labels(fixture_dir, marks_count):
    """Replay the server's deterministic over-segmentation to answer like a perfect labeller."""
    from papercraft_hermes.core import segmentation as seg
    from papercraft_hermes.core.segment_backends import marks_for

    rgba = seg.load_rgba(str(fixture_dir / "source.png"))
    work, _ = seg.work_copy(rgba)
    regions = seg.oversegment(work, max_marks=marks_for(len(LAYER_IDS)))
    assert len(regions.marked) == marks_count
    gt = _ground_truth(fixture_dir)
    h, w = regions.labels.shape
    gt_work = gt[(np.arange(h) * gt.shape[0] // h)][:, (np.arange(w) * gt.shape[1] // w)]
    out = {}
    for region, number in regions.numbers.items():
        values = gt_work[regions.labels == region]
        out[str(number)] = LAYER_IDS[int(np.bincount(values[values >= 0]).argmax())]
    return out


def test_tools_list(client):
    tools = {t["name"]: t for t in client.request("tools/list")["result"]["tools"]}
    assert set(tools) == {"papercraft_status", "papercraft_analyze_begin", "papercraft_analyze_submit",
                          "papercraft_segment_begin", "papercraft_segment_submit", "papercraft_segment_correct",
                          "papercraft_build_scene", "papercraft_generate_plan", "papercraft_update_scene",
                          "papercraft_export_project"}
    assert "lighting_preset" in tools["papercraft_build_scene"]["inputSchema"]["properties"]
    assert client.request("tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602


def test_full_flow_with_claude_as_the_model(client, fixture_dir, tmp_path):
    err, texts, images = client.call("papercraft_analyze_begin", {"image_path": str(fixture_dir / "source.png"),
                                                                   "brief": "柔和光線"})
    assert not err and texts[0]["status"] == "awaiting your analysis" and len(images) == 1
    assert "JSON schema" in texts[1] and "柔和光線" in texts[1]
    pid = texts[0]["project_id"]

    bad = json.loads(json.dumps(_analysis_reply(fixture_dir)))
    bad["layers"][0]["bbox"] = [0.9, 0, 0.1, 1]  # invalid -> repair round
    err, texts, images = client.call("papercraft_analyze_submit", {"project_id": pid, "analysis": bad})
    assert not err and "rejected" in texts[0]["next"] and "layers/0/bbox" in texts[1] and images == []
    err, texts, _ = client.call("papercraft_analyze_submit", {"project_id": pid, "analysis": _analysis_reply(fixture_dir)})
    assert not err and texts[0]["ok"] and texts[0]["data"]["attempts"] == 2
    assert json.loads((tmp_path / "ws" / pid / "analysis.json").read_text())["meta"]["provider"] == "claude-code"

    err, texts, images = client.call("papercraft_segment_begin", {"project_id": pid})
    assert not err and len(images) == 2 and texts[0]["status"] == "awaiting your region labels"
    n = int(texts[1].split("all numbers from 1 to ")[1].split(".")[0])
    labels = _oracle_labels(fixture_dir, n)
    err, texts, images = client.call("papercraft_segment_submit", {"project_id": pid, "assignments": labels})
    assert not err and texts[0]["ok"] and len(images) == 2, texts  # overlay + solid layer map
    assert [l["id"] for l in texts[0]["data"]["layers"]] == LAYER_IDS
    assert _decode(images[0]).size[0] <= 1280

    err, texts, _ = client.call("papercraft_build_scene", {"project_id": pid})
    assert not err and texts[0]["data"]["preview"]["url"].endswith("scene.json")
    assert not client.call("papercraft_generate_plan", {"project_id": pid})[0]
    err, texts, _ = client.call("papercraft_update_scene", {"project_id": pid, "instruction": "夜間",
                                                            "patch": [{"op": "replace", "path": "/lighting",
                                                                       "value": {"$preset": "night"}}]})
    assert not err and texts[0]["data"]["revision"] == 1
    err, texts, _ = client.call("papercraft_export_project", {"project_id": pid})
    assert not err and texts[0]["data"]["checks"]["no_absolute_paths"]
    err, texts, _ = client.call("papercraft_status", {"project_id": pid})
    assert {k: v["state"] for k, v in texts[0]["project"]["stages"].items()} == {
        "analyze": "succeeded", "segment": "succeeded", "build_scene": "succeeded", "generate_plan": "succeeded",
        "update_scene": "succeeded", "export": "succeeded"}


def test_errors_are_reported_not_raised(client, tmp_path):
    err, texts, _ = client.call("papercraft_analyze_submit", {"project_id": "missing", "analysis": {}})
    assert err and texts[0]["error"]["code"] == "project_not_found"
    err, texts, _ = client.call("papercraft_build_scene", {"project_id": "../x"})
    assert err and texts[0]["error"]["code"] == "invalid_argument"


def test_missing_libraries_give_setup_commands(tmp_path):
    """A Python without the imaging stack: the server still starts and explains the setup."""
    c = Client(tmp_path, python="/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable,
               extra_env={"PAPERCRAFT_VENV": str(tmp_path / "no-venv"), "PYTHONNOUSERSITE": "1"})
    try:
        c.request("initialize", {"protocolVersion": "2025-06-18"})
        probe = subprocess.run([c.proc.args[0], "-c", "import skimage"], capture_output=True)
        if probe.returncode == 0:
            pytest.skip("this python already has scikit-image")
        err, texts, _ = c.call("papercraft_analyze_begin", {"image_path": "x.png"})
        assert err and "-m venv" in texts[0] and "requirements.txt" in texts[0]
        tools = c.request("tools/list")["result"]["tools"]
        assert len(tools) == 10
    finally:
        c.close()
