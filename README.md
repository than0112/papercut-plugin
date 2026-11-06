# PaperCraft 3D for Hermes

> One image. One Hermes plugin. An interactive 3D web experience.

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that turns an
AI-generated **paper-cut / papercraft image** into a layered, interactive
**Three.js 2.5D scene**. It also writes a design spec (`DESIGN_SPEC.md`) that is
generated from the scene's own `scene.json`, so the two cannot disagree. You can
then change the scene in plain language ("make it night with cold moonlight").

> **Status: v0.3.1 (phase 3 of 7).** `papercraft_analyze` and
> `papercraft_segment` work end to end. The later stages (scene building
> onward) still return an explicit `not_implemented` error and never fake a
> success. See [Roadmap](#roadmap).

## How it works

```
image ──► papercraft_analyze ──► analysis.json   (vision model via Hermes ctx.llm)
              │
              ▼
        papercraft_segment ────► layers/*.png + masks + layers.json
              │
              ▼
        papercraft_build_scene ► scene.json  ──►  Three.js viewer
              │                     ▲
              ▼                     │ validated Scene Patch
        papercraft_generate_plan    │
              │               papercraft_update_scene  ◄── "make it night…"
              ▼
        DESIGN_SPEC.md ──► papercraft_export_project ──► standalone Vite project
```

`schemas/scene.schema.json` is the single source of truth. The Python plugin
validates against it, and so does the viewer at load time (it uses the same
file through Ajv). The design spec is rendered from it.

## Tools

| Tool | Purpose | Status |
|---|---|---|
| `papercraft_analyze` | Validate the image, create a project, and produce structured `analysis.json` (layers, occlusion, palette, light, camera, confidence, review items) | ✅ phase 2 |
| `papercraft_segment` | Transparent full-size layer PNGs + masks + `layers.json`. Fills hidden areas behind nearer layers. Supports manual mask corrections and review images. Fails rather than fakes, with an opt-in labelled *preview* mode | ✅ phase 3 |
| `papercraft_build_scene` | Compose layers into a schema-valid `scene.json` | phase 4 |
| `papercraft_generate_plan` | `DESIGN_SPEC.md` rendered from `scene.json` | phase 5 |
| `papercraft_update_scene` | Apply a validated JSON-Patch-style Scene Patch (the agent translates the user's words into it). Keeps history and updates the spec | phase 6 |
| `papercraft_export_project` | Standalone, relative-path Vite + Three.js project | phase 7 |

Every tool returns a JSON envelope: `{"ok": true, "data": …, "warnings": […]}` or
`{"ok": false, "error": {"code", "message", "retryable", "details"}}`. Error
codes include `image_format`, `image_too_large`, `model_unavailable`,
`model_output_invalid`, `segmentation_failed`, `scene_invalid`,
`asset_missing`, `viewer_build_failed`, `export_failed`, `path_forbidden`,
`precondition_failed` and `not_implemented`. See `core/errors.py`.

## Install (development)

Requirements: Hermes Agent `>=0.22`, Python ≥ 3.11, and Node ≥ 20.19 (viewer only).

```bash
git clone <this-repo-url> ~/.hermes/plugins/papercraft-3d
```

```bash
hermes plugins enable papercraft-3d
```

```bash
hermes plugins doctor ~/.hermes/plugins/papercraft-3d --ci
```

Python dependencies (`Pillow`, `numpy`, `jsonschema`, `scipy`, `scikit-image`)
are declared in `pyproject.toml` with bounded ranges compatible with Hermes's
core pins. Hermes asks for consent before it
installs them. The full test procedure is in
[`docs/HERMES_TEST_PLAN.md`](docs/HERMES_TEST_PLAN.md).

### Plugin settings

Settings go under `plugins.entries.papercraft-3d.settings` (the Desktop app also shows them under **Settings → Plugins**):

| Key | Default | Meaning |
|---|---|---|
| `workspace_dir` | plugin data dir | Where projects are written. Generated files never go into the plugin's install tree |
| `segmentation_backend` | `builtin` | `builtin` (regions computed locally, labelled by your Hermes vision model) or `http` (your own service, see [docs/SEGMENTATION_HTTP.md](docs/SEGMENTATION_HTTP.md)) |
| `segmentation_url` | — | `https://`, or `http://` on localhost, for the `http` backend |
| `segmentation_timeout_s` | `300` | Timeout for the `http` backend |
| `max_image_mb` | `10` | Upload limit (hard-capped at 10 MB in the MVP) |
| `analysis_timeout_s` | `180` | Timeout per vision-model call (10–600 s) |

## Viewer

```bash
cd viewer
```

```bash
npm install
```

```bash
npm run dev
```

The viewer opens `?scene=<url of scene.json>`. With no parameter it loads the
bundled **synthetic fixture** (`viewer/public/scenes/fixture`). The fixture is
drawn procedurally by `examples/make_synthetic_fixture.py`, has known
ground-truth layers, and is labelled as such. It is not pipeline output.

Features: a left scene tree (visibility, selection), a central orthographic
view with limited orbit and zoom, mouse parallax, an exploded view, a light
intensity control, a shadows toggle, day/night presets, camera reset, and a
responsive phone layout. The viewer's controls only change the preview.
Lasting changes go through `papercraft_update_scene`.

## Tests

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install "Pillow>=10,<14" "numpy>=1.26,<3" "jsonschema>=4.18,<5" "pytest>=8,<10" "PyYAML>=6,<7"
```

```bash
.venv/bin/python -m pytest tests
```

The tests cover the following:

- Manifest and registration parity, mirroring `hermes plugins validate`'s "declared tools" check.
- The handler contract: handlers never raise and always return JSON strings.
- Path confinement, including symlink escapes.
- Image validation: format spoofing, truncation, and size limits.
- The scene schema, including the path-traversal and URL asset rules.

## How analysis works

1. The image is validated, copied read-only into a new project, and a copy
   downscaled to 1536 px on its long side is sent to **your active Hermes
   model** through `ctx.llm`. If that model is text-only, Hermes routes the
   call to your configured vision model.
2. The reply must pass `schemas/analysis.schema.json` and semantic checks:
   unique ids and orders, `x0 < x1`, at most `max_layers` layers, and
   occlusion consistent with order. If it fails, the model gets **one** repair
   attempt that lists the exact errors. If that also fails, the tool returns
   `model_output_invalid` and writes no `analysis.json`.
3. Before the call, the plugin clusters the real pixels (k-means in CIE Lab,
   deterministic) into up to 12 colours. It stores them in `measured.palette`
   and gives them to the model as candidates. Afterwards, every palette colour
   the model returns is checked against every colour covering at least 0.2% of
   the pixels. The result is returned as `palette_check`, and colours that are
   not found become `needsReview` items.
4. `light.emitters` lists elements that should glow (a drawn sun, lanterns,
   lit windows), each with its layer, bbox and colour, for the scene builder.
5. Every raw model reply is kept in `logs/analysis_reply_N.txt`, and each
   attempt is logged in `logs/process.jsonl`.

## How segmentation works

1. **Regions on your machine.** Felzenszwalb graph segmentation runs on a
   1024 px working copy and follows the paper cut lines. A Ward-style
   agglomerative merge then joins similar neighbours, cheapest first,
   recomputing region means after every merge. This leaves 40–120 significant
   regions, depending on the number of layers. A transitive threshold merge
   was dropped: on a real image it chained pink sky, blossoms, snow and rock
   into one region covering 50% of the picture.
2. **Labels from your vision model (Set-of-Mark).** The model gets the original
   image and a copy with each region outlined and numbered, plus the layer plan
   from `analysis.json`. It answers "region 12 → `red_bridge`". The answer is
   validated, with one repair round. Small regions inherit the layer of the
   neighbour they share the longest border with.
   **Second round:** regions the model marks as mixing layers (for example sky
   showing between blossoms) are split into smaller parts along the original
   fine segments. The model gets one more image with only those parts bright
   and numbered (`review/refine_marks.png`).
3. **Full-resolution edges.** The map is upsampled and the pixels on layer
   boundaries are reassigned by local colour similarity. Isolated specks are
   removed.
4. **Hidden-area fill.** The farthest layer is completed everywhere. Every
   other layer except the nearest is extended 5% of the image's long side
   behind nearer layers, and the extension is filled by push-pull interpolation
   from the layer's own pixels, skipping the cast-shadow band. Owned pixels are
   never changed. `fill.marginPx` tells the scene builder how far layers may
   shift relative to each other before an unfilled gap could show.
5. **Review and correct.** `review/overlay.png` (colour-coded layers) and
   `review/marks.png` (what the model saw) are written. To fix a layer, paint a
   mask and call `papercraft_segment` again with `manual_masks`. The previous
   ownership is reused without calling the model again, and the layer is
   recorded as `manual`.

Measured with perfect labels on the synthetic fixture at 480–2400 px wide, the
pipeline reaches 98.6–98.9% pixel accuracy, with every layer's IoU at or above
0.90. Restacking the layers reproduces the source image pixel for pixel, and a
test enforces this. This measures the regions, edges and fill, not the model. On real images
the result depends on how well your vision model labels the regions, so always
check `review/overlay.png`.

## Security & privacy

- **No API keys in the plugin.** Image analysis runs through Hermes's
  `ctx.llm`, which uses the model and credentials you already configured in
  Hermes. The image goes only to that provider.
- **No undeclared network calls.** Segmentation runs locally by default.
  The `http` backend contacts only the HTTPS URL you set.
- **No shell commands.** The plugin never runs a subprocess, and never builds
  a command from file names or model output. Export only copies files; you run
  `npm` yourself.
- **Confined output.** Every write is resolved inside the workspace
  (`core/security.resolve_within`). The original upload is stored read-only and
  never modified.
- **Untrusted model output.** Model output is validated against JSON Schema
  before use. Asset paths in `scene.json` must be relative, with no `..` and
  no URLs.

## Limitations

A scene built from transparent layers is **2.5D**: stacked cut-out planes, not
a 3D mesh with back-face topology. GLB export (planned, optional) will contain
the same planes.

## Roadmap

1. ✅ Plugin skeleton: manifest, registration, schemas, security, tests, viewer scaffold
2. ✅ Image analysis (`ctx.llm` vision → `analysis.json`)
3. ✅ Layer conversion (masks, transparent layers, hidden-area fill, manual corrections)
4. ⬜ Three.js scene builder (`scene.json`) + `shape`/`extrude` geometry
5. ⬜ Design spec generator
6. ⬜ Natural-language Scene Patch (`papercraft_update_scene`)
7. ⬜ Export, test report, demo, catalog PR

## 中文摘要

PaperCraft 3D 是 Hermes Agent 插件：把 AI 紙雕圖轉成可互動的 Three.js 2.5D 場景，並產生與 `scene.json` 完全一致的 `DESIGN_SPEC.md`，可用自然語言修改場景。目前為第一階段骨架；尚未實作的步驟會明確回傳 `not_implemented`，不會假裝成功。

## License

MIT
