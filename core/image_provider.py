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
    data: Any  # parsed JSON or None; untrusted until validated by core.analysis.review_analysis
    provider: str
    model: str
    raw_text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class AnalysisProvider(Protocol):
    name: str

    def analyze(self, image_bytes: bytes, mime_type: str, *, instructions: str, timeout: float) -> AnalysisResult: ...


class HermesLlmAnalysisProvider:
    """Vision analysis through Hermes's ``ctx.llm`` (no keys, follows the user's model).

    Uses ``json_mode=True`` with the schema embedded in the instructions rather
    than ``json_schema=``: with ``json_schema`` Hermes raises on a schema
    mismatch and the raw reply is lost, which makes a targeted repair attempt
    impossible. Validation happens in ``core.analysis`` instead.
    """

    name = "hermes-llm"

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    def analyze(self, image_bytes: bytes, mime_type: str, *, instructions: str, timeout: float) -> AnalysisResult:
        if self._llm is None:
            raise PaperCraftError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Hermes LLM access (ctx.llm) is not available in this environment.",
            )
        try:
            result = self._llm.complete_structured(
                instructions=instructions,
                input=[{"type": "image", "data": image_bytes, "mime_type": mime_type}],
                json_mode=True,
                schema_name="papercraft_analysis",
                temperature=0.2,
                max_tokens=12000,
                timeout=timeout,
                purpose="papercraft.analyze",
            )
        except TimeoutError as exc:
            raise PaperCraftError(ErrorCode.TIMEOUT, f"Vision model timed out after {timeout:.0f}s.") from exc
        except Exception as exc:  # provider errors are not under our control
            raise PaperCraftError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"Vision model call failed: {type(exc).__name__}: {exc}. "
                "Check that Hermes has a vision-capable model configured (`hermes model`).",
            ) from exc
        usage = getattr(result, "usage", None)
        parsed = result.parsed if getattr(result, "content_type", None) == "json" else None
        return AnalysisResult(
            data=parsed,
            provider=getattr(result, "provider", "") or "",
            model=getattr(result, "model", "") or "",
            raw_text=getattr(result, "text", "") or "",
            usage={k: v for k, v in vars(usage).items() if isinstance(v, (int, float))}
            if usage is not None and hasattr(usage, "__dict__") else {},
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
