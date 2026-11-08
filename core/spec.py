"""DESIGN_SPEC.md: rendered from scene.json / analysis.json / layers.json only.

Rules
- Every number in the document is read from those files (never typed in here).
- Sections A–F live between ``<!-- auto:X -->`` markers. Regenerating replaces only
  those blocks; anything the user writes outside them is kept.
- The header records the sha256 + revision of the scene.json it was built from, so a
  stale spec is detectable (``spec_is_current``).
- ``verify_spec`` re-reads the layer, camera and lighting tables from the Markdown and
  compares them with scene.json.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tomllib
from pathlib import Path
from typing import Any

from .version import VERSION

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SECTIONS = ("A", "B", "C", "D", "E", "F", "G")
REQUIRED = ("A", "B", "C", "D", "E", "F")  # G (change log) is added to older specs automatically
FINGERPRINT_RE = re.compile(r"<!-- papercraft-spec: scene-sha256=([0-9a-f]{64}) revision=(\d+) -->")
AUTO_RE = re.compile(r"<!-- auto:([A-Z]) -->.*?<!-- /auto:\1 -->", re.S)

T = {
    "zh-TW": {
        "title": "設計與實作企劃書", "project": "專案", "revision": "場景版本", "mode": "模式",
        "generated": "產生時間", "source": "原始圖片", "preview_warn": "⚠️ 本場景為**示意預覽**：圖片未成功分層，並非 3D 重建。",
        "A": "A. 視覺概念", "B": "B. 3D 場景結構", "C": "C. 光影設計", "D": "D. 材質與渲染",
        "E": "E. 互動功能", "F": "F. 技術實作",
        "subject": "主題", "style": "風格", "keywords": "關鍵字", "palette": "配色", "hex": "HEX", "role": "用途",
        "name": "名稱", "pixel": "像素核對", "found": "✓ 圖中存在", "not_found": "✗ 圖中找不到",
        "measured": "像素量測主色（k-means, CIE Lab）", "hierarchy": "視覺層次（由遠到近）",
        "review": "待人工確認", "graph": "Scene Graph", "layers": "圖層", "order": "順序", "depth": "深度 z",
        "parallax": "視差係數", "geometry": "幾何", "scale": "縮放", "coverage": "覆蓋率", "inpainted": "補全比例",
        "prov": "來源 / 信心", "coords": "座標系", "camera": "攝影機", "limits": "安全邊界",
        "coord_text": "右手座標：+X 向右、+Y 向上、+Z 朝向觀看者；圖片中心在原點。",
        "ppu": "每單位像素", "frame": "畫框尺寸（世界單位）", "type": "類型", "position": "位置", "target": "注視點",
        "zoom": "縮放範圍", "tilt": "最大傾角", "nearfar": "near / far",
        "margin": "分層補全邊距", "tilt_shift": "最大傾角時的圖層錯位", "para_shift": "最大視差時的圖層錯位",
        "budget": "預算", "limits_text": "傾斜與視差造成的圖層相對錯位，合計不超過補全邊距，因此正常觀看不會露出未補全的區域；圖層展開為檢查模式，不受此限制。",
        "active": "目前光源", "preset": "預設", "ambient": "環境光", "key": "主光", "rim": "背光", "color": "顏色",
        "intensity": "強度", "kelvin": "色溫", "az_el": "方位角 / 仰角", "shadow": "陰影", "background": "背景",
        "glow_scale": "發光倍率", "analysed": "分析推估", "confidence": "信心", "emitters": "發光物件",
        "layer": "圖層", "glow_map": "發光貼圖", "none": "無",
        "material": "材質", "rough": "粗糙度", "metal": "金屬度", "alpha": "alphaTest", "side": "雙面",
        "grain": "紙紋強度", "render": "渲染設定", "transparency": "透明排序",
        "transparency_text": "所有圖層以 alphaTest 做裁切透明（非半透明混合），不需要依距離排序，避免透明排序錯誤；陰影同樣依裁切輪廓投射。",
        "performance": "效能", "textures": "貼圖數", "tex_mem": "貼圖記憶體估計（含 mipmap）",
        "interaction": "互動", "orbit": "拖曳旋轉", "zoom_i": "縮放", "explode": "圖層展開間距",
        "presets": "光源預設", "responsive": "響應式",
        "responsive_text": "寬度 ≤ 820px 時改為單欄：畫面在上、控制面板在下；支援觸控拖曳與雙指縮放。",
        "on": "開", "off": "關", "packages": "套件與版本", "structure": "專案檔案結構", "loading": "資產載入",
        "loading_text": "scene.json 以相對路徑引用資產；檢視器先以 JSON Schema 驗證 scene.json，再用 drei useTexture 載入 PNG。",
        "commands": "開發與預覽指令", "deploy": "部署", "deploy_text": "用 papercraft_export_project 匯出獨立網頁專案（含 package-lock.json、場景與全部資產、README），在任何電腦執行 `npm ci && npm run build`，再把 `dist/` 上傳到任一靜態網站主機（GitHub Pages、Netlify、Cloudflare Pages）。`base: \"./\"` 讓它可放在任何子路徑。",
        "known": "已知限制", "next": "後續方向",
        "limits_general": ["透明圖層構成的 2.5D 場景只有正面；不是具備背面拓樸的完整 3D 模型。",
                           "原圖中畫好的陰影是固定的，前景移動時不會跟著移動。",
                           "被遮住的區域以補全產生，並非原圖實際內容。"],
        "next_items": ["shape / extrude 幾何：依遮罩輪廓建立具厚度的紙片",
                       "陰影去除：把原圖畫上的投影從底層移除，改由即時光源產生"],
        "G": "G. 變更紀錄", "rev": "版本", "time": "時間", "request": "要求", "changes": "變更", "adjust": "自動調整", "no_changes": "尚無修改（第 0 版由 papercraft_build_scene 建立）。", "sep": "；", "mparallax": "滑鼠視差", "tool_warnings": "工具回報的警告（原文）", "notes": "備註", "notes_hint": "此段以下的內容在重新產生時會保留。",
    },
    "en": {
        "title": "Design & Implementation Spec", "project": "Project", "revision": "Scene revision", "mode": "Mode",
        "generated": "Generated", "source": "Source image", "preview_warn": "⚠️ This scene is a **preview**: the image was not segmented into layers; it is not a 3D reconstruction.",
        "A": "A. Visual concept", "B": "B. 3D scene structure", "C": "C. Lighting", "D": "D. Materials & rendering",
        "E": "E. Interaction", "F": "F. Technical implementation",
        "subject": "Subject", "style": "Style", "keywords": "Keywords", "palette": "Palette", "hex": "HEX", "role": "Role",
        "name": "Name", "pixel": "Pixel check", "found": "✓ present", "not_found": "✗ not found",
        "measured": "Measured dominant colours (k-means, CIE Lab)", "hierarchy": "Visual hierarchy (far to near)",
        "review": "Needs review", "graph": "Scene graph", "layers": "Layers", "order": "Order", "depth": "Depth z",
        "parallax": "Parallax", "geometry": "Geometry", "scale": "Scale", "coverage": "Coverage", "inpainted": "Inpainted",
        "prov": "Source / confidence", "coords": "Coordinates", "camera": "Camera", "limits": "Safety bounds",
        "coord_text": "Right-handed: +X right, +Y up, +Z toward the viewer; image centred on the origin.",
        "ppu": "Pixels per unit", "frame": "Frame size (world units)", "type": "Type", "position": "Position",
        "target": "Target", "zoom": "Zoom range", "tilt": "Max tilt", "nearfar": "near / far",
        "margin": "Segmentation fill margin", "tilt_shift": "Layer shift at max tilt", "para_shift": "Layer shift at full parallax",
        "budget": "Budget", "limits_text": "Tilt and parallax together shift layers by no more than the fill margin, so normal viewing never reveals unfilled areas; the exploded view is an inspection mode and exempt.",
        "active": "Active lighting", "preset": "Preset", "ambient": "Ambient", "key": "Key", "rim": "Rim", "color": "Colour",
        "intensity": "Intensity", "kelvin": "Colour temperature", "az_el": "Azimuth / elevation", "shadow": "Shadow",
        "background": "Background", "glow_scale": "Glow scale", "analysed": "Analysed estimate", "confidence": "Confidence",
        "emitters": "Emitters", "layer": "Layer", "glow_map": "Glow map", "none": "none",
        "material": "Material", "rough": "Roughness", "metal": "Metalness", "alpha": "alphaTest", "side": "Double-sided",
        "grain": "Paper grain", "render": "Rendering", "transparency": "Transparency",
        "transparency_text": "All layers use alphaTest cut-out transparency (no blending), so no depth sorting is needed and sorting artefacts cannot occur; shadows follow the cut-out outlines.",
        "performance": "Performance", "textures": "Textures", "tex_mem": "Estimated texture memory (with mipmaps)",
        "interaction": "Interaction", "orbit": "Drag to rotate", "zoom_i": "Zoom", "explode": "Exploded-view spacing",
        "presets": "Lighting presets", "responsive": "Responsive",
        "responsive_text": "At widths ≤ 820px the layout becomes one column (view on top, controls below); touch drag and pinch zoom are supported.",
        "on": "on", "off": "off", "packages": "Packages & versions", "structure": "Project files", "loading": "Asset loading",
        "loading_text": "scene.json references assets by relative path; the viewer validates scene.json against the JSON Schema, then loads the PNGs with drei useTexture.",
        "commands": "Development & preview", "deploy": "Deployment", "deploy_text": "papercraft_export_project writes a standalone web project (package-lock.json, scene and all assets, README). On any machine run `npm ci && npm run build`, then upload `dist/` to any static host (GitHub Pages, Netlify, Cloudflare Pages). `base: \"./\"` lets it live in any sub-folder.",
        "known": "Known limitations", "next": "Next steps",
        "limits_general": ["A scene of transparent layers is 2.5D: front faces only, not a 3D model with back-face topology.",
                           "Shadows painted into the source art are fixed and do not move with the layers.",
                           "Areas hidden behind nearer layers are filled in, not original image content."],
        "next_items": ["shape / extrude geometry: paper pieces with thickness from the mask outlines",
                       "Shadow removal: lift painted shadows off back layers so real-time lights cast them"],
        "G": "G. Change log", "rev": "Revision", "time": "Time", "request": "Request", "changes": "Changes", "adjust": "Adjustments", "no_changes": "No edits yet (revision 0 was built by papercraft_build_scene).", "sep": "; ", "mparallax": "Mouse parallax", "tool_warnings": "Warnings reported by the tools", "notes": "Notes", "notes_hint": "Content below this line is kept when the spec is regenerated.",
    },
}


def fmt(value: Any, digits: int = 4) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
        return text if text not in ("-0", "") else "0"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(fmt(v, digits) for v in value) + "]"
    return str(value)


def scene_sha256(scene_path: Path) -> str:
    return hashlib.sha256(scene_path.read_bytes()).hexdigest()


def _row(*cells: Any) -> str:
    return "| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |"


def _table(header: list[str], rows: list[list[Any]]) -> str:
    return "\n".join([_row(*header), _row(*["---"] * len(header)), *(_row(*r) for r in rows)])


def _az_el(position: list[float]) -> tuple[float, float]:
    x, y, z = position
    r = math.sqrt(x * x + y * y + z * z) or 1.0
    return math.degrees(math.atan2(x, z)), math.degrees(math.asin(y / r))


# --------------------------------------------------------------------------- sections


def section_a(t: dict, scene: dict, analysis: dict) -> str:
    check = analysis.get("meta", {}).get("paletteCheck", {})
    missing = {h.lower() for h in check.get("not_found", [])}
    pal_rows = [[c["hex"].lower(), c["role"], c.get("name", ""),
                 (t["not_found"] if c["hex"].lower() in missing else t["found"]) if check else "—"]
                for c in analysis["palette"]]
    measured = ", ".join(f"`{c['hex']}` {c['share'] * 100:.1f}%" for c in analysis.get("measured", {}).get("palette", []))
    by_id = {l["id"]: l for l in analysis["layers"]}
    hierarchy = "\n".join(f"{i + 1}. **{l['name']}** (`{l['id']}`, {l['role']}) — {by_id.get(l['id'], {}).get('description', '')}"
                          for i, l in enumerate(scene["layers"]))
    review = "\n".join(f"- `{r['field']}`: {r['reason']}" for r in analysis.get("needsReview", [])) or f"- {t['none']}"
    return "\n\n".join([
        f"## {t['A']}",
        f"**{t['subject']}：** {analysis['subject']}" if t is T["zh-TW"] else f"**{t['subject']}:** {analysis['subject']}",
        f"**{t['style']}：** {analysis['style']['description']}" if t is T["zh-TW"] else f"**{t['style']}:** {analysis['style']['description']}",
        f"**{t['keywords']}：** " + "、".join(analysis["style"].get("keywords", [])) if t is T["zh-TW"]
        else f"**{t['keywords']}:** " + ", ".join(analysis["style"].get("keywords", [])),
        f"### {t['palette']}",
        _table([t["hex"], t["role"], t["name"], t["pixel"]], pal_rows),
        f"{t['measured']}: {measured}" if measured else "",
        f"### {t['hierarchy']}", hierarchy,
        f"### {t['review']}", review,
    ]).replace("\n\n\n", "\n\n")


def section_b(t: dict, scene: dict, manifest: dict) -> str:
    seg = {l["id"]: l for l in manifest["layers"]}
    graph = ["Scene", f"├── {t['camera']} ({scene['camera']['type']})", f"├── {t['ambient']}, {t['key']}"
             + (f", {t['rim']}" if scene["lighting"].get("rim") else ""), f"└── {t['layers']}"]
    for i, l in enumerate(scene["layers"]):
        graph.append(f"    {'└' if i == len(scene['layers']) - 1 else '├'}── {l['id']}  (z={fmt(l['depth'])})")
    rows = []
    for l in scene["layers"]:
        s = seg.get(l["id"], {})
        rows.append([l["order"], f"`{l['id']}`", l["name"], l["role"], fmt(l["depth"]), fmt(l["parallaxFactor"]),
                     l["geometry"]["type"], fmt(l.get("scale", 1)),
                     f"{s.get('coverage', 0) * 100:.1f}%", f"{s.get('provenance', {}).get('inpaintedFraction', 0) * 100:.1f}%",
                     f"{l['provenance']['source']} / {fmt(l['provenance'].get('confidence', 0), 2)}"])
    cam = scene["camera"]
    lim = scene.get("limits", {})
    cam_rows = [[t["type"], cam["type"]], [t["position"], fmt(cam["position"])], [t["target"], fmt(cam["target"])],
                [t["zoom"], f"{fmt(cam.get('minZoom', 0.5))} – {fmt(cam.get('maxZoom', 3))}"],
                [t["tilt"], f"{math.degrees(cam.get('maxPolarAngle', 0)):.2f}° ({fmt(cam.get('maxPolarAngle', 0))} rad)"],
                [t["nearfar"], f"{fmt(cam.get('near', 0.1))} / {fmt(cam.get('far', 100))}"]]
    if "fov" in cam:
        cam_rows.append(["fov", f"{fmt(cam['fov'], 2)}°"])
    parts = [
        f"## {t['B']}",
        f"### {t['graph']}", "```\n" + "\n".join(graph) + "\n```",
        f"### {t['layers']}",
        _table([t["order"], "id", t["name"], t["role"], t["depth"], t["parallax"], t["geometry"], t["scale"],
                t["coverage"], t["inpainted"], t["prov"]], rows),
        f"### {t['coords']}",
        t["coord_text"] + f" {t['ppu']}: **{fmt(scene['units']['pixelsPerUnit'])}**；{t['frame']}: "
        f"**{fmt(scene['frame']['width'])} × {fmt(scene['frame']['height'])}**",
        f"### {t['camera']}", _table(["", ""], cam_rows),
    ]
    if lim:
        parts += [f"### {t['limits']}", t["limits_text"], _table(["", "px", t["budget"]], [
            [t["margin"], fmt(lim["fillMarginPx"]), "100%"],
            [t["tilt_shift"], fmt(lim["tiltShiftPx"], 2), f"{lim.get('tiltBudget', 0) * 100:.0f}%"],
            [t["para_shift"], fmt(lim["parallaxShiftPx"], 2), f"{lim.get('parallaxBudget', 0) * 100:.0f}%"]])]
    return "\n\n".join(parts)


def _lighting_rows(t: dict, L: dict) -> list[list[Any]]:
    key = L["key"]
    az, el = _az_el(key["position"])
    rows = [[t["ambient"], f"{L['ambient'].get('type', 'ambient')} `{L['ambient']['color']}`"
             + (f" / `{L['ambient']['groundColor']}`" if L["ambient"].get("groundColor") else ""), fmt(L["ambient"]["intensity"])],
            [t["key"], f"`{key['color']}`" + (f" ({fmt(key['colorTemperatureK'])} K)" if "colorTemperatureK" in key else ""),
             fmt(key["intensity"])],
            [f"{t['key']} {t['position']}", fmt(key["position"]), f"{t['az_el']}: {az:.1f}° / {el:.1f}°"],
            [t["shadow"], f"{'on' if key.get('castShadow', True) else 'off'}, mapSize {fmt(key.get('shadow', {}).get('mapSize', 2048))}, "
             f"radius {fmt(key.get('shadow', {}).get('radius', 4))}, bias {fmt(key.get('shadow', {}).get('bias', -0.0005))}", ""]]
    if L.get("rim"):
        rows.append([t["rim"], f"`{L['rim']['color']}` @ {fmt(L['rim']['position'])}", fmt(L["rim"]["intensity"])])
    rows.append([t["background"], f"`{L.get('background', '#1b1b1f')}`", ""])
    rows.append([t["glow_scale"], "", fmt(L.get("emissiveScale", 1))])
    return rows


def section_c(t: dict, scene: dict, analysis: dict) -> str:
    parts = [f"## {t['C']}", f"{t['active']}: **{scene['lighting'].get('preset', 'custom')}**",
             _table(["", t["color"], t["intensity"]], _lighting_rows(t, scene["lighting"]))]
    for name, L in (scene.get("lightingPresets") or {}).items():
        parts += [f"### {t['preset']}: {name}", _table(["", t["color"], t["intensity"]], _lighting_rows(t, L))]
    light = analysis["light"]
    parts += [f"### {t['analysed']}",
              f"{t['az_el']}: {fmt(light['azimuthDeg'])}° / {fmt(light['elevationDeg'])}°{t['sep']}{t['kelvin']}: "
              f"{fmt(light['colorTemperatureK'])} K{t['sep']}softness {fmt(light['softness'])}{t['sep']}{t['confidence']} {fmt(light['confidence'])}"
              + (f"\n\n> {light['shadowNotes']}" if light.get("shadowNotes") else "")]
    glow_layers = {l["id"]: l["material"].get("emissiveMap") for l in scene["layers"] if l.get("material", {}).get("emissiveMap")}
    emitters = light.get("emitters", [])
    if emitters:
        parts += [f"### {t['emitters']}", _table([t["name"], "kind", t["layer"], t["color"], t["glow_map"]], [
            [e["name"], e["kind"], e.get("layerId", "—"), f"`{e['color'].lower()}`",
             f"`{glow_layers[e['layerId']]}`" if e.get("layerId") in glow_layers else t["none"]] for e in emitters])]
    return "\n\n".join(parts)


def section_d(t: dict, scene: dict) -> str:
    rows = []
    for l in scene["layers"]:
        m = l.get("material", {})
        rows.append([f"`{l['id']}`", fmt(m.get("roughness", 0.95)), fmt(m.get("metalness", 0)), fmt(m.get("alphaTest", 0.5)),
                     "✓" if m.get("side", "double") == "double" else "—", fmt(m.get("paperTexture", {}).get("strength", 0.08)),
                     f"`{m['emissiveMap']}` × {fmt(m.get('emissiveIntensity', 0))}" if m.get("emissiveMap") else t["none"]])
    r = scene["rendering"]
    w, h = scene["source"]["width"], scene["source"]["height"]
    n_tex = len(scene["layers"]) + sum(1 for l in scene["layers"] if l.get("material", {}).get("emissiveMap"))
    mem = n_tex * w * h * 4 * 4 / 3 / 1024 / 1024
    render_rows = [["toneMapping", r.get("toneMapping", "neutral")], ["exposure", fmt(r.get("exposure", 1))],
                   ["outputColorSpace", r.get("outputColorSpace", "srgb")], ["antialias", fmt(r.get("antialias", True))],
                   ["shadows", fmt(r.get("shadows", True))], ["maxPixelRatio", fmt(r.get("maxPixelRatio", 2))],
                   ["clipToFrame", fmt(r.get("clipToFrame", True))]]
    return "\n\n".join([
        f"## {t['D']}", f"### {t['material']} (MeshStandardMaterial)",
        _table([t["layer"], t["rough"], t["metal"], t["alpha"], t["side"], t["grain"], t["glow_map"]], rows),
        f"### {t['render']}", _table(["", ""], render_rows),
        f"### {t['transparency']}", t["transparency_text"],
        f"### {t['performance']}",
        f"- {t['textures']}: {n_tex} × {w}×{h}\n- {t['tex_mem']}: ≈ {mem:.0f} MB\n"
        f"- devicePixelRatio ≤ {fmt(r.get('maxPixelRatio', 2))}{t['sep']}shadow map {fmt(scene['lighting']['key'].get('shadow', {}).get('mapSize', 2048))}²",
    ])


def section_e(t: dict, scene: dict) -> str:
    i, cam = scene["interaction"], scene["camera"]
    onoff = lambda v: t["on"] if v else t["off"]  # noqa: E731
    rows = [[t["orbit"], f"{onoff(i.get('orbit', True))}, ±{math.degrees(cam.get('maxPolarAngle', 0)):.2f}°"],
            [t["zoom_i"], f"{onoff(i.get('zoom', True))}, {fmt(cam.get('minZoom', 0.5))} – {fmt(cam.get('maxZoom', 3))}"],
            [t["mparallax"], f"{onoff(i.get('parallax', {}).get('enabled', True))}, strength {fmt(i.get('parallax', {}).get('strength', 0.15))}"],
            [t["explode"], f"{onoff(i.get('explodedView', {}).get('enabled', True))}, {fmt(i.get('explodedView', {}).get('spacing', 1.5))}"],
            [t["presets"], ", ".join(scene.get("lightingPresets", {}).keys()) or t["none"]],
            [t["responsive"], t["responsive_text"]]]
    return "\n\n".join([f"## {t['E']}", _table([t["interaction"], ""], rows)])


def _versions() -> tuple[dict[str, str], list[str]]:
    try:
        pkg = json.loads((PLUGIN_ROOT / "viewer" / "package.json").read_text(encoding="utf-8"))
        node = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    except (OSError, ValueError):
        node = {}
    try:
        py = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    except (OSError, KeyError, ValueError):
        py = []
    return node, py


def section_f(t: dict, scene: dict, project_dir: Path, warnings: list[str]) -> str:
    node, py = _versions()
    files = sorted(str(p.relative_to(project_dir)).replace("\\", "/") for p in project_dir.rglob("*")
                   if p.is_file() and not p.name.startswith(".") and p.parts[len(project_dir.parts)] not in ("history", "logs"))
    tree = "\n".join(files)
    known = "\n".join(f"- {w}" for w in t["limits_general"])
    if warnings:
        known += f"\n\n{t['tool_warnings']}:\n\n" + "\n".join(f"- {w}" for w in warnings)
    return "\n\n".join([
        f"## {t['F']}",
        f"### {t['packages']}",
        _table(["package", "version", ""], [[k, v, "npm"] for k, v in sorted(node.items())]
               + [[re.split(r"[<>=!~\[; ]", p, maxsplit=1)[0], p[len(re.split(r"[<>=!~\[; ]", p, maxsplit=1)[0]):], "python"]
                  for p in py]),
        f"### {t['structure']}", "```\n" + tree + "\n```",
        f"### {t['loading']}", t["loading_text"],
        f"### {t['commands']}",
        "```bash\ncd <plugin>/viewer\nnpm install\nPAPERCRAFT_PROJECT=\"<project_dir>\" npm run dev\n"
        "# http://localhost:5173/?scene=project/scene.json\n```",
        f"### {t['deploy']}", t["deploy_text"],
        f"### {t['known']}", known,
        f"### {t['next']}", "\n".join(f"- {n}" for n in t["next_items"]),
    ])


def section_g(t: dict, project_dir: Path) -> str:
    log = project_dir / "history" / "patches.jsonl"
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] if log.is_file() else []
    if not entries:
        return f"## {t['G']}\n\n{t['no_changes']}"
    rows = [[e["revision"], e["at"], e.get("instruction", ""),
             "<br>".join(f"`{d['path']}`" for d in e.get("diff", []))[:600] or e.get("kind", ""),
             "<br>".join(e.get("adjustments", [])) or "—"] for e in entries]
    return f"## {t['G']}\n\n" + _table([t["rev"], t["time"], t["request"], t["changes"], t["adjust"]], rows)


# --------------------------------------------------------------------------- document


def render(scene: dict, analysis: dict, manifest: dict, project_meta: dict, project_dir: Path,
           *, language: str, sha: str, generated_at: str) -> dict[str, str]:
    t = T[language]
    warnings = list(dict.fromkeys(scene.get("warnings", [])))
    head = "\n".join([
        f"# {scene['name']} — {t['title']}",
        "",
        f"<!-- papercraft-spec: scene-sha256={sha} revision={scene['generator'].get('revision', 0)} -->",
        f"- {t['project']}: `{scene['sceneId']}` · {t['revision']} {scene['generator'].get('revision', 0)} · "
        f"schema {scene['schemaVersion']} · papercraft-3d {VERSION}",
        f"- {t['mode']}: **{scene['mode']}** · {t['generated']}: {generated_at}",
        f"- {t['source']}: `{scene['source']['image']}` ({scene['source']['width']}×{scene['source']['height']})",
    ] + ([f"\n> {t['preview_warn']}"] if scene["mode"] == "preview" else []))
    return {
        "head": head,
        "A": section_a(t, scene, analysis),
        "B": section_b(t, scene, manifest),
        "C": section_c(t, scene, analysis),
        "D": section_d(t, scene),
        "E": section_e(t, scene),
        "F": section_f(t, scene, project_dir, warnings),
        "G": section_g(t, project_dir),
        "notes": f"## {t['notes']}\n\n<!-- {t['notes_hint']} -->\n",
    }


def assemble(parts: dict[str, str], existing: str | None) -> tuple[str, bool]:
    """Full document. With an existing spec, replace only the header fingerprint and auto blocks."""
    blocks = {k: f"<!-- auto:{k} -->\n{parts[k]}\n<!-- /auto:{k} -->" for k in SECTIONS}
    if existing and all(f"<!-- auto:{k} -->" in existing and f"<!-- /auto:{k} -->" in existing for k in REQUIRED):
        out = AUTO_RE.sub(lambda m: blocks[m.group(1)] if m.group(1) in blocks else m.group(0), existing)
        for k in SECTIONS:  # sections added in later versions go after the last existing one
            if f"<!-- auto:{k} -->" not in out:
                last = max(out.rindex(f"<!-- /auto:{j} -->") + len(f"<!-- /auto:{j} -->")
                           for j in SECTIONS if f"<!-- /auto:{j} -->" in out)
                out = out[:last] + "\n\n" + blocks[k] + out[last:]
        head_end = out.index("<!-- auto:A -->")
        return parts["head"] + "\n\n" + out[head_end:], True
    body = "\n\n".join(blocks[k] for k in SECTIONS)
    return f"{parts['head']}\n\n{body}\n\n{parts['notes']}", False


def spec_is_current(markdown: str, scene_path: Path) -> bool:
    m = FINGERPRINT_RE.search(markdown)
    return bool(m) and m.group(1) == scene_sha256(scene_path)


def verify_spec(markdown: str, scene: dict) -> list[str]:
    """Re-read the layer table, camera tilt and key light from the Markdown; compare with scene.json."""
    problems = []
    for l in scene["layers"]:
        row = re.search(rf"^\| {l['order']} \| `{re.escape(l['id'])}` \|(.*)$", markdown, re.M)
        if not row:
            problems.append(f"layer {l['id']} missing from the layer table")
            continue
        cells = [c.strip() for c in row.group(1).split("|")]
        if cells[2] != fmt(l["depth"]):
            problems.append(f"layer {l['id']}: depth {cells[2]} != {fmt(l['depth'])}")
        if cells[3] != fmt(l["parallaxFactor"]):
            problems.append(f"layer {l['id']}: parallax {cells[3]} != {fmt(l['parallaxFactor'])}")
    tilt = f"({fmt(scene['camera'].get('maxPolarAngle', 0))} rad)"
    if tilt not in markdown:
        problems.append(f"camera tilt {tilt} not found")
    for name, L in [("active", scene["lighting"]), *(scene.get("lightingPresets") or {}).items()]:
        if f"`{L['key']['color']}`" not in markdown or fmt(L["key"]["intensity"]) not in markdown:
            problems.append(f"preset {name}: key light not documented as in scene.json")
    m = FINGERPRINT_RE.search(markdown)
    if not m or int(m.group(2)) != scene["generator"].get("revision", 0):
        problems.append("fingerprint revision does not match scene.json")
    return problems
