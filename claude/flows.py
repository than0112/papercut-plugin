"""Claude-in-the-loop flows for the PaperCraft MCP server.

The Hermes plugin asks a vision model through ctx.llm inside one tool call. In Claude Code
the vision model is Claude, the agent calling the tools, so each model step is split in two:

    *_begin   runs the real pipeline until it needs the model, then returns the image(s) and
              the exact instructions instead of calling a model;
    *_submit  stores Claude's answer and runs the SAME pipeline again, replaying every stored
              answer (ReplayProvider). Validation, the repair round, the second labelling round
              and all outputs are therefore identical to the Hermes plugin.

Over-segmentation is deterministic, so region numbers are the same on every replay.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PKG = "papercraft3d"


def _load_package():
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


_load_package()

from PIL import Image  # noqa: E402

from papercraft3d.core.errors import ErrorCode, PaperCraftError  # noqa: E402
from papercraft3d.core.image_provider import AnalysisResult  # noqa: E402
from papercraft3d.core.pipeline import Runtime, read_status, require_prerequisites, set_stage  # noqa: E402
from papercraft3d.core.security import require_id, validate_image  # noqa: E402
from papercraft3d.core.storage import Project, Workspace  # noqa: E402
from papercraft3d.core.version import VERSION  # noqa: E402
from papercraft3d.tools import analyze as t_analyze  # noqa: E402
from papercraft3d.tools import build_scene as t_build  # noqa: E402
from papercraft3d.tools import export as t_export  # noqa: E402
from papercraft3d.tools import generate_plan as t_plan  # noqa: E402
from papercraft3d.tools import segment as t_segment  # noqa: E402
from papercraft3d.tools import update_scene as t_update  # noqa: E402

SHARED_MODULES = {"build_scene": t_build, "generate_plan": t_plan, "update_scene": t_update, "export": t_export}
STATE = "claude/state.json"
MODEL_LABEL = "claude-code-session"


class NeedInput(Exception):
    """Raised by ReplayProvider when the pipeline needs an answer Claude has not given yet."""

    def __init__(self, images: list[tuple[bytes, str]], instructions: str, purpose: str):
        super().__init__(purpose)
        self.images, self.instructions, self.purpose = images, instructions, purpose


class ReplayProvider:
    """Answers model calls from Claude's stored submissions, in order; asks for the next one when they run out."""

    name = "claude-code"

    def __init__(self, answers: list[Any]):
        self.answers = list(answers)
        self.calls = 0

    def analyze(self, image_bytes: bytes, mime_type: str, *, instructions: str, timeout: float) -> AnalysisResult:
        return self.structured([(image_bytes, mime_type)], instructions=instructions, timeout=timeout,
                               purpose="papercraft.analyze", schema_name="papercraft_analysis")

    def structured(self, images, *, instructions, timeout, purpose, schema_name, max_tokens=0) -> AnalysisResult:
        if self.calls < len(self.answers):
            answer = self.answers[self.calls]
            self.calls += 1
            return AnalysisResult(data=answer if isinstance(answer, dict) else None, provider="claude-code",
                                  model=MODEL_LABEL, raw_text=json.dumps(answer, ensure_ascii=False))
        raise NeedInput(images, instructions, purpose)


# --------------------------------------------------------------------------- helpers


def workspace_root() -> Path:
    if os.environ.get("PAPERCRAFT_WORKSPACE", "").strip():
        return Path(os.environ["PAPERCRAFT_WORKSPACE"]).expanduser()
    base = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return Path(base) / "papercraft-projects"


def runtime(provider: Any = None) -> Runtime:
    return Runtime(ctx=None, workspace_override=str(workspace_root()), analysis_provider_override=provider)


def text(obj: Any) -> dict:
    return {"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)}


def image(data: bytes, mime: str, max_side: int | None = None) -> dict:
    """Image block for Claude; PNGs without transparency are sent as JPEG to keep results small."""
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        if max_side:
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if img.mode in ("RGBA", "LA", "P") and "A" in img.getbands():
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/png"}
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=88)
    return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/jpeg"}


def load_state(project: Project) -> dict:
    return project.read_json(STATE) if project.exists(STATE) else {}


def save_state(project: Project, state: dict) -> None:
    project.write_json(STATE, state)


def error_content(exc: PaperCraftError) -> list[dict]:
    return [text({"ok": False, "pluginVersion": VERSION, "error": exc.to_dict()})]


def open_project(project_id: Any) -> Project:
    if not isinstance(project_id, str):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "project_id is required.")
    return Workspace(workspace_root()).open_project(require_id(project_id, "project_id"))


