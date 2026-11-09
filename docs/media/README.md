# Showcase media: shot list

The README's showcase shows **real end-to-end results only**. The synthetic test fixture
(`viewer/public/scenes/fixture`) is test data and must not be used as a showcase image. This folder holds
the captures. Until a file exists, the README shows a placeholder.

Use one real paper-cut image for the whole sequence, run through the plugin in Hermes (preferred) or
Claude Code, and say which host and plugin version produced it in the README caption.

| File | Shot | How to capture |
|---|---|---|
| `01-original.webp` | The original AI-generated artwork | The project's `source/` image, resized to about 1200 px wide |
| `02-layer-map.png` | The semantic layer map | The project's `review/layer_map.png` (solid colours with the legend) |
| `03-preview.png` | The interactive Three.js preview, slightly tilted | The viewer at `?scene=project/scene.json`, dragged towards the tilt limit |
| `04-exploded.png` | The exploded view | The viewer's exploded-view control |
| `05-day.png`, `06-night.png` | Day and night lighting | The day and night presets in the viewer, same camera |
| `07-edit.gif` (or `.mp4`) | Natural-language editing | The agent chat beside the viewer: a request such as "make it night with cold moonlight", then the viewer reloading to the new revision |
| `08-layers.png` | The transparent layer PNGs on a checkerboard | The project's `assets/*.png`, in order, as a contact sheet |

Captures for a person to look at: take them at 2× device pixel ratio, then save them as WebP or PNG
under about 1 MB each.

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
