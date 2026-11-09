# PaperCraft 3D for Hermes

> One image. One Hermes plugin. An interactive 3D web experience.

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that turns an
AI-generated **paper-cut / papercraft image** into a layered, interactive
**Three.js 2.5D scene**. It also writes a design spec (`DESIGN_SPEC.md`) that is
generated from the scene's own `scene.json`, so the two cannot disagree. You can
then change the scene in plain language ("make it night with cold moonlight").

> **Status: v0.9.2.** All six tools work end to end, in Hermes and as a Claude Code plugin.
> Submission material: [docs/SUBMISSION.md](docs/SUBMISSION.md),
> [docs/TEST_REPORT.md](docs/TEST_REPORT.md), [docs/catalog/papercraft-3d.yaml](docs/catalog/papercraft-3d.yaml).

## Quick start

The same repository installs in two hosts. Pick one.

### Requirements

| What | Version | Needed for |
|---|---|---|
| Python | 3.11 or newer | Both hosts |
| Node.js | 20.19 or newer (includes npm) | Only to preview a scene or build the exported site |
| Hermes Agent | 0.22 or newer | Option A |
| Claude Code | a current release | Option B |

Check them with `python --version` (`python3 --version` on macOS and Linux) and `node --version`.

### Option A: Hermes Agent

1. Install and enable the plugin. Hermes clones it into its plugins folder
   (`%LOCALAPPDATA%\hermes\plugins` on Windows, `~/.hermes/plugins` on macOS and Linux):

   ```bash
   hermes plugins install than0112/papercut-plugin --enable
   ```

2. When Hermes asks to install the Python dependencies (Pillow, numpy, jsonschema, scipy,
   scikit-image), accept.
3. **Restart Hermes** (and Hermes Desktop) so the plugin loads.
4. Check that it is listed:

   ```bash
   hermes plugins list
   ```

5. Make sure Hermes has a **vision-capable model** configured. The plugin sends the image
   only to that model, through Hermes.
6. Attach a paper-cut image in Hermes and say, for example:

   > 用 papercraft_analyze 分析這張圖，然後 papercraft_segment 分層、papercraft_build_scene 建場景、papercraft_generate_plan 產生企劃書。

To update later, run the following, then restart Hermes again:

```bash
hermes plugins update papercraft-3d
```

Every tool result carries `pluginVersion`, so you can confirm which version is actually running.
The full test procedure is in [docs/HERMES_TEST_PLAN.md](docs/HERMES_TEST_PLAN.md).

### Option B: Claude Code

In Claude Code, the vision model is Claude itself. No API key is needed.

1. Add the marketplace (this repository) and install the plugin, in a terminal:

   ```bash
   claude plugin marketplace add than0112/papercut-plugin
   ```

   ```bash
   claude plugin install papercraft-3d@papercraft
   ```

   Or inside Claude Code: `/plugin marketplace add than0112/papercut-plugin`, then
   `/plugin install papercraft-3d@papercraft`.
2. Start a new Claude Code session so the plugin loads.
3. **Windows only:** if `python3` does not start Python on your machine, set the Python command
   to `python` or `py`:

   ```
   /plugin configure papercraft-3d@papercraft
   ```

   The same command sets `workspace_dir`, the folder where projects are written. By default,
   projects go to `papercraft-projects/` in the folder where you started Claude Code.
4. Ask, for example: "用 PaperCraft 3D 把 ./my-papercut.png 做成 Three.js 紙雕場景".
5. On first use, Claude reports that the imaging libraries are missing and shows two commands
   that install them into the plugin's own data folder (a virtualenv, not your system Python).
   Allow them. No restart is needed afterwards.

To update later:

```bash
claude plugin marketplace update papercraft
```

```bash
claude plugin update papercraft-3d@papercraft
```

### Preview the scene and build the website

Both hosts finish the build step by returning the exact preview command for your project. In short:

1. Once, in the plugin's `viewer` folder: `npm install`.
2. Start the viewer with `PAPERCRAFT_PROJECT` set to the project folder
   (PowerShell: `$env:PAPERCRAFT_PROJECT="<project_dir>"; npm run dev`,
   bash: `PAPERCRAFT_PROJECT="<project_dir>" npm run dev`).
3. Open `http://localhost:5173/?scene=project/scene.json`. The viewer reloads by itself after
   every edit.
