# PaperCraft 3D for Hermes — working notes

Hermes Agent plugin (NousResearch/hermes-agent). The repo root **is** the plugin:
`plugin.yaml` + `__init__.py` with `register(ctx)`.

## Verified Hermes plugin API (from hermes-agent source/docs, Nov 2026)

Do not invent other APIs; check `website/docs/developer-guide/plugins/index.md` first.

- `ctx.register_tool(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", override=False)`
- Handlers have the signature `handler(args: dict, **kwargs) -> str`. They return a **JSON string** and never raise.
- `ctx.llm.complete_structured(instructions=, input=[{"type":"image","data":bytes,"mime_type":...}], json_schema=, purpose=, timeout=)`
  returns `.parsed`, `.content_type` ("json"|"text"), `.text`, `.provider`, `.model`, `.usage`. The host owns credentials and vision routing.
  The plugin must not pass `provider=` or `model=` (the trust gate denies them).
- Use `ctx.get_config(key, default)` for settings under `plugins.entries.papercraft-3d.settings`, declared in `config_schema`.
- `from plugins.plugin_storage import plugin_data_dir` gives the per-plugin data dir. Never write into the plugin tree.
- `provides_tools` must exactly match the registered tools, because `hermes plugins validate` fails otherwise.
- Catalog rules: no subprocess or shell, no self-update, no reading credential files, and Python deps need upper bounds.

## Conventions

- Every tool lives in `tools/<x>.py` and exports `NAME`, `SCHEMA`, and `handle(args, runtime, **kw)`, wrapped by `core.errors.tool_handler`.
- An unimplemented stage raises `not_implemented(...)`. Never return `ok: true` for work that was not done.
- `schemas/scene.schema.json` is the contract. Update `viewer/src/types/scene.ts` together with it.
- The pytest rootdir is `tests/`, because the repo root is a package with relative imports. Run `.venv/bin/python -m pytest tests`.
- Viewer commands: `cd viewer && npm run build` (runs tsc and vite).
- Segmentation runs Felzenszwalb, then a RAG merge, then Set-of-Mark labelling via ctx.llm, then refinement, then push-pull fill (`core/segmentation.py`, `core/segment_backends.py`).
  The tests use an oracle labeller that reads the fixture's ground truth, so they measure the pipeline and not the model.
- The fixture's shadow and highlight sizes scale with the image width. Keep them that way, or accuracy tests at small sizes become unrealistic.
- Regenerate the fixture with `python3 examples/make_synthetic_fixture.py viewer/public/scenes/fixture`. The output is deterministic.