def shared_schema(mod: str) -> dict:
    return SHARED_MODULES[mod].SCHEMA


# --------------------------------------------------------------------------- analyze


def analyze_begin(args: dict) -> list[dict]:
    rt = runtime()
    image_path, project_id, max_layers, brief, name = t_analyze._arguments({"max_layers": 6, **args})
    project, info = t_analyze._open(rt, image_path, project_id, name)
    state = load_state(project)
    state["analyze"] = {"answers": [], "max_layers": max_layers, "brief": brief}
    save_state(project, state)
    return _run_analyze(project, info, state)


def analyze_submit(args: dict) -> list[dict]:
    project = open_project(args.get("project_id"))
    state = load_state(project)
    if "analyze" not in state:
        raise PaperCraftError(ErrorCode.PRECONDITION_FAILED, "Call papercraft_analyze_begin for this project first.")
    if not isinstance(args.get("analysis"), dict):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "analysis must be the complete JSON object.")
    state["analyze"]["answers"].append(args["analysis"])
    save_state(project, state)
    meta = project.read_json("project.json")
    info = validate_image(project.path(meta["source"]["path"]))
    return _run_analyze(project, info, state)


def _run_analyze(project: Project, info, state: dict) -> list[dict]:
    st = state["analyze"]
    rt = runtime(ReplayProvider(st["answers"]))
    set_stage(project, "analyze", "running")
    try:
        doc, _ = t_analyze._analyse(project, info, rt, max_layers=st["max_layers"], brief=st["brief"])
    except NeedInput as need:
        set_stage(project, "analyze", "pending", mode="awaiting-claude")
        first = not st["answers"]
        steps = ("You are the vision model. Study the image below carefully, then call papercraft_analyze_submit "
                 f"with project_id=\"{project.id}\" and analysis=<one JSON object that follows the schema>. "
                 "Do not invent anything that is not visible.") if first else (
                 "Your analysis was rejected; the problems are listed at the end of the instructions. Fix them and "
                 f"call papercraft_analyze_submit again with project_id=\"{project.id}\" (the image is unchanged).")
        content = [text({"project_id": project.id, "status": "awaiting your analysis", "next": steps}),
                   text(need.instructions)]
        if first:
            content += [image(data, mime) for data, mime in need.images]
        return content
    except PaperCraftError as exc:
        exc.details.setdefault("project_id", project.id)
        set_stage(project, "analyze", "failed", error=exc.to_dict())
        state.pop("analyze", None)
        save_state(project, state)
        exc.details["next"] = "Start over with papercraft_analyze_begin(project_id=...)."
        raise
    set_stage(project, "analyze", "succeeded")
    state.pop("analyze", None)
    save_state(project, state)
    return [text({"ok": True, "pluginVersion": VERSION, "data": t_analyze._summary(project, doc),
                  "warnings": doc.get("warnings", []),
                  "next": "Show the user the needs_review items, then call papercraft_segment_begin."})]


# --------------------------------------------------------------------------- segment


def segment_begin(args: dict) -> list[dict]:
    project = open_project(args.get("project_id"))
    require_prerequisites(project, "segment")
    state = load_state(project)
    state["segment"] = {"answers": []}
    save_state(project, state)
    return _run_segment(project, state)


def segment_submit(args: dict) -> list[dict]:
    project = open_project(args.get("project_id"))
    state = load_state(project)
    if "segment" not in state:
        raise PaperCraftError(ErrorCode.PRECONDITION_FAILED, "Call papercraft_segment_begin for this project first.")
    if not isinstance(args.get("assignments"), dict):
        raise PaperCraftError(ErrorCode.INVALID_ARGUMENT, "assignments must map region numbers to layer ids.")
    state["segment"]["answers"].append({"assignments": args["assignments"], "uncertain": args.get("uncertain", []),
                                        "notes": args.get("notes", "")})
    save_state(project, state)
    return _run_segment(project, state)