4. After `papercraft_export_project`, build the standalone site in the export folder:

   ```bash
   npm ci
   ```

   ```bash
   npm run build
   ```

   Upload `dist/` to any static host.

### Troubleshooting

| Symptom | Fix |
|---|---|
| Hermes keeps running an old version (`pluginVersion` is old) | `hermes plugins remove papercraft-3d`, then install again and restart Hermes |
| Hermes: `model_unavailable` | Configure a vision-capable model in Hermes |
| Claude Code: the papercraft tools do not appear | Run `/mcp` to see the server's state. On Windows, set the Python command (step 3 of Option B) |
| Claude Code: "needs its imaging libraries" | Let Claude run the two setup commands it shows, then call the tool again |
| A part of the picture is in the wrong layer | Look at `review/overlay.png` and `review/layer_map.png`, paint a mask, and correct it with `manual_masks` (Hermes) or `papercraft_segment_correct` (Claude Code) |

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
| `papercraft_build_scene` | Schema-valid `scene.json`: layer depths, camera and parallax limits derived from the fill margin, calibrated light from the analysis plus day/night presets, glow maps for windows, lanterns and the sun | ✅ phase 4 |
| `papercraft_generate_plan` | `DESIGN_SPEC.md` (sections A–G) rendered from `scene.json` and re-checked against it. Records the scene fingerprint and keeps user notes on regeneration | ✅ phase 5 |
| `papercraft_update_scene` | Validated, atomic Scene Patch from a request in plain language. Protected paths, layers addressable by id, `$preset`, safety-bound rebalancing, history with `revert_to`, `dry_run`, spec and change log updated, live viewer reload | ✅ phase 6 |
| `papercraft_export_project` | Standalone Vite + React + Three.js project (`npm ci && npm run build`), scene and every asset, spec, logs with absolute paths removed. The plugin never runs npm | ✅ phase 7 |

Every tool returns a JSON envelope: `{"ok": true, "data": …, "warnings": […]}` or
`{"ok": false, "error": {"code", "message", "retryable", "details"}}`. Error
codes include `image_format`, `image_too_large`, `model_unavailable`,
`model_output_invalid`, `segmentation_failed`, `scene_invalid`,
`asset_missing`, `viewer_build_failed`, `export_failed`, `path_forbidden`,
`precondition_failed` and `not_implemented`. See `core/errors.py`.

## Claude Code plugin

