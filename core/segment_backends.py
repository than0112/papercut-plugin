"""Segmentation backends. Each returns a full-resolution layer-ownership map.

``builtin``  over-segmentation on this machine + Set-of-Mark labelling by the
             user's Hermes vision model (``ctx.llm``). The image goes only to the
             model the user already uses for Hermes.
``http``     a segmentation service the user runs or trusts (see
             docs/SEGMENTATION_HTTP.md). Only the URL the user configured is
             contacted; plain http is accepted for loopback addresses only.

Everything a model or service returns is untrusted and validated here.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import numpy as np
from PIL import Image

from . import segmentation as seg
from .analysis import prepare_model_image
from .errors import ErrorCode, PaperCraftError

LABEL_PROMPT_VERSION = "labels-v2"
MAX_LABEL_ATTEMPTS = 2
MIN_LABELLED_AREA = 0.8  # fraction of the marked area the labeller must cover after repair
HTTP_MAX_RESPONSE = 64 * 1024 * 1024
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
COARSE_MERGE_LAB = 30.0  # a merge threshold above this means regions mix clearly different colours
AUTO_REFINE_MAX = 3  # regions the colour check may add to the second round ...
REFINE_TOTAL_MAX = 8  # ... up to this many regions in total (model-flagged ones first)


def marks_for(n_layers: int) -> int:
    """Numbered regions shown to the labeller: more layers need finer regions (40..120)."""
    return int(min(120, max(40, 15 * n_layers)))


@dataclass
class BackendResult:
    owner: np.ndarray  # (H, W) int: layer index per pixel, -1 = transparent in the source
    confidence: list[float]  # per layer, 0..1
    method: str
    meta: dict[str, Any] = field(default_factory=dict)
    review_images: dict[str, Image.Image] = field(default_factory=dict)
    region_record: dict[str, Any] = field(default_factory=dict)  # saved for re-runs and audit
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- builtin


def label_instructions(layers: list[dict[str, Any]], numbers: list[int]) -> str:
    plan = "\n".join(
        f'- "{layer["id"]}" (order {layer["order"]}, {layer["role"]}): {layer["name"]}. {layer["description"]} '
        f'bbox={layer["bbox"]}'
        for layer in layers
    )
    ids = ", ".join(f'"{layer["id"]}"' for layer in layers)
    return f"""You are labelling regions of a paper-cut image for a layered 2.5D reconstruction.

Image 1 is the original. Image 2 is the same picture divided into numbered regions (white outlines,
white numbers with a black outline). Assign EVERY numbered region to exactly one of these depth
layers (order 0 = farthest):
{plan}

Rules:
- Decide by what the region shows in the original image, using the layer descriptions above.
- A soft dark band right next to a paper edge is a cast shadow: it belongs to the paper it falls ON
  (the farther layer), not to the piece that casts it.
- If a region contains parts of two or more layers (for example sky showing between blossoms that
  belong to a nearer layer), choose the layer covering most of it AND list its number in "uncertain".
  Uncertain regions are split into smaller parts and shown to you again, so flag every mixed region.
- Use only these layer ids: {ids}.

Return one JSON object:
{{"assignments": {{"1": "<layer id>", "2": "<layer id>", ...}}, "uncertain": [<numbers>], "notes": "<short>"}}
It must contain all numbers from 1 to {len(numbers)}."""


def refine_instructions(layers: list[dict[str, Any]], n_parts: int) -> str:
    plan = "\n".join(f'- "{layer["id"]}" (order {layer["order"]}): {layer["name"]}. {layer["description"]}'
                     for layer in layers)
    ids = ", ".join(f'"{layer["id"]}"' for layer in layers)
    return f"""Second pass of the same labelling task. Some regions seem to mix several depth layers
(you marked them uncertain, or their colours match neighbours of different layers), so they were split
into smaller parts. Judge each part afresh from the original image. Image 1 is the original. In image 2 everything else is darkened; only
the parts to decide are bright, outlined and numbered 1 to {n_parts}.

Assign EVERY numbered part to exactly one layer (order 0 = farthest):
{plan}

A soft dark band next to a paper edge is a cast shadow and belongs to the paper it falls on.
Use only these layer ids: {ids}. List parts that still mix layers in "uncertain".

