# Showcase media

The README's showcase shows **real end-to-end results only**. The synthetic test fixture
(`viewer/public/scenes/fixture`) is test data and is never used here.

## Provenance of the current captures

All current captures come from real runs of the **Claude Code host**, with Claude as the vision model. No
capture here comes from a Hermes run yet.

| File | Shot | Source |
|---|---|---|
| `01-original.webp` | The original AI-generated artwork | The lighthouse-harbour papercut (1672 × 941), resized |
| `02-layer-map.webp` | The semantic layer map | The lighthouse segmentation, labelled by Claude with plugin 0.8.0, rendered as solid layer colours. Claude's labels are shown as they are, including a few small mistakes |
| `03-preview.webp` | The interactive Three.js preview, tilted sideways | The lighthouse scene, rebuilt from that segmentation with plugin 0.9.3 (no model call), in the bundled viewer |
| `04-exploded.webp` | The exploded view at 50%, tilted | Same scene |
| `05-day.webp`, `06-night.webp` | Day and night lighting | The scene's own day and night presets, same camera |
| `07-edit-before.webp`, `07-edit-after.webp` | Natural-language editing, revision 0 → 2 | The Japanese-village run. The scene was rebuilt with 0.9.3, then the two patches Claude made for 「改成夜間，使用冷色月光，讓燈籠和窗戶亮起來」 were replayed with `papercraft_update_scene` |
| `08-layers.webp` | The seven transparent layer PNGs on a checkerboard | The lighthouse project's `assets/*.png`, farthest first |

## Still to capture

- The same sequence from a **Hermes** run, once `build_scene` and the later steps have been accepted
  there.
- `07-edit.gif` or `.mp4`: the agent chat beside the viewer, reloading to the new revision live.

## How to capture

Use one real papercut for a whole sequence and record the host and plugin version in the README caption.

- **Viewer captures.** Use `?scene=project/scene.json`, at 2× device pixel ratio, cropped to the frame.
  Tilt sideways rather than vertically: a strong vertical tilt reveals the mirrored edge extension below
  the frame.
- **Format.** Save as WebP at about 1200 px wide, a few hundred KB each.

To start the viewer for a project:

```bash
cd viewer
```

```bash
npm install
```

```bash
PAPERCRAFT_PROJECT="<project_dir>" npm run dev
```
