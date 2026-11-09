---
name: papercraft
description: Turn a paper-cut / papercraft / layered-paper illustration into an interactive Three.js 2.5D web scene with the PaperCraft 3D tools. Use when the user gives an image (file path) and asks for a 3D, 2.5D, parallax, layered or Three.js scene, wants to split an illustration into depth layers, edit such a scene in words, or export it as a web project.
---

# PaperCraft 3D

The `papercraft` MCP server turns one image into a project folder: real per-layer cut-outs,
`scene.json`, `DESIGN_SPEC.md` and an exportable Vite + Three.js site. **You are the vision
model.** Two steps hand you an image and wait for your answer.

## Workflow

1. **Check the setup.** Call `papercraft_status`. If it reports missing libraries, show the user
   the two setup commands it returns. Run them with Bash once the user agrees, then continue
   without restarting.
2. **Analyse.** Call `papercraft_analyze_begin` with `image_path`, the user's wishes as `brief`
   (in their language) and `max_layers` (default 6, up to 8 for busy scenes). Read the returned
   instructions and the schema, look at the image, and submit one JSON object with
   `papercraft_analyze_submit`. If the result lists problems, fix exactly those and resubmit.
   Show the user the `needs_review` items.
3. **Segment.** Call `papercraft_segment_begin`. You get the original image and a numbered-region
   image. Submit with `papercraft_segment_submit`:
   - `assignments` must map **every** number to a layer id, for example `{"1": "sky", "2": "mountains", ...}`.
   - List each region that mixes layers in `uncertain`, for example sky showing between
     foreground blossoms. Those regions come back split into smaller parts. Label the parts and
     submit again.
   - Small numbers are easy to miss, so check the numbers along edges and in corners.
   - A soft dark band beside a paper edge is a cast shadow. It belongs to the paper it falls on.

   The final result includes an overlay with one colour per layer. Look at it. If something is
   in the wrong layer, tell the user, and offer `papercraft_segment_correct` with a mask that you
   or the user paint.
4. **Build.** Call `papercraft_build_scene`. Its result explains the camera and parallax limits
   and the glowing emitters.
5. **Spec.** Call `papercraft_generate_plan` (`language` `zh-TW` or `en`) to write `DESIGN_SPEC.md`.
6. **Preview.** Use the `preview` block of the build result. Run `npm install` once in the viewer
   folder, then start the dev server **in the background** with `PAPERCRAFT_PROJECT` set, and
   give the user the URL. The viewer reloads by itself after every edit.
7. **Edit in words.** Turn the request into JSON Patch operations for `papercraft_update_scene`,
   and always pass the user's words as `instruction`. Useful paths:
   - `/lighting` with `{"$preset": "night"}` switches to the night preset.
   - `/lighting/key/color`, `/lighting/key/intensity`, `/lighting/ambient/intensity`.
   - `/lighting/rim` takes `{color, intensity, position}` and adds a backlight.
   - `/interaction/parallax/strength`.
   - `/layers/<layer id>/depth`, `/layers/<layer id>/material/tint`.

   Use `dry_run` to show the diff first when the request is vague. Report the `adjustments` the
   tool made, for example a lower tilt limit that keeps gaps hidden.
8. **Export.** Call `papercraft_export_project`. Tell the user the folder, the zip file and the
   commands `npm ci && npm run build`.

## Rules

- Never claim a stage succeeded unless its tool returned `"ok": true`.
- If segmentation keeps failing, say so. Use the flat "preview" only if the user accepts it, and
  call it a preview, not a 3D reconstruction.
- Describe only what is visible. Lower your confidences when the image is ambiguous, and put
  doubts in `needsReview`.
- Assets, masks and the source image are never edited by `papercraft_update_scene`. To change
  layers, segment again or correct with masks.
