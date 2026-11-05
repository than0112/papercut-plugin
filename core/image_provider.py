"""Replaceable model providers for analysis and segmentation.

Analysis
    ``HermesLlmAnalysisProvider`` uses ``ctx.llm.complete_structured`` — the
    user's active Hermes model with host-owned credentials and Hermes's own
    vision routing. The image only goes to the provider the user already
    chose for Hermes; this plugin holds no API keys.

Segmentation
    ``local``  in-process mask generation from the analysis plan (no network).
    ``http``   a service the user configures explicitly (``segmentation_url``).
    Both are implemented in phase 3. Until then they raise
    ``SEGMENTATION_FAILED`` / ``NOT_IMPLEMENTED`` honestly.

Everything a model returns is untrusted: callers must validate it against the
JSON Schemas before use (see ``core.schemas.validate``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import ErrorCode, PaperCraftError, not_implemented


@dataclass
class AnalysisResult:
    data: Any  # parsed JSON, still untrusted until schema-validated
    provider: str
    model: str
    raw_text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class AnalysisProvider(Protocol):
    name: str

    def analyze(
        self,
        image_bytes: bytes,
        mime_type: str,
        *,
        instructions: str,
        json_schema: dict[str, Any],
        timeout: float,
    ) -> AnalysisResult: ...


class HermesLlmAnalysisProvider:
    """Vision analysis through Hermes's ``ctx.llm`` (no keys, follows the user's model)."""

    name = "hermes-llm"

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    def analyze(
        self,
        image_bytes: bytes,
        mime_type: str,
        *,
        instructions: str,
        json_schema: dict[str, Any],
        timeout: float,
    ) -> AnalysisResult:
        if self._llm is None:
            raise PaperCraftError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Hermes LLM access (ctx.llm) is not available in this environment.",
            )
        try:
            result = self._llm.complete_structured(
                instructions=instructions,
                input=[{"type": "image", "data": image_bytes, "mime_type": mime_type}],
                json_schema=json_schema,
                schema_name="papercraft_analysis",
                temperature=0.2,
                timeout=timeout,
                purpose="papercraft.analyze",
            )
        except TimeoutError as exc:
            raise PaperCraftError(ErrorCode.TIMEOUT, f"Vision model timed out after {timeout}s.") from exc
        except Exception as exc:  # provider errors are not under our control
            raise PaperCraftError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"Vision model call failed: {type(exc).__name__}: {exc}",
            ) from exc
        if getattr(result, "content_type", None) != "json" or result.parsed is None:
            raise PaperCraftError(
                ErrorCode.MODEL_OUTPUT_INVALID,
                "The model did not return JSON matching the analysis schema.",
                details={"raw_text": (getattr(result, "text", "") or "")[:2000]},
            )
        usage = getattr(result, "usage", None)
        return AnalysisResult(
            data=result.parsed,
            provider=getattr(result, "provider", "") or "",
            model=getattr(result, "model", "") or "",
            raw_text=getattr(result, "text", "") or "",
            usage=dict(vars(usage)) if usage is not None and hasattr(usage, "__dict__") else {},
        )


class SegmentationProvider(Protocol):
    name: str

    def segment(self, image_path: str, analysis: dict[str, Any], *, max_layers: int, timeout: float) -> Any: ...


class LocalSegmentationProvider:
    name = "local"

    def segment(self, image_path: str, analysis: dict[str, Any], *, max_layers: int, timeout: float) -> Any:
        raise not_implemented("papercraft_segment", "phase 3: layer conversion")


class HttpSegmentationProvider:
    name = "http"

    def __init__(self, url: str) -> None:
        if not url.startswith("https://"):
            raise PaperCraftError(
                ErrorCode.INVALID_ARGUMENT,
                "segmentation_url must be an https:// URL you configured explicitly.",
            )
        self.url = url

    def segment(self, image_path: str, analysis: dict[str, Any], *, max_layers: int, timeout: float) -> Any:
        raise not_implemented("papercraft_segment", "phase 3: layer conversion")


def make_segmentation_provider(backend: str, url: str = "") -> SegmentationProvider:
    if backend == "local":
        return LocalSegmentationProvider()
    if backend == "http":
        return HttpSegmentationProvider(url)
    raise PaperCraftError(
        ErrorCode.INVALID_ARGUMENT, f"Unknown segmentation_backend {backend!r} (use 'local' or 'http')."
    )