In Claude Code, two of the steps hand Claude the image and wait for its answer. The answer then
goes through the same validation, repair and second labelling round as in Hermes. The MCP server
(`claude/server.py`) uses only the Python standard library, so it always starts. The imaging
libraries live in a virtualenv in the plugin's data folder. For installation, see
[Option B](#option-b-claude-code).

| Claude Code tool | What it does |
|---|---|
| `papercraft_status` | Setup check and project list |
| `papercraft_analyze_begin` / `_submit` | Returns the image and the schema. Claude submits the analysis, which goes through the same validation and repair round |
| `papercraft_segment_begin` / `_submit` | Returns the numbered regions. Claude submits the labels; uncertain regions come back for a second round. Returns the overlay to review |
| `papercraft_segment_correct` | Manual mask corrections |
| `papercraft_build_scene`, `_generate_plan`, `_update_scene`, `_export_project` | The same as the Hermes tools |

## Hermes details

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

For development only; users do not need this. From the repository root:

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install "Pillow>=10.1,<14" "numpy>=1.26,<3" "jsonschema>=4.18,<5" "scipy>=1.11,<2" "scikit-image>=0.24,<0.28" "pytest>=8,<10" "PyYAML>=6,<7"
```

```bash
.venv/bin/python -m pytest tests
```

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\python`. The suite (170 tests, about
3 minutes) covers the following:

- **Plugin contract.** Manifest and registration parity, mirroring `hermes plugins validate`.
  Handlers never raise and always return JSON strings. The catalog draft and the Claude Code
  manifest match the plugin.
- **Security.** Path confinement, including symlink escapes. Image validation: format spoofing,
  truncation, and size limits. Asset paths in the scene schema: no traversal, no URLs.
- **Each stage, end to end.** A fake vision model replays recorded and scripted replies, and an
  oracle labeller reads the synthetic fixture's ground truth. This covers segmentation accuracy
  and IoU, scene limits recomputed from `scene.json`, the spec agreeing with the scene, Scene Patch
  rules and history, and an export free of absolute paths.
- **Claude Code server.** The MCP handshake, the setup message when libraries are missing, and the
  two-phase begin/submit flow.
- **Real cases.** Analysis replies and region labels recorded from Claude on three real images,
  plus small crops of ten real lamps, lanterns, windows and moons, which pin the glow maps.

The viewer is checked with `cd viewer && npm run build` (TypeScript and Vite).

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

## Preview a project

`papercraft_build_scene` returns the exact command. In the plugin's `viewer`
folder, after a one-time `npm install`:

```powershell
$env:PAPERCRAFT_PROJECT="<project_dir>"; npm run dev
```

```bash
PAPERCRAFT_PROJECT="<project_dir>" npm run dev
```

Then open `http://localhost:5173/?scene=project/scene.json`. The dev server
serves that one folder, read-only, at `/project/`, and serves only image, JSON
and Markdown files inside it.

## How the scene is built

- **Depth.** The analysed `depthEstimate` values are scaled by
  `depth_spacing`, and the image is 8 world units tall.
- **Safety bounds.** Segmentation completed every layer `fill.marginPx` behind
  nearer layers. The camera tilt limit is the largest angle at which two
  overlapping layers shift by at most 60% of that margin. Parallax gets the
  other 40%. Both are rounded down, and `scene.limits` records the resulting
  shifts. A test recomputes them from `scene.json`.
- **Edges.** The viewer clips drawing to the back layer's rectangle, like the
  frame of a shadow box. It also extends every layer past the image edge by
  mirroring, so a moving layer never ends abruptly at the border.
- **Light.** The art already has painted light. Ambient plus key light is set
  to a total irradiance of about 3.0 (π gives the unlit texture colour), with
  55% ambient and 45% directional for real shadows between layers. The key's
  direction and colour come from the analysed azimuth, elevation and colour
  temperature. Night is cool moonlight with a backlight.
- **Glow.** For each analysed emitter, only pixels inside its box that are both
  bright and close to its colour glow. The box itself never glows. The hottest
  part of a light is usually drawn nearly white, so very bright pixels tinted
  towards the emitter's hue also glow when they connect to that colour, and a
  white centre fully enclosed by glow is filled in. Untinted white paper next to
  a lamp stays dark. `emissiveScale` makes lights brighter at night.

## Editing a scene in plain language

The agent turns the request into JSON Patch operations, and `papercraft_update_scene` handles the rest:

- **Validation.** Every path must be on an allow-list: lighting, presets,
  interaction, rendering, camera zoom and tilt, and layer depth, parallax and
  material. Assets, masks, the source image, ids, provenance and derived data
  are protected. The whole patch is checked against the scene schema and the
  semantic rules, and a failure writes nothing.
- **Conveniences.** Layers can be addressed by id (`/layers/red_bridge/depth`).
  `{"$preset": "night"}` copies a preset. Using `replace` on an optional field
  that is not set yet is treated as `add`.
- **Consistency.** If only the key colour changes, its stale colour temperature
  is dropped. Edited lighting is labelled `custom`.
- **Safety bounds.** If the edit would let layers shift past the filled margin,
  the plugin lowers whichever of tilt or parallax the user did not ask for, and
  reports it. If both were requested explicitly, the edit is refused with the
  numbers, unless `respect_limits=false`.
- **History.** Before each change the previous scene is saved as
  `history/scene.rev<N>.json`, and `history/patches.jsonl` records the request,
  the operations, the diff and any adjustments. `revert_to` restores an older
  revision as a new one.
- **Spec and viewer.** `DESIGN_SPEC.md` is regenerated and gains section G
  (change log). An open viewer polls `scene.json` and reloads within about
  2 seconds.

## How segmentation works

1. **Regions on your machine.** Felzenszwalb graph segmentation runs on a
   1024 px working copy and follows the paper cut lines. A Ward-style
   agglomerative merge then joins similar neighbours, cheapest first,
   recomputing region means after every merge. This leaves 40–120 significant
   regions, depending on the number of layers. A transitive threshold merge
   was dropped: on a real image it chained pink sky, blossoms, snow and rock
   into one region covering 50% of the picture.
   On detailed art the merge must be coarse to stay within the number budget,
   so a region can still join clearly different pieces. One real example joined
   a white sail, the pink coast behind it and the sun's gold reflection; its one
   number sat on the coast, so the sail was labelled as coast. Such regions are
   split again by colour group and connectivity before the numbers are drawn.
   The clearest cases go first, with at most 3 parts per region and at most 12
   extra numbers.
2. **Labels from your vision model (Set-of-Mark).** The model gets the original
   image and a copy with each region outlined and numbered, plus the layer plan
   from `analysis.json`. It answers "region 12 → `red_bridge`". The answer is
   validated, with one repair round. Small regions inherit the layer of the
   neighbour they share the longest border with.
   **Second round:** regions the model marks as mixing layers (for example sky
   showing between blossoms) are split into smaller parts along the original
   fine segments. The model gets one more image with only those parts bright
   and numbered (`review/refine_marks.png`).
   **Colour check:** the model does not always notice a mixed region, so the
   plugin also checks every labelled region itself. It splits the region into
   two colour groups. If one group matches an adjacent region of *another* layer
   much better than anything of its own layer, the region joins the second round
   (up to 3 extra regions; `labelling.autoFlagged` lists them).
   **Batches:** the second round is sent in batches of at most 36 numbered parts
   (`review/refine_marks.png`, `refine_marks_2.png`, …), each region's parts
   together, so the numbers stay readable. A failed batch keeps the first-round
   labels for its regions only. Lightness counts
   for half, so a cast shadow (a darker shade of the same paper) is not mistaken
   for another layer. With perfect labels on the synthetic fixture it flags
   nothing; on the real village image it caught the blossom-and-sky region the
   model had missed.
3. **Full-resolution edges.** The map is upsampled and the pixels on layer
   boundaries are reassigned by local colour similarity. Isolated specks are
   removed.
4. **Hidden-area fill.** The farthest layer is completed everywhere. Every
   other layer except the nearest is extended 5% of the image's long side
   behind nearer layers, and the extension is filled by push-pull interpolation
   from the layer's own pixels, skipping the cast-shadow band. Owned pixels are
   never changed. `fill.marginPx` tells the scene builder how far layers may
   shift relative to each other before an unfilled gap could show.
5. **Review and correct.** `review/overlay.png` (colour-coded layers over the art),
   `review/layer_map.png` (the same layers as solid colours, unambiguous) and
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
4. ✅ Three.js scene builder (`scene.json`), frame clipping, edge extension, glow maps. `shape`/`extrude` geometry is still to do
5. ✅ Design spec generator (zh-TW / en, verified against scene.json)
6. ✅ Natural-language Scene Patch (`papercraft_update_scene`), with a live-reloading viewer
7. ✅ Export, test report, demo script, catalog entry draft
8. ✅ Claude Code plugin (Claude as the vision model), tested end to end on a real image

Still to do: a real-Hermes run of every stage, the demo video, and the catalog PR.

## 中文摘要

PaperCraft 3D 能把一張 AI 紙雕圖做成可以互動的 Three.js 2.5D 網頁場景，可以裝在 Hermes Agent 或 Claude Code 裡用。它會產生以下成果：

- 每一層的透明圖檔
- `scene.json` 場景檔
- 企劃書 `DESIGN_SPEC.md`，內容和場景完全一致
- 可以獨立建置的網頁專案

場景做好後，也可以直接用一句話修改，例如「改成夜晚、藍色月光」。任何步驟失敗都會明確回報錯誤，不會假裝成功。

**安裝（擇一）：**

- **Hermes**：執行 `hermes plugins install than0112/papercut-plugin --enable`，同意安裝 Python 套件，然後重新啟動 Hermes。Hermes 需要先設定好支援圖片的模型。
- **Claude Code**：依序執行 `claude plugin marketplace add than0112/papercut-plugin` 和 `claude plugin install papercraft-3d@papercraft`，然後開一個新的對話。第一次使用時，Claude 會請你同意安裝影像套件。Windows 如果無法用 `python3` 啟動 Python，請用 `/plugin configure papercraft-3d@papercraft` 把 Python 指令改成 `python` 或 `py`。

**預覽與建置：**

- 預覽場景需要 Node.js 20.19 以上。先在外掛的 `viewer` 資料夾執行一次 `npm install`，再照建場景步驟回傳的指令啟動預覽。
- 匯出的網頁專案，在匯出資料夾執行 `npm ci` 和 `npm run build` 即可。

## License

MIT