Return one JSON object:
{{"assignments": {{"1": "<layer id>", ...}}, "uncertain": [<numbers>], "notes": "<short>"}}
It must contain all numbers from 1 to {n_parts}."""


def parse_labels(data: Any, n_marks: int, layer_ids: list[str]) -> tuple[dict[int, int], list[int], list[str]]:
    """Validate a labelling reply. Returns (number -> layer index, uncertain numbers, problems)."""
    problems: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("assignments"), dict):
        return {}, [], ['the reply must be an object with an "assignments" object']
    index = {lid: i for i, lid in enumerate(layer_ids)}
    out: dict[int, int] = {}
    for key, value in data["assignments"].items():
        try:
            number = int(str(key).strip())
        except ValueError:
            problems.append(f"assignment key {key!r} is not a region number")
            continue
        if not 1 <= number <= n_marks:
            problems.append(f"region {number} does not exist (numbers are 1..{n_marks})")
        elif value not in index:
            problems.append(f"region {number}: unknown layer id {value!r}")
        else:
            out[number] = index[value]
    missing = [n for n in range(1, n_marks + 1) if n not in out]
    if missing:
        problems.append(f"regions without a layer: {missing[:60]}")
    uncertain = [int(n) for n in data.get("uncertain", []) if isinstance(n, int) or str(n).isdigit()]
    return out, [n for n in uncertain if 1 <= n <= n_marks], problems


def run_builtin(source_path: str, layers: list[dict[str, Any]], provider: Any, *, timeout: float,
                log: Any = None) -> BackendResult:
    """Over-segment, have the vision model label the numbered regions, map back to full resolution."""
    rgba = seg.load_rgba(source_path)
    work, scale = seg.work_copy(rgba)
    regions = seg.oversegment(work, max_marks=marks_for(len(layers)))
    if len(regions.marked) < 2:
        raise PaperCraftError(ErrorCode.SEGMENTATION_FAILED, "The image has too little structure to segment.")
    marks = seg.render_marks(work, regions)
    original, mime, _ = prepare_model_image(source_path)
    layer_ids = [layer["id"] for layer in layers]
    base = label_instructions(layers, list(regions.numbers.values()))
    n_marks = len(regions.marked)

    instructions = base
    for attempt in range(1, MAX_LABEL_ATTEMPTS + 1):
        result = provider.structured([(original, mime), (seg.png_bytes(marks), "image/png")],
                                     instructions=instructions, timeout=timeout,
                                     purpose="papercraft.segment.labels", schema_name="papercraft_region_labels",
                                     max_tokens=4000)
        by_number, uncertain, problems = parse_labels(result.data, n_marks, layer_ids)
        if log:
            log(attempt=attempt, provider=result.provider, model=result.model, problems=problems[:10],
                raw=result.raw_text)
        if not problems:
            break
        if attempt < MAX_LABEL_ATTEMPTS:
            listed = "\n".join(f"- {p}" for p in problems[:20])
            instructions = (f"{base}\n\nYour previous answer had problems:\n{listed}\n"
                            f"Previous answer:\n{result.raw_text[:8000]}\nReturn the complete corrected JSON.")

    number_to_region = {n: r for r, n in regions.numbers.items()}
    marked_area = sum(int(regions.areas[r]) for r in regions.marked)
    labelled_area = sum(int(regions.areas[number_to_region[n]]) for n in by_number)
    if labelled_area < MIN_LABELLED_AREA * marked_area:
        raise PaperCraftError(
            ErrorCode.SEGMENTATION_FAILED,
            f"The vision model labelled only {labelled_area / marked_area:.0%} of the marked area.",
            details={"problems": problems[:20]},
            retryable=True,
        )
    warnings = []
    if regions.merge_threshold > COARSE_MERGE_LAB:
        warnings.append(f"the image is very detailed: regions had to be merged coarsely (Lab {regions.merge_threshold:.0f}), "
                        "so some regions may mix layers; check review/overlay.png")
    if problems:
        warnings.append(f"labelling incomplete after {MAX_LABEL_ATTEMPTS} attempts; "
                        f"{n_marks - len(by_number)} region(s) inherited a neighbour's layer")

    assignment = {number_to_region[n]: k for n, k in by_number.items()}
    uncertain_regions = {number_to_region[n] for n in uncertain}

    # Second round: split large uncertain regions and ask again about the parts only.
    refine_info: dict[str, Any] = {}
    review_images = {"marks": marks}
    min_px = seg.REFINE_MIN_AREA * regions.labels.size
    targets = sorted((r for r in uncertain_regions if regions.areas[r] >= min_px),
                     key=lambda r: -regions.areas[r])[:seg.REFINE_MAX_REGIONS]
    # The model does not always notice a region that mixes layers; check the colours against the neighbours.
    auto = [r for _, r in seg.mixed_suspects(regions, assignment, min_px) if r not in targets]
    auto = auto[:max(0, min(AUTO_REFINE_MAX, REFINE_TOTAL_MAX - len(targets)))]
    targets = targets + auto
    regions_numbers = regions.numbers
    if targets:
        refined, parent, parts = seg.subdivide(regions, targets)
        if len(parts) >= 2:
            refine_img = seg.render_refine_marks(work, refined, parts)
            try:
                second = provider.structured([(original, mime), (seg.png_bytes(refine_img), "image/png")],
                                             instructions=refine_instructions(layers, len(parts)), timeout=timeout,
                                             purpose="papercraft.segment.refine", schema_name="papercraft_region_labels",
                                             max_tokens=3000)
                part_labels, part_uncertain, part_problems = parse_labels(second.data, len(parts), layer_ids)
                if log:
                    log(attempt="refine", provider=second.provider, model=second.model,
                        problems=part_problems[:10], raw=second.raw_text)
            except PaperCraftError as exc:
                part_labels, part_uncertain, part_problems = {}, [], [str(exc.message)]
                warnings.append(f"refinement round failed ({exc.code.value}); first-round labels kept")
            for target in targets:
                assignment.pop(target, None)
                uncertain_regions.discard(target)
            for i, part in enumerate(parts, start=1):
                fallback = by_number_for(number_to_region, by_number, parent[part])
                if i in part_labels:
                    assignment[part] = part_labels[i]
                elif fallback is not None:
                    assignment[part] = fallback
                if i in part_uncertain or i not in part_labels:
                    uncertain_regions.add(part)
            regions = refined
            review_images["refine_marks"] = refine_img
            refine_info = {"regions": len(targets), "parts": len(parts), "labelled": len(part_labels),
                           "autoFlagged": sorted(regions_numbers[r] for r in auto),
                           "problems": part_problems[:10]}

    work_map, how, per_region = seg.region_layer_map(regions, assignment)
    owner = seg.upsample_and_refine(work_map, rgba[..., :3], scale, len(layers))
    owner = np.where(rgba[..., 3] >= 128, owner, -1)

    # Confidence: area-weighted trust in how each region got its layer.
    weight = {"labelled": 1.0, "inherited": 0.6, "default": 0.2}
    num = np.zeros(len(layers))
    den = np.zeros(len(layers))
    for r in np.nonzero(regions.areas)[0]:
        k = int(per_region[r])
        w = weight[how[r]] * (0.5 if r in uncertain_regions else 1.0)
        num[k] += w * regions.areas[r]
        den[k] += regions.areas[r]
    confidence = [round(float(num[k] / den[k]), 3) if den[k] else 0.0 for k in range(len(layers))]

    record = {
        "promptVersion": LABEL_PROMPT_VERSION,
        "regions": int(len(regions.areas)),
        "marked": n_marks,
        "mergeThreshold": regions.merge_threshold,
        "assignments": {str(n): layer_ids[k] for n, k in sorted(by_number.items())},
        "uncertain": sorted(uncertain),
        "refinement": refine_info,
        "notes": (result.data or {}).get("notes", "") if isinstance(result.data, dict) else "",
    }
    return BackendResult(
        owner=owner.astype(np.int16), confidence=confidence, method="builtin:felzenszwalb+set-of-mark",
        meta={"provider": result.provider, "model": result.model, "attempts": attempt,
              "regions": record["regions"], "marked": n_marks, "mergeThreshold": regions.merge_threshold,
              "refined": refine_info.get("regions", 0), "autoFlagged": refine_info.get("autoFlagged", [])},
        review_images=review_images, region_record=record, warnings=warnings,
    )


def by_number_for(number_to_region: dict[int, int], by_number: dict[int, int], region: int) -> int | None:
    """First-round layer of ``region`` (None if it was not labelled)."""
    for n, r in number_to_region.items():
        if r == region:
            return by_number.get(n)
    return None


# --------------------------------------------------------------------------- http


def check_service_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in LOOPBACK:
        return url
    raise PaperCraftError(
        ErrorCode.INVALID_ARGUMENT,
        "segmentation_url must be an https:// URL, or http:// on localhost / 127.0.0.1 for a local service.",
        details={"url": url},
    )


def run_http(source_path: str, layers: list[dict[str, Any]], url: str, *, timeout: float) -> BackendResult:
    """POST the image and layer plan; expect a label-map PNG back (docs/SEGMENTATION_HTTP.md)."""
    check_service_url(url)
    rgba = seg.load_rgba(source_path)
    h, w = rgba.shape[:2]
    with open(source_path, "rb") as fh:
        raw = fh.read()
    with Image.open(io.BytesIO(raw)) as probe:
        mime = Image.MIME.get(probe.format or "", "application/octet-stream")
    payload = {
        "version": "1",
        "image": {"mime": mime, "data": base64.b64encode(raw).decode("ascii"), "width": w, "height": h},
        "layers": [{k: layer[k] for k in ("id", "name", "role", "order", "description", "bbox")} for layer in layers],
    }
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL checked above
            body = response.read(HTTP_MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        raise PaperCraftError(ErrorCode.SEGMENTATION_FAILED, f"Segmentation service returned HTTP {exc.code}.",
                              retryable=exc.code >= 500) from exc
    except TimeoutError as exc:
        raise PaperCraftError(ErrorCode.TIMEOUT, f"Segmentation service timed out after {timeout:.0f}s.") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise PaperCraftError(ErrorCode.MODEL_UNAVAILABLE, f"Segmentation service unreachable: {exc}") from exc
    if len(body) > HTTP_MAX_RESPONSE:
        raise PaperCraftError(ErrorCode.SEGMENTATION_FAILED, "Segmentation service response is too large.")
    try:
        data = json.loads(body)
        png = base64.b64decode(data["labelMap"]["data"], validate=True)
        with Image.open(io.BytesIO(png)) as img:
            if img.format != "PNG":
                raise ValueError("labelMap must be a PNG")
            labels = np.asarray(img.convert("L") if img.mode not in ("L", "P") else img).astype(np.int16)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise PaperCraftError(ErrorCode.SEGMENTATION_FAILED, f"Invalid segmentation service response: {exc}") from exc
    if labels.shape != (h, w):
        labels = np.asarray(Image.fromarray(labels.astype(np.uint8)).resize((w, h), Image.Resampling.NEAREST)).astype(np.int16)
    if labels.max() > len(layers):
        raise PaperCraftError(ErrorCode.SEGMENTATION_FAILED,
                              f"labelMap uses value {int(labels.max())}, but only {len(layers)} layers were sent.")
    owner = labels - 1  # 0 = unassigned in the protocol
    if (owner < 0).any():  # unassigned pixels take the nearest assigned layer
        from scipy import ndimage as ndi

        if (owner >= 0).any():
            _, (iy, ix) = ndi.distance_transform_edt(owner < 0, return_indices=True)
            owner = owner[iy, ix]
        else:
            raise PaperCraftError(ErrorCode.SEGMENTATION_FAILED, "Segmentation service assigned no pixels.")
    owner = np.where(rgba[..., 3] >= 128, owner, -1)
    raw_conf = data.get("confidence", {}) if isinstance(data.get("confidence"), dict) else {}
    confidence = []
    for layer in layers:
        value = raw_conf.get(layer["id"])
        confidence.append(round(float(value), 3) if isinstance(value, (int, float)) and 0 <= value <= 1 else 0.5)
    method = str(data.get("method", "http"))[:80]
    return BackendResult(owner=owner.astype(np.int16), confidence=confidence, method=f"http:{method}",
                         meta={"service": urlparse(url).hostname})
