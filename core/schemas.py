"""Load and apply the versioned JSON Schemas in ``schemas/``.

``scene.schema.json`` is the single source of truth shared by the Python
pipeline, the Three.js viewer (``viewer/src/types/scene.ts`` mirrors it) and
the design-spec generator.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .errors import ErrorCode, PaperCraftError

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
SCENE_SCHEMA_VERSION = "1.0"

# schema name -> error code raised when a document fails it
_ERROR_CODES = {
    "scene": ErrorCode.SCENE_INVALID,
    "analysis": ErrorCode.MODEL_OUTPUT_INVALID,
    "layers": ErrorCode.SEGMENTATION_FAILED,
}


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / f"{name}.schema.json"
    with path.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name))


def schema_errors(name: str, document: Any, *, limit: int = 20) -> list[str]:
    """Return human-readable validation errors (empty list = valid)."""
    errors = sorted(_validator(name).iter_errors(document), key=lambda e: list(e.absolute_path))
    out = []
    for err in errors[:limit]:
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        out.append(f"{where}: {err.message}")
    return out


def scene_semantic_errors(scene: dict[str, Any], base_dir: Path | None = None) -> list[str]:
    """Checks JSON Schema cannot express. Call after schema validation passes."""
    problems: list[str] = []
    layers = scene.get("layers", [])
    ids = [layer["id"] for layer in layers]
    orders = [layer["order"] for layer in layers]
    if len(set(ids)) != len(ids):
        problems.append("layers: layer ids must be unique")
    if len(set(orders)) != len(orders):
        problems.append("layers: layer order values must be unique")
    for i, layer in enumerate(layers):
        if layer.get("geometry", {}).get("type") in ("shape", "extrude") and "outline" not in layer["geometry"]:
            problems.append(f"layers/{i}/geometry: '{layer['geometry']['type']}' needs an outline file")
    if scene.get("mode") == "layered" and any(layer["provenance"]["source"] == "preview" for layer in layers):
        problems.append("mode: a 'layered' scene cannot contain 'preview' layers")
    cam = scene.get("camera", {})
    if cam.get("minZoom", 0.5) > cam.get("maxZoom", 3):
        problems.append("camera: minZoom is greater than maxZoom")
    if base_dir is not None:
        rels = [scene["source"]["image"]] + [layer["asset"] for layer in layers]
        rels += [layer["mask"] for layer in layers if "mask" in layer]
        for rel in rels:
            if not (Path(base_dir) / rel).is_file():
                problems.append(f"asset missing: {rel}")
    return problems


def validate_scene(scene: Any, base_dir: Path | None = None) -> None:
    """Full scene validation: schema, then semantic rules, then (optionally) asset existence."""
    validate("scene", scene)
    problems = scene_semantic_errors(scene, base_dir)
    if problems:
        missing = any(p.startswith("asset missing") for p in problems)
        raise PaperCraftError(
            ErrorCode.ASSET_MISSING if missing else ErrorCode.SCENE_INVALID,
            f"scene.json is not consistent ({len(problems)} problem(s)).",
            details={"errors": problems},
            retryable=False,
        )


def validate(name: str, document: Any) -> None:
    """Raise a classified ``PaperCraftError`` when ``document`` does not satisfy schema ``name``."""
    problems = schema_errors(name, document)
    if problems:
        raise PaperCraftError(
            _ERROR_CODES.get(name, ErrorCode.INVALID_ARGUMENT),
            f"{name} document failed schema validation ({len(problems)} problem(s)).",
            details={"schema": name, "errors": problems},
            retryable=False,
        )
