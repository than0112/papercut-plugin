# Installation, configuration and troubleshooting

The [README](../README.md#quick-start) has the short version. This page has the details for both
hosts, plus Windows, macOS and Linux notes and the development setup.

- [Requirements](#requirements)
- [Hermes Agent](#hermes-agent)
- [Claude Code](#claude-code)
- [Preview a scene and build the website](#preview-a-scene-and-build-the-website)
- [Troubleshooting](#troubleshooting)
- [Development setup](#development-setup)

## Requirements

| What | Version | Needed for |
|---|---|---|
| Python | 3.11 or newer | Both hosts |
| Node.js | 20.19 or newer (includes npm) | Only to preview a scene or build the exported site |
| Hermes Agent | 0.22 or newer | The Hermes plugin |
| Claude Code | a current release | The Claude Code plugin |

Check them with `python --version` (`python3 --version` on macOS and Linux) and `node --version`.

The Python dependencies are Pillow, numpy, jsonschema, scipy and scikit-image, with bounded version
ranges in `pyproject.toml` (and `claude/requirements.txt`) that fit Hermes's own pins.

## Hermes Agent

### Install

```bash
hermes plugins install than0112/papercut-plugin --enable
```

Hermes clones the plugin into its plugins folder:

| OS | Plugins folder |
|---|---|
| Windows | `%LOCALAPPDATA%\hermes\plugins\papercraft-3d` |
| macOS, Linux | `~/.hermes/plugins/papercraft-3d` |

When Hermes asks to install the Python dependencies, accept. Then restart the gateway, so that the
running process loads the plugin together with its dependencies:

```bash
hermes gateway restart
```

Reconnect Hermes Desktop afterwards; if it does not recover by itself, close and reopen the app.

A **vision-capable model** must be configured in Hermes. The plugin sends the image only to that model,
through Hermes (`ctx.llm`).

### Verify

```bash
hermes plugins list
```

```bash
hermes plugins doctor papercraft-3d
```

`doctor` runs the real manifest parser, loader and registration path. It should report
`6 tool(s)` and exit with code 0. Before submitting changes to the catalog, `hermes plugins validate
<path-to-plugin> --install-deps` runs the same check as catalog CI.

```bash
hermes tools --summary
```

This shows whether the `papercraft` toolset is enabled. If you once saved a tool selection with
`hermes tools`, make sure `papercraft` is checked for the platform you use. Hermes Desktop has its own
switch in its toolset panel.

### Finding the tools in a conversation

Hermes puts plugin tools behind **Tool Search** by default. The model does not see
`papercraft_analyze` in its tool list. It finds it with `tool_search` and runs it with `tool_call`. If
the agent says it cannot find the tools, ask it:

> Use tool_search to search for "papercraft" and list the tools you find.

If the search comes back empty, the running gateway has not loaded the plugin. Run
`hermes gateway restart`; see also [Troubleshooting](#troubleshooting).

Every tool result carries `pluginVersion`, which shows the code that is actually running.

### Update

```bash
hermes plugins update papercraft-3d
```

```bash
hermes gateway restart
```

### Settings

Settings go under `plugins.entries.papercraft-3d.settings` in Hermes's `config.yaml`. The Desktop app
also shows them under **Settings → Plugins**.

| Key | Default | Meaning |
|---|---|---|
| `workspace_dir` | plugin data dir | Where projects are written. Generated files never go into the plugin's install tree |
| `segmentation_backend` | `builtin` | `builtin` (regions computed locally, labelled by your vision model) or `http` (your own service, see [SEGMENTATION_HTTP.md](SEGMENTATION_HTTP.md)) |
| `segmentation_url` | — | `https://`, or `http://` on localhost, for the `http` backend |
| `segmentation_timeout_s` | `300` | Timeout for the `http` backend |
| `max_image_mb` | `10` | Upload limit (hard-capped at 10 MB) |
| `analysis_timeout_s` | `180` | Timeout per vision-model call (10–600 s) |

By default, projects go to `<hermes home>/plugin-data/papercraft-3d/projects`. For example, on Windows
that is `%LOCALAPPDATA%\hermes\plugin-data\papercraft-3d\projects`.

The full acceptance procedure is in [HERMES_TEST_PLAN.md](HERMES_TEST_PLAN.md).

## Claude Code

In Claude Code, the vision model is Claude itself. No API key is needed.

### Install

In a terminal:

```bash
claude plugin marketplace add than0112/papercut-plugin
```

```bash
claude plugin install papercraft-3d@papercraft
```

Or inside Claude Code: `/plugin marketplace add than0112/papercut-plugin`, then
`/plugin install papercraft-3d@papercraft`. Start a new session afterwards so that the plugin loads.

### First use

On first use, Claude reports that the imaging libraries are missing and shows two commands that create
a virtualenv in the plugin's data folder and install them there (not into your system Python). Allow
them. No restart is needed afterwards.

### Settings

```
/plugin configure papercraft-3d@papercraft
```

| Option | Default | Meaning |
|---|---|---|
| `python_command` | `python3` | The Python that starts the plugin's MCP server. On Windows, use `python` or `py` if `python3` does not start Python |
| `workspace_dir` | empty | Where projects are written. Empty means `papercraft-projects/` in the folder where you started Claude Code |

### Update

```bash
claude plugin marketplace update papercraft
```

```bash
claude plugin update papercraft-3d@papercraft
```

## Preview a scene and build the website

The build step returns the exact preview command for your project. In short:

1. Once, in the plugin's `viewer` folder, run `npm install`.
2. Start the viewer with `PAPERCRAFT_PROJECT` set to the project folder.

   PowerShell (Windows):

   ```powershell
   $env:PAPERCRAFT_PROJECT="<project_dir>"; npm run dev
   ```

   bash or zsh (macOS, Linux):

   ```bash
   PAPERCRAFT_PROJECT="<project_dir>" npm run dev
   ```

3. Open `http://localhost:5173/?scene=project/scene.json`. The viewer reloads by itself after every
   edit.
4. After `papercraft_export_project`, build the standalone site in the export folder
   (`<project>/export/<name>-web/`):

   ```bash
   npm ci
   ```

   ```bash
   npm run build
   ```

   Upload `dist/` to any static host. The build uses relative paths, so it also works from a
   sub-folder.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Hermes: the agent cannot find `papercraft_analyze` | Ask it to run `tool_search` for "papercraft" (plugin tools sit behind Tool Search). If nothing is found, run `hermes gateway restart` and reconnect Desktop |
| Hermes: the tools are still missing after a restart | Run `hermes plugins doctor papercraft-3d` and `hermes tools --summary`. Check that the `papercraft` toolset is enabled for your platform, including in the Desktop toolset panel. Do not reinstall repeatedly |
| Hermes: an old `pluginVersion` keeps answering | `hermes plugins update papercraft-3d`, then `hermes gateway restart`. If that does not help, run `hermes plugins remove papercraft-3d`, install again and restart |
| Hermes: `No module named 'scipy'` (or another imaging library) in an old log | The gateway loaded the plugin before the dependencies were installed. Restart the gateway |
| Hermes: `model_unavailable` | Configure a vision-capable model in Hermes |
| Hermes: the update hangs at "completing source-update dependencies" | Run `hermes update` in your own terminal without a time limit, and let it finish |
| Claude Code: the papercraft tools do not appear | Run `/mcp` to see the server's state. On Windows, set `python_command` to `python` or `py` |
| Claude Code: "needs its imaging libraries" | Let Claude run the two setup commands it shows, then call the tool again |
| `image_format` or `image_too_large` | Use PNG, JPEG or WebP, at most 10 MB and 40 megapixels |
| A part of the picture is in the wrong layer | Look at `review/layer_map.png`. Paint a mask the size of the source (white = belongs to the layer), then correct it with `manual_masks` (Hermes) or `papercraft_segment_correct` (Claude Code). The model is not called again |
| The warning "regions had to be merged coarsely" | The image is very detailed. Check `review/layer_map.png` carefully and correct with masks where needed |
| The viewer shows a gap while tilting | It should not, within the limits. Report it with the project's `scene.json` |
| `npm` is not found | Install Node.js 20.19 or newer and open a new terminal |

## Development setup

From the repository root:

```bash
python -m venv .venv
```

macOS, Linux:

```bash
.venv/bin/pip install "Pillow>=10.1,<14" "numpy>=1.26,<3" "jsonschema>=4.18,<5" "scipy>=1.11,<2" "scikit-image>=0.24,<0.28" "pytest>=8,<10" "PyYAML>=6,<7"
```

```bash
.venv/bin/python -m pytest tests
```

Windows (PowerShell):

```powershell
.venv\Scripts\pip install "Pillow>=10.1,<14" "numpy>=1.26,<3" "jsonschema>=4.18,<5" "scipy>=1.11,<2" "scikit-image>=0.24,<0.28" "pytest>=8,<10" "PyYAML>=6,<7"
```

```powershell
.venv\Scripts\python -m pytest tests
```

The pytest root is `tests/`, because the repository root is itself the plugin package. Tests use a fake
vision model and an oracle labeller, so they need no network and no API key. The suite takes about
3 minutes; [TEST_REPORT.md](TEST_REPORT.md) lists what each file covers.

The viewer:

```bash
cd viewer
```

```bash
npm install
```

```bash
npm run dev
```

```bash
npm run build
```

`npm run build` type-checks (TypeScript strict) and builds. With no `?scene=` parameter, the viewer
loads the synthetic fixture. Regenerate the fixture with
`python examples/make_synthetic_fixture.py viewer/public/scenes/fixture` (deterministic output).
