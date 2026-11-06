"""Error taxonomy and the JSON result envelope every tool returns.

Hermes tool handlers must return a JSON string and must never raise. Every
PaperCraft tool returns one of two shapes:

    {"ok": true,  "tool": "...", "data": {...}, "warnings": [...]}
    {"ok": false, "tool": "...", "error": {"code": "...", "message": "...",
                                          "retryable": bool, "details": {...}}}

A failed stage is always reported as ``ok: false``. A degraded result (for
example the flat "preview" mode when segmentation is unavailable) is
``ok: true`` with ``data.mode == "preview"`` and an explicit warning, never a
silent success.
"""

from __future__ import annotations

import functools
import json
import logging
from enum import Enum
from typing import Any, Callable

from .version import VERSION

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    # Input
    INVALID_ARGUMENT = "invalid_argument"
    IMAGE_FORMAT = "image_format"  # unsupported / corrupt / undecodable image
    IMAGE_TOO_LARGE = "image_too_large"  # bytes or pixel count over the limit
    PATH_FORBIDDEN = "path_forbidden"  # path escapes the authorised workspace
    PROJECT_NOT_FOUND = "project_not_found"
    PRECONDITION_FAILED = "precondition_failed"  # an earlier stage has not run
    # Model / services
    MODEL_UNAVAILABLE = "model_unavailable"  # vision model / service unreachable
    MODEL_OUTPUT_INVALID = "model_output_invalid"  # model replied, but not schema-valid
    SEGMENTATION_FAILED = "segmentation_failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    # Scene / assets / output
    SCENE_INVALID = "scene_invalid"  # scene.json or a patch fails schema validation
    ASSET_MISSING = "asset_missing"
    VIEWER_BUILD_FAILED = "viewer_build_failed"
    EXPORT_FAILED = "export_failed"
    # Other
    NOT_IMPLEMENTED = "not_implemented"
    INTERNAL = "internal_error"


_RETRYABLE = {ErrorCode.MODEL_UNAVAILABLE, ErrorCode.TIMEOUT, ErrorCode.SEGMENTATION_FAILED}


class PaperCraftError(Exception):
    """An expected, classified failure. Raised inside tools, rendered by ``tool_handler``."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = code in _RETRYABLE if retryable is None else retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def ok(tool: str, data: dict[str, Any], warnings: list[str] | None = None) -> str:
    return json.dumps(
        {"ok": True, "tool": tool, "pluginVersion": VERSION, "data": data, "warnings": warnings or []},
        ensure_ascii=False,
    )


def fail(tool: str, err: PaperCraftError) -> str:
    return json.dumps({"ok": False, "tool": tool, "pluginVersion": VERSION, "error": err.to_dict()},
                      ensure_ascii=False)


def not_implemented(tool: str, phase: str) -> PaperCraftError:
    return PaperCraftError(
        ErrorCode.NOT_IMPLEMENTED,
        f"{tool} is registered but not implemented yet ({phase}). "
        "No output was produced; do not report this step as completed.",
        details={"phase": phase},
        retryable=False,
    )


def tool_handler(tool: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Wrap a handler so it always returns a JSON envelope and never raises."""

    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                return fn(*args, **kwargs)
            except PaperCraftError as exc:
                logger.info("%s failed: %s (%s)", tool, exc.code.value, exc.message)
                return fail(tool, exc)
            except Exception as exc:  # noqa: BLE001 - handlers must never raise
                logger.exception("%s crashed", tool)
                return fail(
                    tool,
                    PaperCraftError(
                        ErrorCode.INTERNAL,
                        f"Unexpected error in {tool}: {type(exc).__name__}: {exc}",
                        retryable=False,
                    ),
                )

        return wrapper

    return decorator
