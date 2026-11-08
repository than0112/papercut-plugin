# Test report: PaperCraft 3D v0.7.0

The automated results below were produced in the development container
(Python 3.13, Node 22) on 2026-11-08. Section 3 lists what has and has not been
run in a real Hermes install. Fill in the remaining rows with
[HERMES_TEST_PLAN.md](HERMES_TEST_PLAN.md).

## 1. Automated tests: 144 passed, 0 failed

```bash
.venv/bin/python -m pytest tests
```

| File | Tests | What it covers |
|---|---|---|
| `test_plugin.py` | 19 | Manifest fields, `provides_tools` equal to the registrations (as `hermes plugins validate` checks), handlers never raise, results are JSON, the catalog draft matches the manifest |
| `test_security.py` | 25 | Path confinement including symlink escapes, id rules, image validation (format spoofing, truncation, size and pixel limits), read-only source copy |
| `test_scene.py` | 18 | The scene, analysis and layers JSON Schemas; path-traversal and URL assets rejected; semantic rules |
| `test_analyze.py` | 19 | The `ctx.llm` contract (json_mode, no provider/model override), repair round, honest failure, retry by `project_id`, pixel palette check, emitters, the recorded **real model reply** |
| `test_segment.py` | 20 | Oracle-labelled pipeline accuracy (pixel accuracy above 96%, every layer IoU above 0.88 at 480×320), **restacking the layers reproduces the source exactly**, fill, the second labelling round (blossom IoU below 0.6 without it, above 0.9 with it), manual masks, preview fallback, HTTP backend |
| `test_build_scene.py` | 10 | **Tilt and parallax shifts recomputed from scene.json stay within the fill margin**, light direction, glow only inside the emitter box, history, perspective, preview scenes |
| `test_generate_plan.py` | 7 | The spec's tables parsed back and compared with scene.json, fingerprint and staleness, user notes kept, tampering detected, zh-TW and en |
| `test_update_scene.py` | 22 | The test-plan request "night, cold moonlight, lower ambient", asset hashes unchanged, protected paths refused with nothing written, atomicity, rebalancing, revert, dry run |
| `test_export.py` | 4 | A standalone file set, no absolute paths (scanner tested), zip equal to the folder, re-export cleanup |

Viewer: `npm run build` (TypeScript strict plus Vite) passes.

## 2. End-to-end run on a real image (model replies supplied by hand)

The user's 1672×941 paper-cut image was run through all six tools in this
container. The vision-model replies were stand-ins: the analysis was rebuilt
from the user's real analysis-v1 output, and the region labels were assigned by
the developer. Every other step was the plugin's real code.

| Check | Result |
|---|---|
| Segmentation | 120 numbered regions; three uncertain regions were split into 39 parts in round two. Stacking the layers reproduces the source with a maximum difference of 0 |
| Scene | Tilt shift 36.2 px and parallax shift 29.6 px, within the 84 px fill margin. Glow covers 20–24% of the lantern and window boxes |
| Viewer, day view | Colours match the source image (light total calibrated to about π) |
| Viewer, tilt and parallax at their limits | No gaps and no black edges (frame clipping plus mirrored edge extension) |
| Edit: "藍色月光、背光、前景移動更明顯" | rev 0 to rev 1. The viewer reloaded without a page refresh. The spec was updated and section G was added |
| Export, clean folder | `npm ci` added 90 packages; `npm run build` succeeded. `dist/` served from the sub-folder `/gallery/kitsune/` by a plain static server rendered correctly, with no console errors and no polling in production |

## 3. Real Hermes (0.22.7, Windows), run by the user

| Hermes test plan item | Status |
|---|---|
| 1. Install, `hermes plugins validate` | ✅ passed. The user reported "Validation passed" and exit code 0 |
| 2. `papercraft_analyze` with a real vision model | ✅ ran twice on `openai-codex / gpt-6.1-sol`, accepted on the first attempt (plugin v0.2, prompt v1) |
| 2b. `papercraft_segment` | ⬜ to do with v0.7.0 |
| 3. `papercraft_build_scene` and the viewer | ⬜ to do |
| 4. `papercraft_generate_plan` | ⬜ to do |
| 5. `papercraft_update_scene` | ⬜ to do |
| 6. `papercraft_export_project` on a clean machine | ⬜ to do |
| Acceptance: three different test images | ⬜ one image so far; two more needed |