def _run_segment(project: Project, state: dict) -> list[dict]:
    analysis = project.read_json("analysis.json")
    layers = sorted(analysis["layers"], key=lambda layer: layer["order"])
    source = str(project.path(project.read_json("project.json")["source"]["path"]))
    answers = state["segment"]["answers"]
    rt = runtime(ReplayProvider(answers))
    set_stage(project, "segment", "running")
    try:
        result, reused = t_segment._ownership(project, source, layers, rt, reuse=False)
    except NeedInput as need:
        set_stage(project, "segment", "pending", mode="awaiting-claude")
        refine = need.purpose.endswith("refine")
        first = need.purpose.endswith("labels") and not answers
        if refine:
            steps = ("Second round: the regions you marked uncertain, plus any whose colours match neighbours of a different "
                     "layer, were split into smaller parts. In the image below "
                     "everything else is darkened. Label every bright numbered part and call papercraft_segment_submit "
                     f"again with project_id=\"{project.id}\".")
            images = [need.images[-1]]
        elif first:
            steps = ("You are the vision model. Image 1 is the original, image 2 shows numbered regions. Assign EVERY "
                     "number to one layer id and list mixed regions in 'uncertain', then call papercraft_segment_submit "
                     f"with project_id=\"{project.id}\".")
            images = need.images
        else:
            steps = ("Your labels had problems (listed at the end of the instructions). Fix them and call "
                     f"papercraft_segment_submit again with project_id=\"{project.id}\".")
            images = []
        return [text({"project_id": project.id, "status": "awaiting your region labels", "next": steps}),
                text(need.instructions)] + [image(data, mime) for data, mime in images]
    except PaperCraftError as exc:
        exc.details.setdefault("project_id", project.id)
        set_stage(project, "segment", "failed", error=exc.to_dict())
        state.pop("segment", None)
        save_state(project, state)
        raise
    manifest = t_segment._write_layers(project, source, layers, result, [], reused)
    set_stage(project, "segment", "succeeded", mode="layered")
    state.pop("segment", None)
    save_state(project, state)
    overlay = project.path("review/overlay.png").read_bytes()
    return [text({"ok": True, "pluginVersion": VERSION, "data": t_segment._summary(project, manifest),
                  "warnings": manifest["warnings"],
                  "next": "Check the overlay below (each colour is one layer). If a region is in the wrong layer, "
                          "fix it with papercraft_segment_correct; otherwise call papercraft_build_scene."}),
            image(overlay, "image/png", max_side=1280)]


def segment_correct(args: dict) -> list[dict]:
    project = open_project(args.get("project_id"))
    if not (project.exists("layers.json") and project.exists(t_segment.OWNER_PATH)):
        raise PaperCraftError(ErrorCode.PRECONDITION_FAILED, "Segment the project first (papercraft_segment_begin).")
    res = json.loads(t_segment.handle({"project_id": project.id, "manual_masks": args.get("manual_masks")}, runtime()))
    content = [text(res)]
    if res.get("ok"):
        content.append(image(project.path("review/overlay.png").read_bytes(), "image/png", max_side=1280))
    return content


# --------------------------------------------------------------------------- status and shared tools


def status(args: dict) -> list[dict]:
    root = workspace_root()
    info = {"ready": True, "plugin_version": VERSION, "workspace": str(root)}
    if args.get("project_id"):
        project = open_project(args["project_id"])
        info["project"] = {"id": project.id, "dir": str(project.dir), "stages": read_status(project)["stages"],
                           "files": sorted(p.name for p in project.dir.iterdir())}
    else:
        ws = Workspace(root)
        info["projects"] = [{"id": pid, "stages": {k: v["state"] for k, v in read_status(ws.open_project(pid))["stages"].items()}}
                            for pid in ws.list_projects()]
    return [text(info)]


def _viewer_hint(data: dict) -> dict:
    viewer = ROOT / "viewer"
    return {
        "viewer_dir": str(viewer),
        "first_time": f'cd "{viewer}" && npm install',
        "start_in_background": f'cd "{viewer}" && PAPERCRAFT_PROJECT="{Path(data["scene_path"]).parent}" npx vite --port 5173',
        "url": "http://127.0.0.1:5173/?scene=project/scene.json",
    }


def dispatch(name: str, args: dict) -> tuple[list[dict], bool]:
    handlers = {"papercraft_analyze_begin": analyze_begin, "papercraft_analyze_submit": analyze_submit,
                "papercraft_segment_begin": segment_begin, "papercraft_segment_submit": segment_submit,
                "papercraft_segment_correct": segment_correct}
    try:
        if name in handlers:
            content = handlers[name](args)
            first = json.loads(content[0]["text"]) if content and content[0]["type"] == "text" else {}
            return content, first.get("ok") is False
        mod = {"papercraft_build_scene": t_build, "papercraft_generate_plan": t_plan,
               "papercraft_update_scene": t_update, "papercraft_export_project": t_export}[name]
        res = json.loads(mod.handle(args, runtime()))
        if name == "papercraft_build_scene" and res.get("ok"):
            res["data"]["preview"] = _viewer_hint(res["data"])
        return [text(res)], not res.get("ok", False)
    except PaperCraftError as exc:
        return error_content(exc), True
