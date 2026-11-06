"""Replaceable model providers for analysis and segmentation.

Analysis
    ``HermesLlmAnalysisProvider`` uses ``ctx.llm.complete_structured`` — the
    user's active Hermes model with host-owned credentials and Hermes's own
    vision routing. The image only goes to the provider the user already
    chose for Hermes; this plugin holds no API keys.

Segmentation backends live in ``core.segment_backends``; the built-in one uses
``structured()`` here to have the vision model label numbered regions.

Everything a model returns is untrusted: callers must validate it against the
JSON Schemas before use (see ``core.schemas.validate``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import ErrorCode, PaperCraftError


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
        return self.structured([(image_bytes, mime_type)], instructions=instructions, timeout=timeout,
                               purpose="papercraft.analyze", schema_name="papercraft_analysis")

    def structured(self, images: list[tuple[bytes, str]], *, instructions: str, timeout: float,
                   purpose: str, schema_name: str, max_tokens: int = 12000) -> AnalysisResult:
        """One JSON-mode call with one or more images; the reply is returned unvalidated."""
        if self._llm is None:
            raise PaperCraftError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Hermes LLM access (ctx.llm) is not available in this environment.",
            )
        try:
            result = self._llm.complete_structured(
                instructions=instructions,
                input=[{"type": "image", "data": data, "mime_type": mime} for data, mime in images],
                json_mode=True,
                schema_name=schema_name,
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=timeout,
                purpose=purpose,
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
