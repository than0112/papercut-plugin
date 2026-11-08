# DESIGN_SPEC.md structure

`papercraft_generate_plan` writes `DESIGN_SPEC.md` with `core/spec.py`. Every number comes from
`scene.json`, `analysis.json` or `layers.json`. This file only documents the structure; it is not
a template with placeholders.

```
# <scene name> — 設計與實作企劃書 / Design & Implementation Spec
<!-- papercraft-spec: scene-sha256=<sha256 of scene.json> revision=<n> -->   ← staleness check
- project, scene revision, schema, plugin version, mode, generated at, source image

<!-- auto:A --> A. Visual concept: subject, style, keywords, palette + pixel check,
                measured palette, visual hierarchy, needs-review items        <!-- /auto:A -->
<!-- auto:B --> B. Scene graph, layer table (order, depth, parallax, geometry, scale, coverage,
                inpainted, provenance), coordinates, camera, safety bounds    <!-- /auto:B -->
<!-- auto:C --> C. Lighting: active preset, every preset (ambient, key, rim, shadow, background,
                glow scale), analysed light, emitters and their glow maps      <!-- /auto:C -->
<!-- auto:D --> D. Materials per layer, rendering settings, transparency strategy, performance <!-- /auto:D -->
<!-- auto:E --> E. Interaction: orbit, zoom, parallax, exploded view, presets, responsive     <!-- /auto:E -->
<!-- auto:F --> F. Packages with real versions, project files, asset loading, commands,
                deployment, known limitations + tool warnings, next steps       <!-- /auto:F -->

## 備註 / Notes   ← anything outside the auto blocks is kept when the spec is regenerated
```

Before the tool succeeds, `spec.verify_spec` re-reads the layer table, camera tilt and key lights
from the Markdown and compares them with scene.json.
