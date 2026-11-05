"""Input validation and path confinement.

Rules (企劃書 §六):
- Output paths must stay inside the authorised workspace (``resolve_within``).
- Images are validated by decoding them, not by trusting the file extension.
- Identifiers that end up in paths are reduced to a safe slug.
- Nothing here (or anywhere in the plugin) builds shell commands.
"""

from __future__ import annotations

import hashlib
import re
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import ErrorCode, PaperCraftError

ALLOWED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
# 40 MP is far beyond any concept image we expect; guards against decompression bombs.
MAX_PIXELS = 40_000_000
MIN_SIDE = 64

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class ImageInfo:
    path: str
    format: str
    mime_type: str
    width: int
    height: int
    mode: str
    has_alpha: bool
    bytes: int
    sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_within(base: Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` (absolute or relative to ``base``) and require it stays in ``base``."""
    base_resolved = Path(base).resolve()
    path = Path(candidate)
    target = (path if path.is_absolute() else base_resolved / path).resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise PaperCraftError(
            ErrorCode.PATH_FORBIDDEN,
            "Path is outside the PaperCraft workspace.",
            details={"path": str(candidate), "workspace": str(base_resolved)},
        )
    return target


def safe_id(value: str, *, fallback: str = "scene") -> str:
    """Reduce arbitrary text (file names, model output) to a path-safe identifier."""
    slug = re.sub(r"[^a-z0-9_]+", "-", value.lower()).strip("-_")[:64]
    return slug if slug and _ID_RE.match(slug) else fallback


def require_id(value: str, field: str = "id") -> str:
    """Validate an identifier supplied by a caller (no silent rewriting)."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise PaperCraftError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field} must match {_ID_RE.pattern}",
            details={"field": field, "value": value},
        )
    return value


def validate_image(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> ImageInfo:
    """Open and fully decode an image, enforcing format, size and pixel limits."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise PaperCraftError(
            ErrorCode.INVALID_ARGUMENT, "Image file not found.", details={"path": str(path)}
        )
    size = p.stat().st_size
    if size > max_bytes:
        raise PaperCraftError(
            ErrorCode.IMAGE_TOO_LARGE,
            f"Image is {size / 1_048_576:.1f} MB; the limit is {max_bytes / 1_048_576:.0f} MB.",
            details={"bytes": size, "max_bytes": max_bytes},
        )
    data = p.read_bytes()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(p) as probe:
                fmt = probe.format or ""
                width, height = probe.size
                if width * height > MAX_PIXELS:
                    raise PaperCraftError(
                        ErrorCode.IMAGE_TOO_LARGE,
                        f"Image has {width}x{height} pixels; the limit is {MAX_PIXELS:,} pixels.",
                        details={"width": width, "height": height},
                    )
                probe.verify()  # structural check
            with Image.open(p) as img:
                img.load()  # full decode; verify() alone does not catch truncated data
                mode = img.mode
                has_alpha = mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    except PaperCraftError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PaperCraftError(
            ErrorCode.IMAGE_FORMAT, f"Not a decodable image: {exc}", details={"path": str(path)}
        ) from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise PaperCraftError(
            ErrorCode.IMAGE_FORMAT, f"Image is corrupt or truncated: {exc}", details={"path": str(path)}
        ) from exc

    if fmt not in ALLOWED_FORMATS:
        raise PaperCraftError(
            ErrorCode.IMAGE_FORMAT,
            f"Unsupported image format {fmt or 'unknown'}; use PNG, JPEG or WebP.",
            details={"format": fmt},
        )
    if min(width, height) < MIN_SIDE:
        raise PaperCraftError(
            ErrorCode.IMAGE_FORMAT,
            f"Image is too small ({width}x{height}); each side must be at least {MIN_SIDE}px.",
        )
    return ImageInfo(
        path=str(p.resolve()),
        format=fmt,
        mime_type=ALLOWED_FORMATS[fmt],
        width=width,
        height=height,
        mode=mode,
        has_alpha=has_alpha,
        bytes=size,
        sha256=hashlib.sha256(data).hexdigest(),
    )
