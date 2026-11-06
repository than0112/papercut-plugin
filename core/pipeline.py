"""Stage status tracking and the shared runtime handed to every tool.

Each project carries ``status.json``::

    {"stages": {"analyze": {"state": "succeeded", "mode": null, "updatedAt": "...", "error": null}, ...}}

States: pending | running | succeeded | degraded | failed.
``degraded`` is used for the flat "preview" fallback so it can never be
mistaken for a real layered reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ErrorCode, PaperCraftError
from .security import DEFAULT_MAX_BYTES
from .storage import Project, Workspace, utc_now

STAGES = ("analyze", "segment", "build_scene", "generate_plan", "update_scene", "export")
# stage -> stages that must have succeeded (or degraded) first
PREREQUISITES = {
    "analyze": (),
    "segment": ("analyze",),
    "build_scene": ("segment",),
    "generate_plan": ("build_scene",),
    "update_scene": ("build_scene",),
    "export": ("build_scene",),
}
DONE_STATES = {"succeeded", "degraded"}


def read_status(project: Project) -> dict[str, Any]:
    if project.exists("status.json"):
        return project.read_json("status.json")
    return {"stages": {s: {"state": "pending", "mode": None, "updatedAt": None, "error": None} for s in STAGES}}


def set_stage(
    project: Project, stage: str, state: str, *, mode: str | None = None, error: dict | None = None
) -> dict[str, Any]:
    status = read_status(project)
    status["stages"][stage] = {"state": state, "mode": mode, "updatedAt": utc_now(), "error": error}
    project.write_json("status.json", status)
    project.log("stage", stage=stage, state=state, mode=mode, error=error)
    return status


def require_prerequisites(project: Project, stage: str) -> None:
    stages = read_status(project)["stages"]
    missing = [s for s in PREREQUISITES[stage] if stages.get(s, {}).get("state") not in DONE_STATES]
    if missing:
        raise PaperCraftError(
            ErrorCode.PRECONDITION_FAILED,
            f"Run {', '.join('papercraft_' + s for s in missing)} before papercraft_{stage}.",
            details={"project_id": project.id, "missing": missing},
        )


@dataclass
class Runtime:
    """What tool handlers need from Hermes, resolved once at ``register(ctx)`` time."""

    ctx: Any = None
    workspace_override: str | None = None
    analysis_provider_override: Any = None  # tests inject a fake provider here

    def config(self, key: str, default: Any = None) -> Any:
        if self.ctx is None or not hasattr(self.ctx, "get_config"):
            return default
        try:
            value = self.ctx.get_config(key, default=default)
        except Exception:
            return default
        return default if value in (None, "") else value

    @property
    def workspace(self) -> Workspace:
        return Workspace(self.workspace_override or self.config("workspace_dir") or None)

    @property
    def max_image_bytes(self) -> int:
        try:
            mb = int(self.config("max_image_mb", 10))
        except (TypeError, ValueError):
            return DEFAULT_MAX_BYTES
        return max(1, min(mb, 10)) * 1024 * 1024  # MVP hard cap: 10 MB

    @property
    def llm(self) -> Any:
        return getattr(self.ctx, "llm", None) if self.ctx is not None else None

    @property
    def analysis_timeout(self) -> float:
        try:
            seconds = float(self.config("analysis_timeout_s", 180))
        except (TypeError, ValueError):
            seconds = 180.0
        return max(10.0, min(seconds, 600.0))

    @property
    def segmentation_backend(self) -> str:
        return str(self.config("segmentation_backend", "builtin"))

    @property
    def segmentation_timeout(self) -> float:
        try:
            seconds = float(self.config("segmentation_timeout_s", 300))
        except (TypeError, ValueError):
            seconds = 300.0
        return max(10.0, min(seconds, 1800.0))

    def analysis_provider(self) -> Any:
        if self.analysis_provider_override is not None:
            return self.analysis_provider_override
        from .image_provider import HermesLlmAnalysisProvider

        return HermesLlmAnalysisProvider(self.llm)
