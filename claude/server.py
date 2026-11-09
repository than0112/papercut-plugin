#!/usr/bin/env python3
"""PaperCraft 3D: MCP server for Claude Code (stdio, Python standard library only).

Claude Code starts this process from the plugin's .mcp configuration. The protocol layer
here needs nothing beyond the standard library, so the server always starts. The imaging
stack (Pillow, numpy, scipy, scikit-image, jsonschema) lives in a virtualenv in the plugin
data directory and is put on sys.path the first time a tool needs it. If it is missing, the
tools answer with the exact setup commands for Claude to run (with the user's permission).

The pipeline is the same code the Hermes plugin uses. Where Hermes asks its vision model
through ctx.llm, this server hands the image and instructions to Claude itself and resumes
when Claude submits its answer (see flows.py).
"""

from __future__ import annotations

import json
import os
import sys
import sysconfig
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or (ROOT / ".papercraft-data"))
VENV = Path(os.environ.get("PAPERCRAFT_VENV") or (DATA / "venv"))
REQUIREMENTS = ROOT / "claude" / "requirements.txt"
PROTOCOL_FALLBACK = "2025-06-18"


def _version() -> str:
    text = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
    return text.split('VERSION = "', 1)[1].split('"', 1)[0]


VERSION = _version()


def log(*parts: object) -> None:
    print("[papercraft-3d]", *parts, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- dependencies


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_site_packages() -> list[str]:
    paths = []
    for key in ("purelib", "platlib"):
        try:
            p = sysconfig.get_path(key, scheme="venv", vars={"base": str(VENV), "platbase": str(VENV)})
        except KeyError:  # Python < 3.11 has no 'venv' scheme
            p = sysconfig.get_path(key, vars={"base": str(VENV), "platbase": str(VENV)})
        if p and p not in paths:
            paths.append(p)
    return paths


def setup_instructions() -> str:
    py = sys.executable
    return (
        "PaperCraft 3D needs its imaging libraries before this tool can run. Ask the user, then run "
        "these two commands (they create a virtualenv in the plugin's data folder, using the same "
        "Python as this server, and install Pillow, numpy, jsonschema, scipy and scikit-image):\n\n"
        f'"{py}" -m venv "{VENV}"\n'
        f'"{venv_python()}" -m pip install -r "{REQUIREMENTS}"\n\n'
        f"With uv installed this is faster:\nuv venv --python \"{py}\" \"{VENV}\"\n"
        f'uv pip install --python "{venv_python()}" -r "{REQUIREMENTS}"\n\n'
        "Then call the tool again; no restart is needed."
    )


_flows = None


def flows():
    """Import the pipeline lazily, from the plugin virtualenv."""
    global _flows
    if _flows is not None:
        return _flows
    for p in venv_site_packages():
        if Path(p).is_dir() and p not in sys.path:
            sys.path.insert(0, p)
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import jsonschema  # noqa: F401
        import scipy  # noqa: F401
        import skimage  # noqa: F401
    except ImportError as exc:
        raise DependencyMissing(str(exc)) from exc
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import flows as module  # claude/flows.py

    _flows = module
    return module


class DependencyMissing(Exception):
    pass


# --------------------------------------------------------------------------- tools

PROJECT_ID = {"type": "string", "description": "The project_id returned by papercraft_analyze_begin."}

CLAUDE_TOOLS = [
    {
        "name": "papercraft_status",
        "description": "Check the PaperCraft 3D setup (imaging libraries, workspace) and list projects with the "
                       "state of each pipeline stage. Call this first if unsure.",
        "inputSchema": {"type": "object", "properties": {"project_id": PROJECT_ID}, "additionalProperties": False},
    },
    {
        "name": "papercraft_analyze_begin",
        "description": "Step 1 of turning ONE paper-cut / papercraft image (PNG, JPEG, WebP, max 10 MB) into a "
                       "layered Three.js 2.5D scene. Validates the image, creates a project (or reuses project_id) and "
                       "returns the image with analysis instructions and the JSON schema. YOU are the vision model: "
                       "study the image and answer with papercraft_analyze_submit.",
        "inputSchema": {"type": "object", "properties": {
            "image_path": {"type": "string", "description": "Path of the image file (new project)."},
            "project_id": {"type": "string", "description": "Re-analyse this existing project instead."},
            "project_name": {"type": "string"},
            "brief": {"type": "string", "description": "The user's requirements, in their language."},
            "max_layers": {"type": "integer", "minimum": 3, "maximum": 8, "default": 6},
        }, "additionalProperties": False},
    },
    {
        "name": "papercraft_analyze_submit",
        "description": "Submit your analysis JSON for the image from papercraft_analyze_begin. It is validated "
                       "against the schema and semantic rules and its palette is checked against the pixels. If it "
                       "is rejected you get the exact problems and one chance to resubmit.",
        "inputSchema": {"type": "object", "properties": {
            "project_id": PROJECT_ID,
            "analysis": {"type": "object", "description": "The complete analysis object."},
        }, "required": ["project_id", "analysis"], "additionalProperties": False},
    },
    {
        "name": "papercraft_segment_begin",
        "description": "Step 2. Divides the analysed image into numbered regions along its cut lines and returns the "
                       "original plus the numbered image. YOU label every number with a layer id via "
                       "papercraft_segment_submit.",
        "inputSchema": {"type": "object", "properties": {"project_id": PROJECT_ID},
                        "required": ["project_id"], "additionalProperties": False},
    },
    {
        "name": "papercraft_segment_submit",
        "description": "Submit region labels: assignments maps EVERY region number (as a string) to a layer id; list "
                       "regions that mix layers in 'uncertain'. Uncertain regions come back split into smaller "
                       "numbered parts for a second round (submit those the same way). When labelling is complete "
                       "the layers, masks and layers.json are written and the overlay image is returned for review.",
        "inputSchema": {"type": "object", "properties": {
            "project_id": PROJECT_ID,
            "assignments": {"type": "object", "additionalProperties": {"type": "string"}},
            "uncertain": {"type": "array", "items": {"type": "integer"}},
            "notes": {"type": "string"},
        }, "required": ["project_id", "assignments"], "additionalProperties": False},
    },
    {
        "name": "papercraft_segment_correct",
        "description": "Correct layers with user-painted masks (layer id -> path of a PNG/JPEG/WebP the size of the "
                       "source; white/opaque = belongs to that layer). Reuses the previous result, changes only those "
                       "pixels and records them as manual.",
        "inputSchema": {"type": "object", "properties": {
            "project_id": PROJECT_ID,
            "manual_masks": {"type": "object", "additionalProperties": {"type": "string"}},
        }, "required": ["project_id", "manual_masks"], "additionalProperties": False},
    },
]

SHARED = ("build_scene", "generate_plan", "update_scene", "export")
_fallback_served = False  # tools/list was answered before the imaging libraries were installed


def shared_tools() -> list[dict]:
    """Same tools as the Hermes plugin; schemas read without importing the imaging stack."""
    out = []
    for mod in SHARED:
        try:
            schema = flows().shared_schema(mod)
        except DependencyMissing:
            global _fallback_served
            _fallback_served = True
            schema = {"name": f"papercraft_{'export_project' if mod == 'export' else mod}",
                      "description": "Requires the PaperCraft imaging libraries (see papercraft_status).",
                      "parameters": {"type": "object", "properties": {"project_id": PROJECT_ID}}}
        out.append({"name": schema["name"], "description": schema["description"], "inputSchema": schema["parameters"]})
    return out


def text(obj) -> dict:
    return {"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)}


def call_tool(name: str, args: dict) -> dict:
    if name == "papercraft_status":
        try:
            return {"content": flows().status(args), "isError": False}
        except DependencyMissing as exc:
            return {"content": [text({"ready": False, "missing": str(exc), "venv": str(VENV),
                                      "plugin_version": VERSION}), text(setup_instructions())], "isError": False}
    global _fallback_served
    try:
        f = flows()
    except DependencyMissing:
        return {"content": [text(setup_instructions())], "isError": True}
    if _fallback_served:  # libraries were installed meanwhile: let the client fetch the full schemas
        _fallback_served = False
        notify("notifications/tools/list_changed")
    content, is_error = f.dispatch(name, args)
    return {"content": content, "isError": is_error}


# --------------------------------------------------------------------------- JSON-RPC over stdio


def respond(msg_id, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def notify(method: str) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if msg_id is None:  # notification (e.g. notifications/initialized)
        return
    if method == "initialize":
        respond(msg_id, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_FALLBACK,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "papercraft-3d", "version": VERSION},
            "instructions": "Turn a paper-cut image into a layered Three.js scene. Start with papercraft_status or "
                            "papercraft_analyze_begin; the papercraft skill describes the whole workflow.",
        })
    elif method == "ping":
        respond(msg_id, {})
    elif method == "tools/list":
        respond(msg_id, {"tools": CLAUDE_TOOLS + shared_tools()})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        known = {t["name"] for t in CLAUDE_TOOLS} | {f"papercraft_{m}" for m in SHARED[:-1]} | {"papercraft_export_project"}
        if name not in known:
            respond(msg_id, error={"code": -32602, "message": f"unknown tool {name!r}"})
            return
        try:
            respond(msg_id, call_tool(name, args))
        except Exception as exc:  # never let one call kill the server
            log("tool crashed:", traceback.format_exc())
            respond(msg_id, {"content": [text(f"Internal error in {name}: {type(exc).__name__}: {exc}")], "isError": True})
    else:
        respond(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> None:
    log(f"v{VERSION} started; plugin root {ROOT}; data {DATA}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            respond(None, error={"code": -32700, "message": "parse error"})
            continue
        for item in msg if isinstance(msg, list) else [msg]:
            handle(item)


if __name__ == "__main__":
    main()
