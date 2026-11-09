# PaperCraft 3D for Hermes: Hermes Plugin Hackathon 2026 submission

> One image. One Hermes plugin. An interactive 3D web experience.

## Summary

PaperCraft 3D turns an AI-generated **paper-cut / papercraft image** into a
**layered, interactive Three.js 2.5D scene** that you can develop further: a
validated `scene.json`, per-layer transparent PNGs and masks, a design spec
generated from that same `scene.json`, natural-language editing and a standalone
web project export. Hermes does more than describe the picture. It produces code
and assets you own.

## What it does

| Tool | Result |
|---|---|
| `papercraft_analyze` | The user's vision model (via `ctx.llm`) plans 3–8 depth layers, occlusion, palette, light and emitters. The reply is schema-checked and repaired once if needed. Its colours are checked against the pixels. |
| `papercraft_segment` | Regions are computed locally along the paper cut lines and labelled by the vision model (Set-of-Mark, with a second round for mixed regions, which the plugin also detects itself by checking colours against neighbouring layers). The result is full-size transparent layers, masks and hidden-area fill. Restacking the layers reproduces the source exactly. Supports manual mask corrections and an optional HTTP backend. |
| `papercraft_build_scene` | Builds `scene.json`. The camera tilt and parallax limits are derived from the fill margin, so gaps never show. Light is calibrated to the painted art, with day and night presets. Glow maps cover only the bright pixels of lanterns, windows and the sun. |
| `papercraft_generate_plan` | Writes `DESIGN_SPEC.md` (sections A–G). Every number is read from `scene.json` and re-checked against it, and user notes are kept on regeneration. |
| `papercraft_update_scene` | Applies a natural-language edit as a validated, atomic Scene Patch, with protected paths, safety-bound rebalancing, history and undo. The spec and change log are updated and the viewer reloads live. |
| `papercraft_export_project` | Exports a standalone Vite + React + Three.js project (`npm ci && npm run build`). It uses relative paths only, which is checked. |

## Also a Claude Code plugin

The same repository installs as a Claude Code plugin (`.claude-plugin/`, `skills/papercraft`,
`claude/server.py`). There, Claude is the vision model. The MCP server returns the image and
the exact instructions, Claude submits its answer, and the plugin replays that answer through the
same validation, repair and second labelling round. It is one pipeline with two hosts.

## Why it is trustworthy

- **It never fakes success.** A failed stage returns a classified error
  (`model_unavailable`, `segmentation_failed`, `scene_invalid`, …) and writes
  nothing. The degraded flat "preview" is opt-in and labelled everywhere,
  including in a viewer banner.
- **Untrusted model output.** Every model reply is validated against a JSON
  Schema and semantic rules before use. Anything the plugin can measure
  (colours) is cross-checked against the pixels.
- **Single source of truth.** `schemas/scene.schema.json` is shared by the
  plugin, the viewer (Ajv at load time) and the spec generator.
- **Measured, not assumed.** With perfect labels, segmentation reaches
  98.6–98.9% pixel accuracy and every layer's IoU is at least 0.90 at
  480–2400 px. The scene bounds are recomputed from `scene.json` in tests.

## Disclosures (catalog rule 13)

- **Network.**
  - The image goes to the vision model the user already configured in Hermes,
    through `ctx.llm`, so Hermes owns the credentials. A full run makes one
    analysis call (plus one repair if needed), one labelling call (plus one repair if needed), and,
    when regions mix layers, up to four second-round calls of at most 36 numbered parts each.
  - `segmentation_backend: http` contacts only the URL the user sets: https,
    or http on localhost.
  - The plugin makes no other network calls and has no telemetry.
- **Files read.**
  - The image path, and any manual mask paths, that the user passes to the tools.
  - The plugin's own `viewer/` and `schemas/` (copied when exporting).
- **Files written.** Only the workspace: `<hermes home>/plugin-data/papercraft-3d/projects` or the configured `workspace_dir`.
- **No shell commands, subprocesses, background processes or stored credentials.**
  The plugin never runs npm. The user runs the viewer and builds.
- **Dependencies.** Pillow, numpy, jsonschema, scipy and scikit-image, all with
  bounded ranges, installed through Hermes's dependency consent.
- **Viewer dev server.** It binds to 127.0.0.1. With `PAPERCRAFT_PROJECT` it
  serves that one folder read-only, and only image, JSON and Markdown files.

## Install and try

```bash
hermes plugins install than0112/papercut-plugin --enable
```

In Hermes, attach a paper-cut image and say:

> 用 papercraft_analyze 分析這張圖，然後 papercraft_segment 分層、papercraft_build_scene 建場景、papercraft_generate_plan 產生企劃書。

Preview: the `preview` field of `papercraft_build_scene` gives the command. To
edit, say: "把場景改成夜間，使用冷色月光，降低環境亮度". Export with
`papercraft_export_project`.

## 90-second demo script

| Time | Screen | What to show |
|---|---|---|
| 00–15 | The AI-generated paper-cut image | "One image." |
| 15–30 | Hermes chat | The prompt, and the `papercraft_analyze` call returning `pluginVersion`, the layer plan and `palette_check` |
| 30–45 | `review/marks.png` → `review/overlay.png` → the layer PNGs on a checkerboard | Real per-layer cut-outs, not a copy of the picture at several depths |
| 45–65 | Viewer | Drag to tilt (no gaps at the limit), mouse parallax, exploded view, day ↔ night |
| 65–80 | Hermes → viewer side by side | "改成藍色月光，增加背光，讓前景移動更明顯". The viewer reloads by itself to rev 1 |
| 80–90 | `DESIGN_SPEC.md` (section G change log) and the export folder | `npm ci && npm run build` → `dist/` |

## Verification status

- Automated: see [TEST_REPORT.md](TEST_REPORT.md).
- In a real Hermes 0.22.7 install (Windows):
  - With plugin 0.9.1, install, `hermes plugins validate` and `hermes plugins doctor` passed with
    exit code 0, and all six tools registered.
  - `papercraft_analyze` ran against `openai-codex / gpt-6.1-sol` on a real image.
  - `papercraft_segment` ran with the same model and produced a true layered result: seven layer
    PNGs, seven masks and `layers.json`, with the second round in three batches. Its quality did
    not pass review, because the sailboats landed in the wrong layers. The cause is addressed in
    0.9.2, and a painted mask corrects the run.
  - `papercraft_build_scene`, `papercraft_generate_plan`, `papercraft_update_scene` and
    `papercraft_export_project` are still to be run in Hermes. They were exercised end to end on
    the same real image with the model replies supplied by hand, and with the Claude Code host.
- As a Claude Code plugin, with Claude as the vision model:
  - Analysis, segmentation, scene building, the spec and export ran end to end on three real
    images: a lighthouse harbour, a Japanese village and a night forest. On the village, Claude
    also applied a night edit, and it corrected a layer with a mask on its own.
  - The analyses and labels from these runs are recorded as regression tests.

## Known limitations

- The scene is 2.5D: transparent planes, not meshes with back faces. `shape`/`extrude` geometry is planned.
- Painted shadows stay fixed, and hidden areas are filled in rather than recovered.
- Very detailed images can produce regions that mix layers. A warning is raised,
  and `manual_masks` corrects them.
